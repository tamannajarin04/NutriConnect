import os
from datetime import timedelta

BASEDIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-secret-key-change-in-production"

    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or \
        "postgresql+psycopg://nutrition_user:nutrition_pass@localhost/nutrition_db"

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)

    UPLOAD_FOLDER = os.path.join(BASEDIR, "app", "static", "uploads")
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5MB

    OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434"
    OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL") or "qwen3:4b"

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False

config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig
}