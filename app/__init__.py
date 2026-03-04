from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate
from config import config
from app.models import db, User

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

    from .routes.main import main_bp
    from .routes.auth import auth_bp
    from .routes.user_dashboard import user_dashboard_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(user_dashboard_bp, url_prefix="/dashboard")

    # SAFE: only run after tables exist
    with app.app_context():
        create_roles_if_ready()

    return app


def create_roles_if_ready():
    """
    Creates default roles ONLY if the roles table exists.
    Prevents migration crashes.
    """
    from sqlalchemy import inspect
    from app.models import Role

    inspector = inspect(db.engine)

    if "roles" not in inspector.get_table_names():
        return  # table not created yet → skip safely

    roles_data = [
        {"name": "user", "description": "Regular user"},
        {"name": "food_provider", "description": "Food Provider"},
        {"name": "admin", "description": "Administrator"},
    ]

    for r in roles_data:
        if not Role.query.filter_by(name=r["name"]).first():
            db.session.add(Role(**r))

    db.session.commit()

