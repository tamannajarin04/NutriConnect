from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func, desc

from app.models import (
    db, FoodItem, CartItem, Order, OrderItem, OrderTimeline,
    FavoriteFood, FoodRating
)

orders_bp = Blueprint("orders", __name__)


def user_or_admin_owns_order(order):
    return order.user_id == current_user.id or current_user.is_admin()


def require_regular_user():
    if not current_user.has_role("user"):
        flash("Only regular users can use cart and ordering features.", "warning")
        return False
    return True


def get_user_orders_paginated(page=1, per_page=10, status_filter="", sort_by="date_desc"):
    query = Order.query.filter_by(user_id=current_user.id)

    if status_filter:
        query = query.filter_by(status=status_filter)

    if sort_by == "date_asc":
        query = query.order_by(Order.created_at.asc())
    elif sort_by == "price_asc":
        query = query.order_by(Order.total_price.asc())
    elif sort_by == "price_desc":
        query = query.order_by(Order.total_price.desc())
    else:
        query = query.order_by(Order.created_at.desc())

    return query.paginate(page=page, per_page=per_page, error_out=False)


# ---------------- CART + MY ORDERS ----------------

@orders_bp.route("/cart")
@login_required
def cart():
    if not require_regular_user():
        return redirect(url_for("main.home"))

    status_filter = (request.args.get("status") or "").strip()
    sort_by = request.args.get("sort", "date_desc")
    page = request.args.get("page", 1, type=int)

    items = (
        CartItem.query
        .filter_by(user_id=current_user.id)
        .join(FoodItem)
        .all()
    )

    total = sum(item.subtotal for item in items if item.food and item.food.is_available)
    orders = get_user_orders_paginated(
        page=page,
        per_page=10,
        status_filter=status_filter,
        sort_by=sort_by
    )

    return render_template(
        "orders/cart.html",
        items=items,
        total=round(total, 2),
        orders=orders,
        status_filter=status_filter,
        sort_by=sort_by
    )


@orders_bp.route("/cart/add/<int:food_id>", methods=["POST"])
@login_required
def add_to_cart(food_id):
    if not require_regular_user():
        return redirect(request.referrer or url_for("main.home"))

    food = FoodItem.query.get_or_404(food_id)

    if not food.is_available:
        flash("This item is out of stock right now.", "warning")
        return redirect(request.referrer or url_for("food_search.search_foods"))

    qty = request.form.get("quantity", 1, type=int)
    qty = max(1, min(qty, 99))

    item = CartItem.query.filter_by(user_id=current_user.id, food_id=food.id).first()
    if item:
        item.quantity = min(item.quantity + qty, 99)
    else:
        item = CartItem(user_id=current_user.id, food_id=food.id, quantity=qty)
        db.session.add(item)

    db.session.commit()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        count = CartItem.query.filter_by(user_id=current_user.id).count()
        return jsonify({"success": True, "cart_count": count})

    flash(f'"{food.name}" added to cart.', "success")
    return redirect(request.referrer or url_for("orders.cart"))


@orders_bp.route("/cart/update/<int:item_id>", methods=["POST"])
@login_required
def update_cart(item_id):
    if not require_regular_user():
        return redirect(url_for("main.home"))

    item = CartItem.query.filter_by(id=item_id, user_id=current_user.id).first_or_404()
    qty = request.form.get("quantity", 1, type=int)

    if qty <= 0:
        db.session.delete(item)
    else:
        item.quantity = min(qty, 99)

    db.session.commit()
    flash("Cart updated.", "success")
    return redirect(url_for("orders.cart"))


@orders_bp.route("/cart/remove/<int:item_id>", methods=["POST"])
@login_required
def remove_from_cart(item_id):
    if not require_regular_user():
        return redirect(url_for("main.home"))

    item = CartItem.query.filter_by(id=item_id, user_id=current_user.id).first_or_404()
    db.session.delete(item)
    db.session.commit()
    flash("Item removed from cart.", "info")
    return redirect(url_for("orders.cart"))


@orders_bp.route("/cart/count")
@login_required
def cart_count():
    if not require_regular_user():
        return jsonify({"count": 0})

    count = CartItem.query.filter_by(user_id=current_user.id).count()
    return jsonify({"count": count})


# ---------------- CHECKOUT ----------------

@orders_bp.route("/checkout")
@login_required
def checkout():
    if not require_regular_user():
        return redirect(url_for("main.home"))

    items = CartItem.query.filter_by(user_id=current_user.id).join(FoodItem).all()

    if not items:
        flash("Your cart is empty.", "warning")
        return redirect(url_for("orders.cart"))

    grouped = {}
    for item in items:
        if not item.food or not item.food.is_available:
            continue

        pid = item.food.provider_id
        grouped.setdefault(pid, {
            "provider": item.food.provider,
            "items": [],
            "subtotal": 0
        })
        grouped[pid]["items"].append(item)
        grouped[pid]["subtotal"] += item.subtotal

    total = round(sum(group["subtotal"] for group in grouped.values()), 2)
    return render_template("orders/checkout.html", grouped=grouped, total=total)


@orders_bp.route("/checkout/place", methods=["POST"])
@login_required
def place_order():
    if not require_regular_user():
        return redirect(url_for("main.home"))

    items = CartItem.query.filter_by(user_id=current_user.id).join(FoodItem).all()

    if not items:
        flash("Your cart is empty.", "warning")
        return redirect(url_for("orders.cart"))

    delivery_address = (request.form.get("delivery_address") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    notes = (request.form.get("notes") or "").strip()

    grouped = {}
    for item in items:
        if not item.food or not item.food.is_available:
            continue
        grouped.setdefault(item.food.provider_id, [])
        grouped[item.food.provider_id].append(item)

    if not grouped:
        flash("No available items found in your cart.", "warning")
        return redirect(url_for("orders.cart"))

    created_orders = []

    for provider_id, cart_items in grouped.items():
        total = round(sum(ci.subtotal for ci in cart_items), 2)

        order = Order(
            order_number=Order.generate_order_number(),
            user_id=current_user.id,
            provider_id=provider_id,
            status="pending",
            total_price=total,
            delivery_address=delivery_address,
            phone=phone,
            notes=notes,
        )
        db.session.add(order)
        db.session.flush()

        for ci in cart_items:
            db.session.add(OrderItem(
                order_id=order.id,
                food_id=ci.food_id,
                food_name=ci.food.name,
                food_price=ci.food.price or 0,
                quantity=ci.quantity,
                subtotal=ci.subtotal,
            ))
            ci.food.order_count = (ci.food.order_count or 0) + ci.quantity

        db.session.add(OrderTimeline(
            order_id=order.id,
            status="pending",
            note="Order placed"
        ))

        created_orders.append(order)

    CartItem.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()

    # ── Redirect to payment page ──────────────────────
    flash("Order placed! Please complete your payment.", "info")
    return redirect(url_for("payment.pay", order_id=created_orders[0].id))


# ---------------- ORDERS ----------------

@orders_bp.route("/orders")
@login_required
def order_history():
    if not require_regular_user():
        return redirect(url_for("main.home"))

    status_filter = (request.args.get("status") or "").strip()
    sort_by = request.args.get("sort", "date_desc")
    page = request.args.get("page", 1, type=int)

    params = {
        "sort": sort_by,
        "page": page,
    }
    if status_filter:
        params["status"] = status_filter

    return redirect(url_for("orders.cart", **params))


@orders_bp.route("/orders/<int:order_id>")
@login_required
def order_detail(order_id):
    if not require_regular_user():
        return redirect(url_for("main.home"))

    order = Order.query.get_or_404(order_id)

    if not user_or_admin_owns_order(order):
        flash("Access denied.", "danger")
        return redirect(url_for("orders.cart"))

    return render_template("orders/detail.html", order=order)


@orders_bp.route("/orders/<int:order_id>/receipt")
@login_required
def receipt(order_id):
    if not require_regular_user():
        return redirect(url_for("main.home"))

    order = Order.query.get_or_404(order_id)

    if not user_or_admin_owns_order(order):
        flash("Access denied.", "danger")
        return redirect(url_for("orders.cart"))

    return render_template("orders/receipt.html", order=order)


@orders_bp.route("/orders/<int:order_id>/cancel", methods=["POST"])
@login_required
def cancel_order(order_id):
    if not require_regular_user():
        return redirect(url_for("main.home"))

    order = Order.query.get_or_404(order_id)

    if order.user_id != current_user.id:
        flash("Access denied.", "danger")
        return redirect(url_for("orders.cart"))

    if not order.can_cancel:
        flash("Only pending orders can be cancelled.", "warning")
        return redirect(url_for("orders.order_detail", order_id=order.id))

    order.status = "cancelled"
    order.cancelled_at = datetime.utcnow()

    db.session.add(OrderTimeline(
        order_id=order.id,
        status="cancelled",
        note="Cancelled by user"
    ))
    db.session.commit()

    flash("Order cancelled successfully.", "success")
    return redirect(url_for("orders.order_detail", order_id=order.id))


@orders_bp.route("/orders/<int:order_id>/reorder", methods=["POST"])
@login_required
def reorder(order_id):
    if not require_regular_user():
        return redirect(url_for("main.home"))

    order = Order.query.get_or_404(order_id)

    if order.user_id != current_user.id:
        flash("Access denied.", "danger")
        return redirect(url_for("orders.cart"))

    added_any = False

    for item in order.items.all():
        if not item.food:
            continue
        if not item.food.is_available:
            continue

        existing = CartItem.query.filter_by(user_id=current_user.id, food_id=item.food_id).first()
        if existing:
            existing.quantity = min(existing.quantity + item.quantity, 99)
        else:
            db.session.add(CartItem(
                user_id=current_user.id,
                food_id=item.food_id,
                quantity=item.quantity
            ))
        added_any = True

    db.session.commit()

    if added_any:
        flash("Items added to cart again.", "success")
        return redirect(url_for("orders.cart"))

    flash("None of the items from that order are currently available.", "warning")
    return redirect(url_for("orders.cart"))


@orders_bp.route("/orders/<int:order_id>/status-json")
@login_required
def order_status_json(order_id):
    order = Order.query.get_or_404(order_id)

    if not user_or_admin_owns_order(order) and order.provider_id != current_user.id:
        return jsonify({"error": "forbidden"}), 403

    timeline = [
        {
            "status": t.status,
            "note": t.note,
            "created_at": t.created_at.strftime("%Y-%m-%d %H:%M")
        }
        for t in order.timeline.order_by(OrderTimeline.created_at.asc()).all()
    ]

    updated_at = order.updated_at.strftime("%Y-%m-%d %H:%M") if order.updated_at else ""
    return jsonify({
        "order_number": order.order_number,
        "status": order.status,
        "timeline": timeline,
        "updated_at": updated_at
    })


# ---------------- FAVORITES ----------------

@orders_bp.route("/favorites")
@login_required
def favorites():
    items = (
        FavoriteFood.query
        .filter_by(user_id=current_user.id)
        .order_by(FavoriteFood.created_at.desc())
        .all()
    )
    return render_template("orders/favorites.html", items=items)


@orders_bp.route("/favorites/toggle/<int:food_id>", methods=["POST"])
@login_required
def toggle_favorite(food_id):
    food = FoodItem.query.get_or_404(food_id)

    favorite = FavoriteFood.query.filter_by(
        user_id=current_user.id,
        food_id=food.id
    ).first()

    if favorite:
        db.session.delete(favorite)
        db.session.commit()
        flash("Removed from favorites.", "info")
    else:
        db.session.add(FavoriteFood(user_id=current_user.id, food_id=food.id))
        db.session.commit()
        flash("Added to favorites.", "success")

    return redirect(request.referrer or url_for("orders.favorites"))


# ---------------- RATINGS ----------------

@orders_bp.route("/foods/<int:food_id>/rate", methods=["POST"])
@login_required
def rate_food(food_id):
    food = FoodItem.query.get_or_404(food_id)
    rating_value = request.form.get("rating", type=int)
    review = (request.form.get("review") or "").strip()

    if rating_value not in [1, 2, 3, 4, 5]:
        flash("Rating must be between 1 and 5.", "danger")
        return redirect(request.referrer or url_for("food_search.food_detail", food_id=food.id))

    existing = FoodRating.query.filter_by(user_id=current_user.id, food_id=food.id).first()

    if existing:
        existing.rating = rating_value
        existing.review = review
    else:
        db.session.add(FoodRating(
            user_id=current_user.id,
            food_id=food.id,
            rating=rating_value,
            review=review
        ))

    db.session.commit()
    flash("Rating submitted successfully.", "success")
    return redirect(request.referrer or url_for("food_search.food_detail", food_id=food.id))


# ---------------- DISCOVERY ----------------

@orders_bp.route("/trending-foods")
@login_required
def trending_foods():
    foods = (
        FoodItem.query
        .order_by(desc(FoodItem.order_count), desc(FoodItem.view_count))
        .limit(10)
        .all()
    )
    return render_template("dashboard/trending.html", foods=foods)


@orders_bp.route("/top-rated-foods")
@login_required
def leaderboard():
    foods = (
        db.session.query(FoodItem)
        .outerjoin(FoodRating, FoodRating.food_id == FoodItem.id)
        .group_by(FoodItem.id)
        .order_by(func.avg(FoodRating.rating).desc().nullslast(), FoodItem.order_count.desc())
        .limit(10)
        .all()
    )
    return render_template("dashboard/leaderboard.html", foods=foods)