from flask import Blueprint, redirect, url_for, render_template
from flask_login import current_user

main_bp = Blueprint("main", __name__)

@main_bp.route("/")
def home():
    if current_user.is_authenticated:
        if current_user.is_admin():
            return redirect(url_for("admin.dashboard"))
        if current_user.is_food_provider():
            return redirect(url_for("provider.provider_dashboard"))
        return redirect(url_for("user_dashboard.index"))
    return redirect(url_for("auth.login"))

@main_bp.route("/privacy")
def privacy():
    return render_template("privacy.html")

@main_bp.route("/terms")
def terms():
    return render_template("terms.html")