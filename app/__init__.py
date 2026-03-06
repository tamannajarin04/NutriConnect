# app/__init__.py

from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate
from config import config
from app.models import db, User
import os

login_manager = LoginManager()
migrate = Migrate()


def create_app(config_name="default"):
    app = Flask(__name__)
    app.config.from_object(config.get(config_name, config["default"]))

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access this page."

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Import blueprints
    from .routes.main import main_bp
    from .routes.auth import auth_bp
    from .routes.user_dashboard import user_dashboard_bp
    from .routes.bmi import bmi_bp                          

    # Register blueprints
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(user_dashboard_bp, url_prefix="/dashboard")
    app.register_blueprint(bmi_bp, url_prefix="/dashboard")

    with app.app_context():
        create_roles_if_ready()
        seed_admins_if_ready()

    return app


def create_roles_if_ready():
    from sqlalchemy import inspect
    from app.models import Role

    inspector = inspect(db.engine)

    if "roles" not in inspector.get_table_names():
        return

    roles_data = [
        {"name": "user", "description": "Regular user"},
        {"name": "food_provider", "description": "Food Provider"},
        {"name": "admin", "description": "Administrator"},
    ]

    for r in roles_data:
        if not Role.query.filter_by(name=r["name"]).first():
            db.session.add(Role(**r))
def seed_admins_if_ready():
    """
    Ensures at least one admin exists based on .env variables.
    This prevents losing admin access forever.
    """
    from sqlalchemy import inspect
    from app.models import Role, User, db

    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    if not all(t in tables for t in ["users", "roles", "user_roles"]):
        return

    if os.environ.get("SEED_ADMINS", "0") != "1":
        return

    email = (os.environ.get("ADMIN1_EMAIL") or "").strip().lower()
    username = (os.environ.get("ADMIN1_USERNAME") or "Admin").strip()
    password = os.environ.get("ADMIN1_PASSWORD") or "Admin@12345"

    if not email:
        return

    admin_role = Role.query.filter_by(name="admin").first()
    if not admin_role:
        admin_role = Role(name="admin", description="Administrator")
        db.session.add(admin_role)
        db.session.flush()

    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(username=username, email=email, first_name=username, last_name="")
        user.set_password(password)
        user.roles = [admin_role]
        db.session.add(user)
        db.session.commit()
        return

    # If user exists but is not admin, force admin (preserve admin access)
    if not user.has_role("admin"):
        user.roles = [admin_role]
        db.session.commit()
    db.session.commit()