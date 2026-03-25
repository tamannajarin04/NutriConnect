from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
from sqlalchemy import func
import uuid

db = SQLAlchemy()


user_roles = db.Table(
    "user_roles",
    db.metadata,
    db.Column("user_id", db.Integer, db.ForeignKey("users.id"), primary_key=True),
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id"), primary_key=True),
)


class Role(db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Role {self.name}>"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    first_name = db.Column(db.String(50))
    last_name = db.Column(db.String(50))
    profile_picture = db.Column(db.String(200))

    is_verified = db.Column(db.Boolean, default=False, nullable=False)
    account_status = db.Column(db.String(20), default="active", nullable=False)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    roles = db.relationship("Role", secondary=user_roles, backref=db.backref("users", lazy="dynamic"))

    dietary_preference = db.relationship("DietaryPreference", backref="user", uselist=False, cascade="all, delete-orphan")

    upgrade_requests = db.relationship("RoleUpgradeRequest", back_populates="user", cascade="all, delete-orphan")

    bmi_records = db.relationship("BMIRecord", backref="user", lazy="dynamic", cascade="all, delete-orphan")

    foods = db.relationship("FoodItem", backref="provider", lazy=True, cascade="all, delete-orphan")

    meal_logs = db.relationship(
        "MealLog",
        backref="user",
        lazy="dynamic",
        cascade="all, delete-orphan"
    )

    cart_items = db.relationship(
        "CartItem",
        backref="user",
        lazy="dynamic",
        cascade="all, delete-orphan"
    )

    orders = db.relationship("Order", foreign_keys="Order.user_id", backref="customer", lazy="dynamic")

    provider_orders = db.relationship("Order", foreign_keys="Order.provider_id", backref="provider_user", lazy="dynamic")

    favorite_foods = db.relationship("FavoriteFood", backref="user", lazy="dynamic", cascade="all, delete-orphan")

    recent_views = db.relationship("RecentlyViewed", backref="user", lazy="dynamic", cascade="all, delete-orphan")

    ratings = db.relationship("FoodRating", backref="user", lazy="dynamic", cascade="all, delete-orphan")

    notifications = db.relationship("Notification", backref="user", lazy="dynamic", cascade="all, delete-orphan")

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def has_role(self, role_name: str) -> bool:
        return any(role.name == role_name for role in self.roles)

    def is_user(self) -> bool:
        return self.has_role("user")

    def is_admin(self) -> bool:
        return self.has_role("admin")

    def is_food_provider(self) -> bool:
        return self.has_role("food_provider")

    @property
    def full_name(self):
        return f"{self.first_name or ''} {self.last_name or ''}".strip() or self.username

    @property
    def unread_notifications_count(self):
        return self.notifications.filter_by(is_read=False).count()


class DietaryPreference(db.Model):
    __tablename__ = "dietary_preferences"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)

    diet_type = db.Column(db.String(50))
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


class BMIRecord(db.Model):
    __tablename__ = "bmi_records"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    height = db.Column(db.Float, nullable=False)
    weight = db.Column(db.Float, nullable=False)
    bmi = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), nullable=False)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)


class FoodItem(db.Model):
    __tablename__ = "food_items"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Float, default=0)
    diet_type = db.Column(db.String(50))
    calories = db.Column(db.Float)
    protein = db.Column(db.Float)
    carbs = db.Column(db.Float)
    fat = db.Column(db.Float)
    image = db.Column(db.String(255))
    availability_status = db.Column(db.String(20), default="available", nullable=False)
    order_count = db.Column(db.Integer, default=0)
    view_count = db.Column(db.Integer, default=0)
    provider_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    gallery_images = db.relationship("FoodImage", backref="food", lazy="dynamic", cascade="all, delete-orphan")
    ratings = db.relationship("FoodRating", backref="food", lazy="dynamic", cascade="all, delete-orphan")
    favorites = db.relationship("FavoriteFood", backref="food", lazy="dynamic", cascade="all, delete-orphan")
    order_items = db.relationship("OrderItem", backref="food", lazy="dynamic")
    recent_views = db.relationship("RecentlyViewed", backref="food", lazy="dynamic", cascade="all, delete-orphan")
    views = db.relationship("FoodView", backref="food", lazy="dynamic", cascade="all, delete-orphan")

    views = db.relationship(
        "FoodView",
        backref="food",
        lazy="dynamic",
        cascade="all, delete-orphan"
    )

    @property
    def is_available(self):
        return self.availability_status == "available"

    @property
    def average_rating(self):
        value = db.session.query(func.avg(FoodRating.rating)).filter(FoodRating.food_id == self.id).scalar()
        return round(float(value or 0), 1)

    @property
    def rating_count(self):
        return FoodRating.query.filter_by(food_id=self.id).count()


class FoodView(db.Model):
    __tablename__ = "food_views"

    id = db.Column(db.Integer, primary_key=True)
    food_id = db.Column(db.Integer, db.ForeignKey("food_items.id"), nullable=False)
    viewer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    viewed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class RoleUpgradeRequest(db.Model):
    __tablename__ = "role_upgrade_requests"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    requested_role = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False)
    note = db.Column(db.Text)
    admin_comment = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = db.relationship("User", back_populates="upgrade_requests")


MEAL_GOAL_CHOICES = ["weight_loss", "weight_gain", "maintain_weight"]


class MealLog(db.Model):
    __tablename__ = "meal_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    food_name = db.Column(db.String(120), nullable=False)
    meal_type = db.Column(db.String(20), nullable=False)
    quantity = db.Column(db.String(50), nullable=False)

    goal = db.Column(db.String(30), nullable=True, default=None)

    logged_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    log_date = db.Column(db.Date, default=date.today, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<MealLog user_id={self.user_id} food={self.food_name} meal_type={self.meal_type} goal={self.goal}>"


class CartItem(db.Model):
    __tablename__ = "cart_items"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    food_id = db.Column(db.Integer, db.ForeignKey("food_items.id"), nullable=False)
    quantity = db.Column(db.Integer, default=1, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    food = db.relationship("FoodItem")

    __table_args__ = (
        db.UniqueConstraint("user_id", "food_id", name="uq_user_food_cart"),
    )

    @property
    def subtotal(self):
        return round((self.food.price or 0) * self.quantity, 2)


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(40), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    provider_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    status = db.Column(db.String(20), default="pending", nullable=False, index=True)
    total_price = db.Column(db.Float, default=0, nullable=False)
    delivery_address = db.Column(db.String(255))
    phone = db.Column(db.String(30))
    notes = db.Column(db.Text)
    cancelled_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = db.relationship("OrderItem", backref="order", lazy="dynamic", cascade="all, delete-orphan")
    timeline = db.relationship("OrderTimeline", backref="order", lazy="dynamic", cascade="all, delete-orphan")

    @staticmethod
    def generate_order_number():
        return f"NC-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

    @property
    def can_cancel(self):
        return self.status == "pending"

    @property
    def status_steps(self):
        return ["pending", "confirmed", "preparing", "ready", "delivered"]

    @property
    def timeline_position(self):
        return self.status_steps.index(self.status) if self.status in self.status_steps else -1


class OrderItem(db.Model):
    __tablename__ = "order_items"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)

    food_id = db.Column(db.Integer, db.ForeignKey("food_items.id"))
    food_name = db.Column(db.String(120), nullable=False)
    food_price = db.Column(db.Float, nullable=False)

    quantity = db.Column(db.Integer, nullable=False, default=1)
    subtotal = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class OrderTimeline(db.Model):
    __tablename__ = "order_timelines"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)

    status = db.Column(db.String(20), nullable=False)
    note = db.Column(db.String(255))

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class FavoriteFood(db.Model):
    __tablename__ = "favorite_foods"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    food_id = db.Column(db.Integer, db.ForeignKey("food_items.id"), nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "food_id", name="uq_user_food_favorite"),
    )


class FoodImage(db.Model):
    __tablename__ = "food_images"

    id = db.Column(db.Integer, primary_key=True)
    food_id = db.Column(db.Integer, db.ForeignKey("food_items.id"), nullable=False)

    image_path = db.Column(db.String(255), nullable=False)
    sort_order = db.Column(db.Integer, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class FoodRating(db.Model):
    __tablename__ = "food_ratings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    food_id = db.Column(db.Integer, db.ForeignKey("food_items.id"), nullable=False)

    rating = db.Column(db.Integer, nullable=False)
    review = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "food_id", name="uq_user_food_rating"),
    )


class RecentlyViewed(db.Model):
    __tablename__ = "recently_viewed"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    food_id = db.Column(db.Integer, db.ForeignKey("food_items.id"), nullable=False)

    viewed_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "food_id", name="uq_user_food_viewed"),
    )


# ═══════════════════════════════════════════════════════
#  WALLET & TRANSACTION MODELS
# ═══════════════════════════════════════════════════════

class Wallet(db.Model):
    __tablename__ = "wallets"

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    balance    = db.Column(db.Float, default=0.0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user         = db.relationship("User", backref=db.backref("wallet", uselist=False))
    transactions = db.relationship("WalletTransaction", backref="wallet", lazy="dynamic", cascade="all, delete-orphan")

    def credit(self, amount, description, ref=None):
        self.balance = round(self.balance + amount, 2)
        tx = WalletTransaction(
            wallet_id=self.id, type="credit",
            amount=amount, description=description,
            reference=ref, balance_after=self.balance
        )
        db.session.add(tx)
        return tx

    def debit(self, amount, description, ref=None):
        if self.balance < amount:
            return False
        self.balance = round(self.balance - amount, 2)
        tx = WalletTransaction(
            wallet_id=self.id, type="debit",
            amount=amount, description=description,
            reference=ref, balance_after=self.balance
        )
        db.session.add(tx)
        return tx


class WalletTransaction(db.Model):
    __tablename__ = "wallet_transactions"

    id            = db.Column(db.Integer, primary_key=True)
    wallet_id     = db.Column(db.Integer, db.ForeignKey("wallets.id"), nullable=False, index=True)
    type          = db.Column(db.String(10), nullable=False)
    amount        = db.Column(db.Float, nullable=False)
    balance_after = db.Column(db.Float, nullable=False)
    description   = db.Column(db.String(255))
    reference     = db.Column(db.String(100))
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class PaymentTransaction(db.Model):
    __tablename__ = "payment_transactions"

    id             = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.String(80), unique=True, nullable=False,
                               default=lambda: "TXN-" + uuid.uuid4().hex[:10].upper())
    order_id       = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    user_id        = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    method         = db.Column(db.String(30), nullable=False)
    status         = db.Column(db.String(20), default="pending", nullable=False)
    amount         = db.Column(db.Float, nullable=False)
    currency       = db.Column(db.String(10), default="USD")
    ssl_tran_id    = db.Column(db.String(100))
    ssl_val_id     = db.Column(db.String(100))
    ssl_status     = db.Column(db.String(50))
    ssl_card_type  = db.Column(db.String(50))
    wallet_amount  = db.Column(db.Float, default=0.0)
    gateway_amount = db.Column(db.Float, default=0.0)
    phone_number   = db.Column(db.String(20))
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at     = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    order = db.relationship("Order", backref="payment_transactions")
    user  = db.relationship("User",  backref="payment_transactions")


# ═══════════════════════════════════════════════════════
#  NOTIFICATION MODEL
# ═══════════════════════════════════════════════════════

class Notification(db.Model):
    __tablename__ = "notifications"

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    type       = db.Column(db.String(50), nullable=False)
    title      = db.Column(db.String(120), nullable=False)
    message    = db.Column(db.Text, nullable=False)
    link       = db.Column(db.String(255))
    is_read    = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
