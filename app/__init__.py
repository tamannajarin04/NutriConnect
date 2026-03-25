import os
from datetime import datetime

from flask import Flask
from flask_login import LoginManager, current_user
from flask_migrate import Migrate

from config import config
from app.models import db, User

login_manager = LoginManager()
migrate = Migrate()

def create_app(config_name="default"):
    app = Flask(__name__)
    app.config.from_object(config.get(config_name, config["default"]))
    app.config["PROPAGATE_EXCEPTIONS"] = True
    app.config["TRAP_HTTP_EXCEPTIONS"] = True

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access this page."

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    @app.before_request
    def update_last_seen():
        if current_user.is_authenticated:
            current_user.last_seen = datetime.utcnow()
            db.session.commit()

    from .routes.main import main_bp
    from .routes.auth import auth_bp
    from .routes.user_dashboard import user_dashboard_bp
    from .routes.bmi import bmi_bp
    from .routes.admin import admin_bp
    from .routes.food import food_bp, food_search_bp
    from .routes.meal_log import meal_log_bp
    from .routes.orders import orders_bp
    from .routes.provider_dashboard import provider_bp
    from .routes.analytics import analytics_bp
    from .routes.payment import payment_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(user_dashboard_bp, url_prefix="/dashboard")
    app.register_blueprint(bmi_bp, url_prefix="/dashboard")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    app.register_blueprint(food_bp, url_prefix="/provider")
    app.register_blueprint(food_search_bp, url_prefix="/food")

    app.register_blueprint(meal_log_bp, url_prefix="/dashboard/meal-log")

    app.register_blueprint(orders_bp)
    app.register_blueprint(provider_bp, url_prefix="/provider")
    app.register_blueprint(analytics_bp, url_prefix="/admin")
    app.register_blueprint(payment_bp, url_prefix="/dashboard")

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
        {"name": "user",          "description": "Regular user"},
        {"name": "food_provider", "description": "Food Provider"},
        {"name": "admin",         "description": "Administrator"},
    ]
    changed = False
    for r in roles_data:
        if not Role.query.filter_by(name=r["name"]).first():
            db.session.add(Role(**r))
            changed = True
    if changed:
        db.session.commit()


def seed_admins_if_ready():
    from sqlalchemy import inspect
    from app.models import Role, User
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    if not all(t in tables for t in ["users", "roles", "user_roles"]):
        return
    if os.environ.get("SEED_ADMINS", "0") != "1":
        return

    email    = (os.environ.get("ADMIN1_EMAIL") or "").strip().lower()
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
        user = User(
            username=username,
            email=email,
            first_name=username,
            last_name=""
        )
        user.set_password(password)
        user.roles = [admin_role]
        db.session.add(user)
        db.session.commit()
        return

    if not user.has_role("admin"):
        user.roles = [admin_role]
        db.session.commit()