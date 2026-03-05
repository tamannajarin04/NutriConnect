import os
from dotenv import load_dotenv

load_dotenv()

from app import create_app
from app.models import db, User, Role, DietaryPreference, BMIRecord  

app = create_app(os.getenv("FLASK_ENV") or "default")

@app.shell_context_processor
def make_shell_context():
    return {
        "db": db,
        "User": User,
        "Role": Role,
        "DietaryPreference": DietaryPreference,
        "BMIRecord": BMIRecord  
    }

if __name__ == "__main__":
    app.run(debug=True)
