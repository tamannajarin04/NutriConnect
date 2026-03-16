from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import func

from app.models import db, Order, OrderTimeline, FoodItem, FoodRating

provider_bp = Blueprint("provider", __name__)


def provider_required():
    return current_user.is_authenticated and (
        current_user.is_food_provider() or current_user.is_admin()
    )


@provider_bp.before_request
def protect_provider_routes():
    if not provider_required():
        flash("Access denied. Food provider role required.", "danger")
        return redirect(url_for("main.home"))


def get_provider_foods_with_ratings():
    rows = (
        db.session.query(
            FoodItem,
            func.coalesce(func.avg(FoodRating.rating), 0).label("avg_rating"),
            func.count(FoodRating.id).label("rating_total"),
        )
        .outerjoin(FoodRating, FoodRating.food_id == FoodItem.id)
        .filter(FoodItem.provider_id == current_user.id)
        .group_by(FoodItem.id)
        .order_by(FoodItem.created_at.desc())
        .all()
    )

    foods = []
    for food, avg_rating, rating_total in rows:
        food.average_rating_display = round(float(avg_rating or 0), 1)
        food.rating_count_display = int(rating_total or 0)
        foods.append(food)

    return foods


def get_provider_food_summary(foods):
    total_foods = len(foods)
    total_with_photos = sum(1 for food in foods if food.image)

    calorie_values = [
        float(food.calories) for food in foods
        if food.calories is not None
    ]
    price_values = [
        float(food.price) for food in foods
        if food.price is not None
    ]

    avg_calories = round(sum(calorie_values) / len(calorie_values)) if calorie_values else None
    avg_price = round(sum(price_values) / len(price_values), 2) if price_values else None

    return {
        "total_foods": total_foods,
        "total_with_photos": total_with_photos,
        "avg_calories": avg_calories,
        "avg_price": avg_price,
    }


def get_provider_order_insight_data():
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=6)
    start_datetime = datetime.combine(start_date, datetime.min.time())

    daily_rows = (
        db.session.query(
            func.date(Order.created_at).label("order_day"),
            func.count(Order.id).label("order_count"),
        )
        .filter(
            Order.provider_id == current_user.id,
            Order.created_at >= start_datetime,
        )
        .group_by(func.date(Order.created_at))
        .all()
    )

    daily_map = {str(day): int(count or 0) for day, count in daily_rows}
    daily_labels = []
    daily_values = []

    for i in range(7):
        day = start_date + timedelta(days=i)
        daily_labels.append(day.strftime("%d %b"))
        daily_values.append(daily_map.get(day.isoformat(), 0))

    status_order = ["pending", "confirmed", "preparing", "ready", "delivered", "cancelled"]
    status_rows = (
        db.session.query(Order.status, func.count(Order.id))
        .filter(Order.provider_id == current_user.id)
        .group_by(Order.status)
        .all()
    )
    status_map = {status: int(count or 0) for status, count in status_rows}
    status_labels = [status.title() for status in status_order]
    status_values = [status_map.get(status, 0) for status in status_order]

    top_food_rows = (
        db.session.query(
            FoodItem.name,
            func.coalesce(FoodItem.order_count, 0).label("order_count"),
        )
        .filter(FoodItem.provider_id == current_user.id)
        .order_by(
            func.coalesce(FoodItem.order_count, 0).desc(),
            FoodItem.name.asc()
        )
        .limit(6)
        .all()
    )

    top_food_labels = [name for name, _ in top_food_rows]
    top_food_values = [int(count or 0) for _, count in top_food_rows]

    total_orders = Order.query.filter_by(provider_id=current_user.id).count()
    pending_orders = Order.query.filter_by(provider_id=current_user.id, status="pending").count()
    delivered_orders = Order.query.filter_by(provider_id=current_user.id, status="delivered").count()
    cancelled_orders = Order.query.filter_by(provider_id=current_user.id, status="cancelled").count()

    return {
        "daily_labels": daily_labels,
        "daily_values": daily_values,
        "status_labels": status_labels,
        "status_values": status_values,
        "top_food_labels": top_food_labels,
        "top_food_values": top_food_values,
        "total_orders": total_orders,
        "pending_orders": pending_orders,
        "delivered_orders": delivered_orders,
        "cancelled_orders": cancelled_orders,
    }


@provider_bp.route("/dashboard")
@login_required
def provider_dashboard():
    total_orders = Order.query.filter_by(provider_id=current_user.id).count()
    pending_orders = Order.query.filter_by(
        provider_id=current_user.id,
        status="pending"
    ).count()
    delivered_orders = Order.query.filter_by(
        provider_id=current_user.id,
        status="delivered"
    ).count()

    revenue = (
        db.session.query(func.sum(Order.total_price))
        .filter(
            Order.provider_id == current_user.id,
            Order.status != "cancelled"
        )
        .scalar()
    ) or 0

    avg_rating = (
        db.session.query(func.avg(FoodRating.rating))
        .join(FoodItem, FoodItem.id == FoodRating.food_id)
        .filter(FoodItem.provider_id == current_user.id)
        .scalar()
    ) or 0

    foods = get_provider_foods_with_ratings()
    food_summary = get_provider_food_summary(foods)

    return render_template(
        "dashboard/food_provider_dashboard.html",
        total_orders=total_orders,
        pending_orders=pending_orders,
        delivered_orders=delivered_orders,
        revenue=round(float(revenue), 2),
        avg_rating=round(float(avg_rating), 1),
        foods=foods,
        total_foods=food_summary["total_foods"],
        total_with_photos=food_summary["total_with_photos"],
        avg_calories=food_summary["avg_calories"],
        avg_price=food_summary["avg_price"],
    )


@provider_bp.route("/orders")
@login_required
def provider_orders():
    status_filter = (request.args.get("status") or "").strip()
    sort_by = request.args.get("sort", "date_desc")

    base_query = Order.query.filter_by(provider_id=current_user.id)

    query = base_query
    if status_filter:
        query = query.filter_by(status=status_filter)

    if sort_by == "price_asc":
        query = query.order_by(Order.total_price.asc())
    elif sort_by == "price_desc":
        query = query.order_by(Order.total_price.desc())
    elif sort_by == "date_asc":
        query = query.order_by(Order.created_at.asc())
    else:
        query = query.order_by(Order.created_at.desc())

    orders = query.all()

    recent_orders = (
        base_query.order_by(Order.created_at.desc())
        .limit(5)
        .all()
    )

    return render_template(
        "dashboard/provider_order_list.html",
        orders=orders,
        recent_orders=recent_orders,
        status_filter=status_filter,
        sort_by=sort_by,
    )


@provider_bp.route("/orders/insights")
@login_required
def provider_order_insights():
    insight_data = get_provider_order_insight_data()

    return render_template(
        "dashboard/provider_order_insights.html",
        daily_labels=insight_data["daily_labels"],
        daily_values=insight_data["daily_values"],
        status_labels=insight_data["status_labels"],
        status_values=insight_data["status_values"],
        top_food_labels=insight_data["top_food_labels"],
        top_food_values=insight_data["top_food_values"],
        total_orders=insight_data["total_orders"],
        pending_orders=insight_data["pending_orders"],
        delivered_orders=insight_data["delivered_orders"],
        cancelled_orders=insight_data["cancelled_orders"],
    )


@provider_bp.route("/orders/<int:order_id>")
@login_required
def provider_order_detail(order_id):
    order = Order.query.get_or_404(order_id)

    if order.provider_id != current_user.id and not current_user.is_admin():
        flash("Access denied.", "danger")
        return redirect(url_for("provider.provider_orders"))

    return render_template("dashboard/provider_order_detail.html", order=order)


@provider_bp.route("/orders/<int:order_id>/status", methods=["POST"])
@login_required
def update_order_status(order_id):
    order = Order.query.get_or_404(order_id)

    if order.provider_id != current_user.id and not current_user.is_admin():
        flash("Access denied.", "danger")
        return redirect(url_for("provider.provider_orders"))

    new_status = (request.form.get("status") or "").strip().lower()
    valid_statuses = [
        "pending",
        "confirmed",
        "preparing",
        "ready",
        "delivered",
        "cancelled",
    ]

    if new_status not in valid_statuses:
        flash("Invalid order status.", "danger")
        return redirect(url_for("provider.provider_order_detail", order_id=order.id))

    order.status = new_status

    db.session.add(OrderTimeline(
        order_id=order.id,
        status=new_status,
        note=f"Updated by provider to {new_status.title()}",
    ))
    db.session.commit()

    flash("Order status updated successfully.", "success")
    return redirect(url_for("provider.provider_order_detail", order_id=order.id))