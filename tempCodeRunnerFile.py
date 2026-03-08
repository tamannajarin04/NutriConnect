import os
from dotenv import load_dotenv

load_dotenv()

from app import create_app
from app.models import db, User, Role, DietaryPreference

app = create_app(os.getenv("FLASK_ENV") or "default")

@app.shell_context_processor
def make_shell_context():
    return {
        "db": db,
        "User": User,
        "Role": Role,
        "DietaryPreference": DietaryPreference
    }

if __name__ == "__main__":
    app.run(debug=True)
