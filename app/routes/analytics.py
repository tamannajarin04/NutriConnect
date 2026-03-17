from flask import Blueprint, render_template
from sqlalchemy import func

from app.models import db, User, Order, FoodItem, FoodRating
from ._rbac import role_required

analytics_bp = Blueprint("analytics", __name__)


@analytics_bp.route("/analytics")
@role_required("admin")
def analytics_dashboard():
    total_users = User.query.count()
    total_orders = Order.query.count()
    total_foods = FoodItem.query.count()

    avg_rating = db.session.query(func.avg(FoodRating.rating)).scalar() or 0

    popular_foods = (
        FoodItem.query
        .order_by(FoodItem.order_count.desc(), FoodItem.view_count.desc())
        .limit(10)
        .all()
    )

    orders_by_status = {
        "pending": Order.query.filter_by(status="pending").count(),
        "confirmed": Order.query.filter_by(status="confirmed").count(),
        "preparing": Order.query.filter_by(status="preparing").count(),
        "ready": Order.query.filter_by(status="ready").count(),
        "delivered": Order.query.filter_by(status="delivered").count(),
        "cancelled": Order.query.filter_by(status="cancelled").count(),
    }

    recent_users = (
        User.query
        .order_by(User.created_at.desc())
        .limit(10)
        .all()
    )

    provider_base = User.query.filter(User.roles.any(name="food_provider"))

    active_providers = provider_base.filter_by(account_status="active").count()
    restricted_providers = provider_base.filter_by(account_status="restricted").count()
    suspended_providers = provider_base.filter_by(account_status="suspended").count()
    verified_providers = provider_base.filter_by(is_verified=True).count()

    return render_template(
        "dashboard/admin_analytics.html",
        total_users=total_users,
        total_orders=total_orders,
        total_foods=total_foods,
        avg_rating=round(float(avg_rating), 1),
        popular_foods=popular_foods,
        orders_by_status=orders_by_status,
        recent_users=recent_users,
        active_providers=active_providers,
        restricted_providers=restricted_providers,
        suspended_providers=suspended_providers,
        verified_providers=verified_providers,
    )