from flask import Blueprint, redirect, url_for
from flask_login import current_user

main_bp = Blueprint("main", __name__)

@main_bp.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("user_dashboard.index"))
    return redirect(url_for("auth.login"))
