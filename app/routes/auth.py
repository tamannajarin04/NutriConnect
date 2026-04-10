import re
import uuid

from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, current_app)
from flask_login import login_user, logout_user, login_required, current_user
from flask_mail import Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

from app.models import db, User, Role
from app import oauth, mail

auth_bp = Blueprint("auth", __name__)


# ────────────────────────────────────────────────────────────────────────────
#  Helpers
# ────────────────────────────────────────────────────────────────────────────

def _get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


def _ensure_user_role(user: User) -> None:
    """Assign the 'user' role if the account has no roles yet."""
    if not user.roles:
        role = Role.query.filter_by(name="user").first()
        if not role:
            role = Role(name="user", description="Regular user")
            db.session.add(role)
            db.session.flush()
        user.roles = [role]


def _make_unique_username(base: str) -> str:
    """Derive a username that doesn't collide with existing ones."""
    username = re.sub(r"[^a-zA-Z0-9_]", "", base) or "user"
    if not User.query.filter_by(username=username).first():
        return username
    for _ in range(10):
        candidate = f"{username}_{uuid.uuid4().hex[:5]}"
        if not User.query.filter_by(username=candidate).first():
            return candidate
    return f"user_{uuid.uuid4().hex[:8]}"


# ────────────────────────────────────────────────────────────────────────────
#  Register
# ────────────────────────────────────────────────────────────────────────────

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("user_dashboard.index"))

    if request.method == "POST":
        first_name = (request.form.get("first_name") or "").strip()
        last_name  = (request.form.get("last_name")  or "").strip()
        username   = (request.form.get("username")   or "").strip()
        email      = (request.form.get("email")      or "").strip().lower()
        password   = request.form.get("password") or ""

        if not all([first_name, last_name, username, email, password]):
            flash("All fields are required.", "danger")
            return redirect(url_for("auth.register"))

        if User.query.filter_by(username=username).first():
            flash("Username already exists.", "danger")
            return redirect(url_for("auth.register"))

        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "danger")
            return redirect(url_for("auth.register"))

        user = User(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
        )
        user.set_password(password)
        _ensure_user_role(user)

        db.session.add(user)
        db.session.commit()

        flash("Registration successful! Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html")


# ────────────────────────────────────────────────────────────────────────────
#  Login
# ────────────────────────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("user_dashboard.index"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        remember = bool(request.form.get("remember"))

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user, remember=remember)
            flash(f"Welcome back, {user.first_name or user.username}!", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("user_dashboard.index"))

        flash("Invalid username or password.", "danger")

    return render_template("auth/login.html")


# ────────────────────────────────────────────────────────────────────────────
#  Logout
# ────────────────────────────────────────────────────────────────────────────

@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


# ────────────────────────────────────────────────────────────────────────────
#  Google OAuth  –  Step 1: redirect
# ────────────────────────────────────────────────────────────────────────────

@auth_bp.route("/google")
def google_login():
    if current_user.is_authenticated:
        return redirect(url_for("user_dashboard.index"))
    redirect_uri = url_for("auth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


# ────────────────────────────────────────────────────────────────────────────
#  Google OAuth  –  Step 2: callback
# ────────────────────────────────────────────────────────────────────────────

@auth_bp.route("/google/callback")
def google_callback():
    try:
        token = oauth.google.authorize_access_token()
    except Exception:
        flash("Google sign-in was cancelled or failed. Please try again.", "danger")
        return redirect(url_for("auth.login"))

    user_info = token.get("userinfo") or oauth.google.userinfo()
    google_email = (user_info.get("email") or "").strip().lower()
    first_name   = user_info.get("given_name")  or ""
    last_name    = user_info.get("family_name") or ""
    picture      = user_info.get("picture")     or ""

    if not google_email:
        flash("Could not retrieve your email from Google.", "danger")
        return redirect(url_for("auth.login"))

    user = User.query.filter_by(email=google_email).first()

    if user is None:
        username = _make_unique_username(
            (user_info.get("name") or google_email.split("@")[0])
        )
        user = User(
            username=username,
            email=google_email,
            first_name=first_name,
            last_name=last_name,
            profile_picture=picture,
            is_verified=True,
            password_hash="",
        )
        _ensure_user_role(user)
        db.session.add(user)
        db.session.commit()
        flash("Account created via Google — welcome!", "success")
    else:
        if not user.first_name and first_name:
            user.first_name = first_name
        if not user.last_name and last_name:
            user.last_name = last_name
        if picture and not user.profile_picture:
            user.profile_picture = picture
        db.session.commit()

    login_user(user, remember=True)
    flash(f"Welcome, {user.first_name or user.username}!", "success")
    next_page = request.args.get("next")
    return redirect(next_page or url_for("user_dashboard.index"))


# ────────────────────────────────────────────────────────────────────────────
#  Forgot Password  –  request form
# ────────────────────────────────────────────────────────────────────────────

@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("user_dashboard.index"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        user = User.query.filter_by(email=email).first()

        if user:
            _send_reset_email(user)

        flash(
            "If an account with that email exists, a reset link has been sent.",
            "info",
        )
        return redirect(url_for("auth.forgot_password"))

    return render_template("auth/forgot_password.html")


def _send_reset_email(user: User) -> None:
    s     = _get_serializer()
    token = s.dumps(user.email, salt="password-reset-salt")
    reset_url = url_for("auth.reset_password", token=token, _external=True)

    msg = Message(
        subject="NutriConnect – Password Reset Request",
        sender=current_app.config.get("MAIL_DEFAULT_SENDER", "noreply@nutriconnect.com"),
        recipients=[user.email],
    )
    msg.html = f"""
    <p>Hello {user.first_name or user.username},</p>
    <p>We received a request to reset your NutriConnect password.
       Click the button below — this link expires in <strong>1 hour</strong>.</p>
    <p>
      <a href="{reset_url}"
         style="background:#198754;color:#fff;padding:10px 20px;
                border-radius:6px;text-decoration:none;display:inline-block;">
        Reset My Password
      </a>
    </p>
    <p>If you did not request this, you can safely ignore this email.</p>
    <hr>
    <small>NutriConnect &mdash; {reset_url}</small>
    """
    try:
        mail.send(msg)
    except Exception as exc:
        current_app.logger.error("Failed to send reset email to %s: %s", user.email, exc)


# ────────────────────────────────────────────────────────────────────────────
#  Reset Password  –  consume token + set new password
# ────────────────────────────────────────────────────────────────────────────

@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    if current_user.is_authenticated:
        return redirect(url_for("user_dashboard.index"))

    s = _get_serializer()
    try:
        email = s.loads(token, salt="password-reset-salt", max_age=3600)
    except SignatureExpired:
        flash("This reset link has expired. Please request a new one.", "danger")
        return redirect(url_for("auth.forgot_password"))
    except BadSignature:
        flash("Invalid or tampered reset link.", "danger")
        return redirect(url_for("auth.forgot_password"))

    user = User.query.filter_by(email=email).first()
    if not user:
        flash("Account not found.", "danger")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password  = request.form.get("password")  or ""
        password2 = request.form.get("password2") or ""

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "danger")
            return render_template("auth/reset_password.html", token=token)

        if password != password2:
            flash("Passwords do not match.", "danger")
            return render_template("auth/reset_password.html", token=token)

        user.set_password(password)
        db.session.commit()
        flash("Password updated successfully. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", token=token)