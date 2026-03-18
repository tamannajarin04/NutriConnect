from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from functools import wraps
from datetime import datetime
from sqlalchemy import or_
import os
import uuid

from app.models import (
    db,
    FoodItem,
    FoodImage,
    FavoriteFood,
    RecentlyViewed,
    FoodRating,
    FoodView,
)

food_bp = Blueprint("food", __name__)
food_search_bp = Blueprint("food_search", __name__)

UPLOAD_FOLDER = "app/static/uploads"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

VALID_DIET_TYPES = {
    "vegan",
    "vegetarian",
    "keto",
    "paleo",
    "gluten-free",
    "dairy-free",
    "halal",
    "low-carb",
    "high-protein",
}

VALID_AVAILABILITY_STATUSES = {"available", "out_of_stock"}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def normalize_availability_status(value):
    value = (value or "available").strip().lower()
    return value if value in VALID_AVAILABILITY_STATUSES else "available"


def normalize_diet_type(value):
    value = (value or "").strip().lower()
    return value if value in VALID_DIET_TYPES else None


def save_uploaded_file(file_obj):
    if not file_obj or file_obj.filename == "":
        return None

    if not allowed_file(file_obj.filename):
        return None

    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    original_name = secure_filename(file_obj.filename)
    ext = original_name.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    file_path = os.path.join(UPLOAD_FOLDER, filename)
    file_obj.save(file_path)
    return filename


def food_provider_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_food_provider() and not current_user.is_admin():
            flash("Access denied. Food provider role required.", "danger")
            return redirect(url_for("main.home"))
        return f(*args, **kwargs)
    return decorated_function


def track_recent_view(user_id, food):
    existing_view = RecentlyViewed.query.filter_by(
        user_id=user_id,
        food_id=food.id
    ).first()

    if existing_view:
        existing_view.viewed_at = db.func.now()
    else:
        db.session.add(RecentlyViewed(user_id=user_id, food_id=food.id))


def record_food_view_once_per_day(food, viewer_id):
    """
    Count exactly one popularity view per user per food per day.
    Updates BOTH:
    - FoodView table
    - food.view_count column
    """
    today = datetime.utcnow().date()

    already_viewed = FoodView.query.filter(
        FoodView.food_id == food.id,
        FoodView.viewer_id == viewer_id,
        db.func.date(FoodView.viewed_at) == today
    ).first()

    if already_viewed:
        return False

    db.session.add(FoodView(food_id=food.id, viewer_id=viewer_id))
    food.view_count = (food.view_count or 0) + 1
    return True


# ── Food Provider Dashboard Redirect ──────────────────────────────────────────
@food_bp.route("/foods")
@login_required
@food_provider_required
def provider_foods():
    return redirect(url_for("provider.provider_dashboard"))


# ── Track Food View (one view per user per day) ───────────────────────────────
@food_search_bp.route("/view/<int:food_id>", methods=["POST"])
@login_required
def track_view(food_id):
    food = FoodItem.query.get_or_404(food_id)

    counted = record_food_view_once_per_day(food, current_user.id)
    db.session.commit()

    return jsonify({
        "ok": True,
        "counted": counted,
        "view_count": food.view_count or 0
    })


# ── Add Food ──────────────────────────────────────────────────────────────────
@food_bp.route("/foods/add", methods=["GET", "POST"])
@login_required
@food_provider_required
def add_food():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        price = request.form.get("price") or None
        calories = request.form.get("calories") or None
        protein = request.form.get("protein") or None
        carbs = request.form.get("carbs") or None
        fat = request.form.get("fat") or None

        diet_type_value = normalize_diet_type(request.form.get("diet_type"))
        availability_status = normalize_availability_status(request.form.get("availability_status"))

        if not name:
            flash("Food name is required.", "danger")
            return render_template("dashboard/add_food.html")

        image_filename = None
        image_file = request.files.get("image")
        if image_file and image_file.filename != "":
            image_filename = save_uploaded_file(image_file)
            if not image_filename:
                flash("Invalid image format. Allowed: png, jpg, jpeg, gif, webp", "danger")
                return render_template("dashboard/add_food.html")

        food = FoodItem(
            name=name,
            description=description,
            diet_type=diet_type_value,
            price=float(price) if price else None,
            calories=float(calories) if calories else None,
            protein=float(protein) if protein else None,
            carbs=float(carbs) if carbs else None,
            fat=float(fat) if fat else None,
            image=image_filename,
            availability_status=availability_status,
            provider_id=current_user.id,
        )

        db.session.add(food)
        db.session.commit()

        flash("Food item added successfully!", "success")
        return redirect(url_for("provider.provider_dashboard"))

    return render_template("dashboard/add_food.html")


# ── Edit Food ─────────────────────────────────────────────────────────────────
@food_bp.route("/foods/edit/<int:id>", methods=["GET", "POST"])
@login_required
@food_provider_required
def edit_food(id):
    food = FoodItem.query.get_or_404(id)

    if food.provider_id != current_user.id:
        flash("Unauthorized access.", "danger")
        return redirect(url_for("provider.provider_dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()

        if not name:
            flash("Food name is required.", "danger")
            return render_template("dashboard/edit_food.html", food=food)

        food.name = name
        food.description = request.form.get("description", "").strip()
        food.diet_type = normalize_diet_type(request.form.get("diet_type"))
        food.price = float(request.form["price"]) if request.form.get("price") else None
        food.calories = float(request.form["calories"]) if request.form.get("calories") else None
        food.protein = float(request.form["protein"]) if request.form.get("protein") else None
        food.carbs = float(request.form["carbs"]) if request.form.get("carbs") else None
        food.fat = float(request.form["fat"]) if request.form.get("fat") else None
        food.availability_status = normalize_availability_status(request.form.get("availability_status"))

        image_file = request.files.get("image")
        if image_file and image_file.filename != "":
            image_filename = save_uploaded_file(image_file)
            if not image_filename:
                flash("Invalid image format.", "danger")
                return render_template("dashboard/edit_food.html", food=food)
            food.image = image_filename

        db.session.add(food)
        db.session.commit()

        flash("Food item updated successfully!", "success")
        return redirect(url_for("provider.provider_dashboard"))

    return render_template("dashboard/edit_food.html", food=food)


# ── Upload Food Gallery ───────────────────────────────────────────────────────
@food_bp.route("/foods/<int:id>/gallery", methods=["POST"])
@login_required
@food_provider_required
def upload_food_gallery(id):
    food = FoodItem.query.get_or_404(id)

    if food.provider_id != current_user.id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("provider.provider_dashboard"))

    files = request.files.getlist("gallery_images")
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    uploaded = 0
    for file_obj in files:
        if not file_obj or file_obj.filename == "":
            continue

        filename = save_uploaded_file(file_obj)
        if filename:
            db.session.add(FoodImage(food_id=food.id, image_path=filename))
            uploaded += 1

    db.session.commit()
    flash(f"{uploaded} gallery image(s) uploaded.", "success")
    return redirect(url_for("food.edit_food", id=food.id))


# ── Delete Food ───────────────────────────────────────────────────────────────
@food_bp.route("/foods/delete/<int:id>", methods=["POST"])
@login_required
@food_provider_required
def delete_food(id):
    food = FoodItem.query.get_or_404(id)

    if food.provider_id != current_user.id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("provider.provider_dashboard"))

    db.session.delete(food)
    db.session.commit()
    flash("Food item deleted.", "success")
    return redirect(url_for("provider.provider_dashboard"))


# ── Food Detail ───────────────────────────────────────────────────────────────
@food_search_bp.route("/<int:food_id>")
@login_required
def food_detail(food_id):
    food = FoodItem.query.get_or_404(food_id)

    # Count popularity exactly once per user per day
    record_food_view_once_per_day(food, current_user.id)

    # Keep recently viewed separate from popularity
    track_recent_view(current_user.id, food)

    db.session.commit()

    favorite = FavoriteFood.query.filter_by(
        user_id=current_user.id,
        food_id=food.id
    ).first()

    my_rating = FoodRating.query.filter_by(
        user_id=current_user.id,
        food_id=food.id
    ).first()

    ratings = (
        FoodRating.query
        .filter_by(food_id=food.id)
        .order_by(FoodRating.created_at.desc())
        .limit(10)
        .all()
    )

    gallery = (
        FoodImage.query
        .filter_by(food_id=food.id)
        .order_by(FoodImage.sort_order.asc(), FoodImage.id.asc())
        .all()
    )

    return render_template(
        "dashboard/detail.html",
        food=food,
        gallery=gallery,
        is_favorite=bool(favorite),
        my_rating=my_rating,
        ratings=ratings
    )


# ── Food Search (all logged-in users) ────────────────────────────────────────
@food_search_bp.route("/search")
@login_required
def search_foods():
    q = request.args.get("q", "").strip()
    selected_diet_type = normalize_diet_type(request.args.get("diet_type"))

    max_cal = request.args.get("max_cal", type=float)
    min_pro = request.args.get("min_protein", type=float)
    max_price = request.args.get("max_price", type=float)
    sort_by = request.args.get("sort", "name")
    page = request.args.get("page", 1, type=int)
    show_out_of_stock = request.args.get("show_out_of_stock", "0") == "1"

    query = FoodItem.query

    if not show_out_of_stock:
        query = query.filter(FoodItem.availability_status == "available")

    if q:
        query = query.filter(
            or_(
                FoodItem.name.ilike(f"%{q}%"),
                FoodItem.description.ilike(f"%{q}%")
            )
        )

    if selected_diet_type:
        query = query.filter(FoodItem.diet_type == selected_diet_type)

    if max_cal is not None:
        query = query.filter(FoodItem.calories <= max_cal)

    if min_pro is not None:
        query = query.filter(FoodItem.protein >= min_pro)

    if max_price is not None:
        query = query.filter(FoodItem.price <= max_price)

    sort_options = {
        "name": FoodItem.name.asc(),
        "cal_asc": FoodItem.calories.asc(),
        "cal_desc": FoodItem.calories.desc(),
        "price": FoodItem.price.asc(),
        "protein": FoodItem.protein.desc(),
    }
    query = query.order_by(sort_options.get(sort_by, FoodItem.name.asc()))

    results = query.paginate(page=page, per_page=12, error_out=False)

    default_diet = ""
    if not selected_diet_type and getattr(current_user, "dietary_preference", None):
        pref = (current_user.dietary_preference.diet_type or "").lower()
        if pref in VALID_DIET_TYPES:
            default_diet = pref

    total_providers = db.session.query(FoodItem.provider_id).distinct().count()

    favorite_items = []
    favorite_count = 0
    recent_views = []
    recent_view_count = 0

    if not current_user.is_food_provider():
        favorite_items = (
            FavoriteFood.query
            .filter_by(user_id=current_user.id)
            .order_by(FavoriteFood.created_at.desc())
            .limit(3)
            .all()
        )
        favorite_count = (
            FavoriteFood.query
            .filter_by(user_id=current_user.id)
            .count()
        )

        recent_views = (
            RecentlyViewed.query
            .filter_by(user_id=current_user.id)
            .order_by(RecentlyViewed.viewed_at.desc())
            .limit(3)
            .all()
        )
        recent_view_count = (
            RecentlyViewed.query
            .filter_by(user_id=current_user.id)
            .count()
        )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        cards_html = render_template(
            "partials/food_cards.html",
            foods=results.items
        )
        return jsonify({
            "html": cards_html,
            "total": results.total,
            "has_next": results.has_next,
            "page": results.page,
            "food_ids": [f.id for f in results.items],
        })

    return render_template(
        "food/search.html",
        foods=results,
        q=q,
        diet_type=selected_diet_type or default_diet,
        selected_diet_types=[selected_diet_type] if selected_diet_type else [],
        max_cal=max_cal,
        min_pro=min_pro,
        max_price=max_price,
        sort_by=sort_by,
        show_out_of_stock=show_out_of_stock,
        total_providers=total_providers,
        favorite_items=favorite_items,
        favorite_count=favorite_count,
        recent_views=recent_views,
        recent_view_count=recent_view_count,
    )