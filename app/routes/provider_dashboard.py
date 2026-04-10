from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import func

from app.models import (
    db, Order, OrderTimeline, FoodItem, FoodRating, FoodView,
    PaymentTransaction
)

provider_bp = Blueprint("provider", __name__)

PROVIDER_ORDERS_PER_PAGE = 5
RECENT_ORDER_DAYS = 5
PROVIDER_STATUS_FLOW = ["pending", "confirmed", "preparing", "ready", "delivered"]


def provider_required():
    return current_user.is_authenticated and (
        current_user.is_food_provider() or current_user.is_admin()
    )


@provider_bp.before_request
def protect_provider_routes():
    if not provider_required():
        flash("Access denied. Food provider role required.", "danger")
        return redirect(url_for("main.home"))


def parse_date_param(value):
    value = (value or "").strip()
    if not value:
        return None

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def get_summary_date_bounds(summary_range, start_date_str="", end_date_str=""):
    today = datetime.utcnow().date()
    summary_range = (summary_range or "today").strip().lower()

    if summary_range == "week":
        start_date = today - timedelta(days=6)
        end_date = today
        label = "This Week"

    elif summary_range == "month":
        start_date = today.replace(day=1)
        end_date = today
        label = "This Month"

    elif summary_range == "custom":
        parsed_start = parse_date_param(start_date_str)
        parsed_end = parse_date_param(end_date_str)

        if parsed_start and parsed_end:
            start_date = min(parsed_start, parsed_end)
            end_date = max(parsed_start, parsed_end)
        elif parsed_start:
            start_date = parsed_start
            end_date = parsed_start
        elif parsed_end:
            start_date = parsed_end
            end_date = parsed_end
        else:
            start_date = today
            end_date = today

        label = "Custom Range"

    else:
        summary_range = "today"
        start_date = today
        end_date = today
        label = "Today"

    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date + timedelta(days=1), datetime.min.time())

    return {
        "summary_range": summary_range,
        "label": label,
        "start_date": start_date,
        "end_date": end_date,
        "start_datetime": start_datetime,
        "end_datetime": end_datetime,
    }


def get_recent_order_management_bounds(days=RECENT_ORDER_DAYS):
    today = datetime.utcnow().date()
    days = max(int(days or 1), 1)

    start_date = today - timedelta(days=days - 1)
    end_date = today

    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date + timedelta(days=1), datetime.min.time())

    return {
        "days": days,
        "label": f"Last {days} Days",
        "start_date": start_date,
        "end_date": end_date,
        "start_datetime": start_datetime,
        "end_datetime": end_datetime,
    }


def apply_provider_order_sort(query, sort_by):
    sort_by = (sort_by or "date_desc").strip().lower()

    if sort_by == "price_asc":
        return query.order_by(Order.total_price.asc(), Order.created_at.desc())
    if sort_by == "price_desc":
        return query.order_by(Order.total_price.desc(), Order.created_at.desc())
    if sort_by == "date_asc":
        return query.order_by(Order.created_at.asc())

    return query.order_by(Order.created_at.desc())


def normalize_provider_order_status(status):
    status = (status or "pending").strip().lower()
    allowed = set(PROVIDER_STATUS_FLOW + ["cancelled"])
    return status if status in allowed else "pending"


def get_next_provider_status(current_status):
    current_status = normalize_provider_order_status(current_status)

    next_map = {
        "pending": "confirmed",
        "confirmed": "preparing",
        "preparing": "ready",
        "ready": "delivered",
    }
    return next_map.get(current_status)


def get_primary_status_button_label(current_status):
    current_status = normalize_provider_order_status(current_status)

    label_map = {
        "pending": "Confirm",
        "confirmed": "Preparing",
        "preparing": "Ready",
        "ready": "Delivered",
    }
    return label_map.get(current_status)


def get_food_views_count(food):
    display_value = getattr(food, "total_views_display", None)
    if display_value is not None:
        try:
            return int(display_value or 0)
        except (TypeError, ValueError):
            pass

    views_rel = getattr(food, "views", None)
    if views_rel is not None:
        try:
            return int(views_rel.count())
        except TypeError:
            try:
                return int(len(views_rel))
            except TypeError:
                pass

    for attr_name in ("view_count", "total_views", "views_count"):
        value = getattr(food, attr_name, None)
        if value is not None:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                pass

    return 0


def get_provider_foods_with_ratings():
    view_counts_subquery = (
        db.session.query(
            FoodView.food_id.label("food_id"),
            func.count(FoodView.id).label("view_total"),
        )
        .group_by(FoodView.food_id)
        .subquery()
    )

    rows = (
        db.session.query(
            FoodItem,
            func.coalesce(func.avg(FoodRating.rating), 0).label("avg_rating"),
            func.count(func.distinct(FoodRating.id)).label("rating_total"),
            func.coalesce(view_counts_subquery.c.view_total, 0).label("view_total"),
        )
        .outerjoin(FoodRating, FoodRating.food_id == FoodItem.id)
        .outerjoin(view_counts_subquery, view_counts_subquery.c.food_id == FoodItem.id)
        .filter(FoodItem.provider_id == current_user.id)
        .group_by(FoodItem.id, view_counts_subquery.c.view_total)
        .order_by(FoodItem.created_at.desc())
        .all()
    )

    foods = []
    for food, avg_rating, rating_total, view_total in rows:
        food.average_rating_display = round(float(avg_rating or 0), 1)
        food.rating_count_display = int(rating_total or 0)
        food.total_views_display = int(view_total or 0)
        food.view_count = int(view_total or 0)
        foods.append(food)

    return foods


def get_provider_food_summary(foods):
    total_foods = len(foods)
    total_with_photos = sum(1 for food in foods if food.image)
    total_views = sum(get_food_views_count(food) for food in foods)

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
        "total_views": total_views,
    }


def get_provider_content_insights(foods):
    insights = []

    for food in foods:
        views = int(getattr(food, "total_views_display", 0) or 0)
        orders = int(getattr(food, "order_count", 0) or 0)
        rating = float(getattr(food, "average_rating_display", 0) or 0)
        reviews = int(getattr(food, "rating_count_display", 0) or 0)

        insights.append({
            "food": food,
            "name": food.name,
            "title": food.name,
            "views": views,
            "orders": orders,
            "order_count": orders,
            "rating": rating,
            "avg_rating": rating,
            "reviews": reviews,
            "rating_count": reviews,
            "image": food.image,
            "price": float(food.price) if food.price is not None else None,
            "is_available": bool(food.is_available),
        })

    insights.sort(
        key=lambda item: (item["views"], item["orders"], item["rating"]),
        reverse=True,
    )

    return insights


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


def get_provider_order_summary(provider_id, summary_range="today", start_date_str="", end_date_str=""):
    bounds = get_summary_date_bounds(summary_range, start_date_str, end_date_str)

    base_query = Order.query.filter(
        Order.provider_id == provider_id,
        Order.created_at >= bounds["start_datetime"],
        Order.created_at < bounds["end_datetime"],
    )

    status_rows = (
        db.session.query(Order.status, func.count(Order.id))
        .filter(
            Order.provider_id == provider_id,
            Order.created_at >= bounds["start_datetime"],
            Order.created_at < bounds["end_datetime"],
        )
        .group_by(Order.status)
        .all()
    )
    status_map = {status: int(count or 0) for status, count in status_rows}

    total_orders = int(base_query.count())

    non_cancelled_total = (
        db.session.query(func.coalesce(func.sum(Order.total_price), 0))
        .filter(
            Order.provider_id == provider_id,
            Order.created_at >= bounds["start_datetime"],
            Order.created_at < bounds["end_datetime"],
            Order.status != "cancelled",
        )
        .scalar()
    ) or 0

    completed_order_count = (
        db.session.query(func.count(Order.id))
        .filter(
            Order.provider_id == provider_id,
            Order.created_at >= bounds["start_datetime"],
            Order.created_at < bounds["end_datetime"],
            Order.status != "cancelled",
        )
        .scalar()
    ) or 0

    avg_order_value = round(float(non_cancelled_total) / completed_order_count, 2) if completed_order_count else 0

    return {
        "summary_range": bounds["summary_range"],
        "summary_label": bounds["label"],
        "start_date": bounds["start_date"].isoformat(),
        "end_date": bounds["end_date"].isoformat(),
        "total_orders": total_orders,
        "pending_orders": status_map.get("pending", 0),
        "confirmed_orders": status_map.get("confirmed", 0),
        "preparing_orders": status_map.get("preparing", 0),
        "ready_orders": status_map.get("ready", 0),
        "delivered_orders": status_map.get("delivered", 0),
        "cancelled_orders": status_map.get("cancelled", 0),
        "total_revenue": round(float(non_cancelled_total), 2),
        "avg_order_value": avg_order_value,
    }


def get_provider_total_orders_list(
    provider_id,
    summary_range="today",
    start_date_str="",
    end_date_str="",
    show_all=False,
    limit=5,
):
    bounds = get_summary_date_bounds(summary_range, start_date_str, end_date_str)

    query = (
        Order.query
        .filter(
            Order.provider_id == provider_id,
            Order.created_at >= bounds["start_datetime"],
            Order.created_at < bounds["end_datetime"],
        )
        .order_by(Order.created_at.desc())
    )

    total_count = query.count()

    if show_all:
        items = query.all()
    else:
        items = query.limit(limit).all()

    return {
        "items": items,
        "total_count": total_count,
        "has_more": total_count > limit,
        "show_all": show_all,
        "limit": limit,
        "summary_range": bounds["summary_range"],
        "summary_label": bounds["label"],
        "start_date": bounds["start_date"].isoformat(),
        "end_date": bounds["end_date"].isoformat(),
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
    insights = get_provider_content_insights(foods)

    return render_template(
        "dashboard/food_provider_dashboard.html",
        total_orders=total_orders,
        pending_orders=pending_orders,
        delivered_orders=delivered_orders,
        revenue=round(float(revenue), 2),
        avg_rating=round(float(avg_rating), 1),
        foods=foods,
        insights=insights,
        total_foods=food_summary["total_foods"],
        total_with_photos=food_summary["total_with_photos"],
        avg_calories=food_summary["avg_calories"],
        avg_price=food_summary["avg_price"],
        total_views=food_summary["total_views"],
    )


@provider_bp.route("/orders")
@login_required
def provider_orders():
    status_filter = (request.args.get("status") or "").strip().lower()
    sort_by = (request.args.get("sort") or "date_desc").strip().lower()
    page = request.args.get("page", 1, type=int)

    summary_range = (request.args.get("summary_range") or "today").strip().lower()
    summary_start = (request.args.get("summary_start") or "").strip()
    summary_end = (request.args.get("summary_end") or "").strip()
    summary_show = (request.args.get("summary_show") or "preview").strip().lower()

    recent_bounds = get_recent_order_management_bounds(days=RECENT_ORDER_DAYS)

    recent_orders_query = (
        Order.query
        .filter(
            Order.provider_id == current_user.id,
            Order.created_at >= recent_bounds["start_datetime"],
            Order.created_at < recent_bounds["end_datetime"],
        )
    )

    if status_filter:
        recent_orders_query = recent_orders_query.filter(Order.status == status_filter)

    recent_orders_query = apply_provider_order_sort(recent_orders_query, sort_by)

    orders = recent_orders_query.paginate(
        page=page,
        per_page=PROVIDER_ORDERS_PER_PAGE,
        error_out=False,
    )

    order_summary = get_provider_order_summary(
        provider_id=current_user.id,
        summary_range=summary_range,
        start_date_str=summary_start,
        end_date_str=summary_end,
    )

    total_orders_section = get_provider_total_orders_list(
        provider_id=current_user.id,
        summary_range=summary_range,
        start_date_str=summary_start,
        end_date_str=summary_end,
        show_all=(summary_show == "all"),
        limit=5,
    )

    return render_template(
        "dashboard/provider_order_list.html",
        orders=orders,
        status_filter=status_filter,
        sort_by=sort_by,
        order_summary=order_summary,
        summary_range=order_summary["summary_range"],
        summary_start=order_summary["start_date"],
        summary_end=order_summary["end_date"],
        summary_show="all" if total_orders_section["show_all"] else "preview",
        total_orders_list=total_orders_section["items"],
        total_orders_count=total_orders_section["total_count"],
        total_orders_has_more=total_orders_section["has_more"],
        total_orders_limit=total_orders_section["limit"],
        recent_order_days=recent_bounds["days"],
        recent_start_date=recent_bounds["start_date"].isoformat(),
        recent_end_date=recent_bounds["end_date"].isoformat(),
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

    paid_txn = (
        PaymentTransaction.query
        .filter_by(order_id=order.id, status="success")
        .order_by(PaymentTransaction.created_at.desc())
        .first()
    )
    is_paid = paid_txn is not None

    current_status = normalize_provider_order_status(order.status)

    if is_paid and current_status == "pending":
        order.status = "confirmed"

        already_has_confirmed_timeline = (
            order.timeline
            .filter(OrderTimeline.status == "confirmed")
            .first()
            is not None
        )

        if not already_has_confirmed_timeline:
            db.session.add(OrderTimeline(
                order_id=order.id,
                status="confirmed",
                note="Payment received"
            ))

        db.session.commit()
        current_status = "confirmed"

    timeline_entries = (
        order.timeline
        .order_by(OrderTimeline.created_at.asc(), OrderTimeline.id.asc())
        .all()
    )

    next_status = get_next_provider_status(current_status)
    primary_action_label = get_primary_status_button_label(current_status)
    can_cancel = current_status not in {"delivered", "cancelled"}

    return render_template(
        "dashboard/provider_order_detail.html",
        order=order,
        items=order.items.all(),
        timeline_entries=timeline_entries,
        current_status=current_status,
        is_paid=is_paid,
        next_status=next_status,
        primary_action_label=primary_action_label,
        can_cancel=can_cancel,
    )


@provider_bp.route("/orders/<int:order_id>/status", methods=["POST"])
@login_required
def update_order_status(order_id):
    order = Order.query.get_or_404(order_id)

    if order.provider_id != current_user.id and not current_user.is_admin():
        flash("Access denied.", "danger")
        return redirect(url_for("provider.provider_orders"))

    current_status = normalize_provider_order_status(order.status)
    action = (request.form.get("action") or "").strip().lower()

    if current_status == "delivered":
        flash("Delivered orders are locked and cannot be changed again.", "warning")
        return redirect(url_for("provider.provider_order_detail", order_id=order.id))

    if current_status == "cancelled":
        flash("Cancelled orders are locked and cannot be changed again.", "warning")
        return redirect(url_for("provider.provider_order_detail", order_id=order.id))

    if action == "cancel":
        new_status = "cancelled"
        note = "Cancelled by provider"

    elif action == "advance":
        new_status = get_next_provider_status(current_status)

        if not new_status:
            flash("No further status step is available for this order.", "info")
            return redirect(url_for("provider.provider_order_detail", order_id=order.id))

        note_map = {
            "confirmed": "Confirmed by provider",
            "preparing": "Order is now being prepared",
            "ready": "Order is ready",
            "delivered": "Order marked as delivered",
        }
        note = note_map.get(new_status, f"Updated by provider to {new_status.title()}")

    else:
        flash("Invalid action.", "danger")
        return redirect(url_for("provider.provider_order_detail", order_id=order.id))

    order.status = new_status

    if hasattr(order, "cancelled_at"):
        if new_status == "cancelled":
            order.cancelled_at = datetime.utcnow()
        else:
            order.cancelled_at = None

    db.session.add(OrderTimeline(
        order_id=order.id,
        status=new_status,
        note=note,
    ))
    db.session.commit()

    flash(f"Order marked as {new_status.title()}.", "success")
    return redirect(url_for("provider.provider_order_detail", order_id=order.id))