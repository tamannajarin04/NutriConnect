from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

user_roles = db.Table(
    "user_roles",
    db.Column("user_id", db.Integer, db.ForeignKey("users.id"), primary_key=True),
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id"), primary_key=True),
)

class Role(db.Model):
    __tablename__ = "roles"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    first_name = db.Column(db.String(50))
    last_name = db.Column(db.String(50))
    profile_picture = db.Column(db.String(200))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    roles = db.relationship("Role", secondary=user_roles, backref=db.backref("users", lazy="dynamic"))
    dietary_preference = db.relationship(
        "DietaryPreference",
        backref="user",
        uselist=False,
        cascade="all, delete-orphan"
    )
    upgrade_requests = db.relationship(
    "RoleUpgradeRequest",
    back_populates="user",
    cascade="all, delete-orphan"
    )

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def has_role(self, role_name: str) -> bool:
        return any(r.name == role_name for r in self.roles)

    def is_user(self) -> bool:
        return self.has_role("user")

    def is_admin(self) -> bool:
        return self.has_role("admin")

    def is_food_provider(self) -> bool:
        return self.has_role("food_provider")

class DietaryPreference(db.Model):
    __tablename__ = "dietary_preferences"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)

    diet_type = db.Column(db.String(50))
    # JSON works in PostgreSQL
    food_restrictions = db.Column(db.JSON, default=list)
    allergies = db.Column(db.JSON, default=list)
    preferred_cuisine = db.Column(db.JSON, default=list)
    avoid_foods = db.Column(db.JSON, default=list)
    favorite_foods = db.Column(db.JSON, default=list)

    meals_per_day = db.Column(db.Integer, default=3)

    calorie_goal = db.Column(db.Integer)
    protein_goal = db.Column(db.Float)
    carbs_goal = db.Column(db.Float)
    fat_goal = db.Column(db.Float)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class RoleUpgradeRequest(db.Model):
    __tablename__ = "role_upgrade_requests"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # requested_role: "food_provider" or "admin"
    requested_role = db.Column(db.String(50), nullable=False)

    # pending / approved / rejected
    status = db.Column(db.String(20), default="pending", nullable=False)

    # user message
    note = db.Column(db.Text)

    # admin message
    admin_comment = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = db.relationship("User", back_populates="upgrade_requests")

    def __repr__(self):
        return f"<RoleUpgradeRequest user_id={self.user_id} role={self.requested_role} status={self.status}>"