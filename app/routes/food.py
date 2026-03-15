from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from functools import wraps
import os
from sqlalchemy import or_, func
from datetime import datetime

from app.models import db, FoodItem, User, FoodView

food_bp = Blueprint("food", __name__)
food_search_bp = Blueprint("food_search", __name__)

UPLOAD_FOLDER = "app/static/uploads"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

VALID_DIET_TYPES = {
    'vegan', 'vegetarian', 'keto', 'paleo',
    'gluten-free', 'dairy-free', 'halal', 'low-carb', 'high-protein'
}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def food_provider_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_food_provider() and not current_user.is_admin():
            flash("Access denied. Food provider role required.", "danger")
            return redirect(url_for("main.index"))
        return f(*args, **kwargs)
    return decorated_function


# ── Food Provider Dashboard ───────────────────────────────────────────────────
@food_bp.route("/foods")
@login_required
@food_provider_required
def provider_foods():
    foods = FoodItem.query.filter_by(provider_id=current_user.id).all()

    # Build popularity insights: view count per food item
    view_counts = dict(
        db.session.query(FoodView.food_id, func.count(FoodView.id))
        .filter(FoodView.food_id.in_([f.id for f in foods]))
        .group_by(FoodView.food_id)
        .all()
    ) if foods else {}

    # Attach view count to each food and sort by popularity
    insights = sorted(
        [{"food": f, "views": view_counts.get(f.id, 0)} for f in foods],
        key=lambda x: x["views"],
        reverse=True
    )

    total_views = sum(view_counts.values()) if view_counts else 0

    return render_template(
        "dashboard/food_provider_dashboard.html",
        foods=foods,
        insights=insights,
        total_views=total_views,
    )


# ── Track Food View (one view per user per day) ───────────────────────────────
@food_search_bp.route("/view/<int:food_id>", methods=["POST"])
@login_required
def track_view(food_id):
    today = datetime.utcnow().date()

    already_viewed = FoodView.query.filter(
        FoodView.food_id   == food_id,
        FoodView.viewer_id == current_user.id,
        db.func.date(FoodView.viewed_at) == today
    ).first()

    if not already_viewed:
        view = FoodView(food_id=food_id, viewer_id=current_user.id)
        db.session.add(view)
        db.session.commit()

    return jsonify({"ok": True})


# ── Add Food ──────────────────────────────────────────────────────────────────
@food_bp.route("/foods/add", methods=["GET", "POST"])
@login_required
@food_provider_required
def add_food():
    if request.method == "POST":
        name        = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        price       = request.form.get("price") or None
        calories    = request.form.get("calories") or None
        protein     = request.form.get("protein") or None
        carbs       = request.form.get("carbs") or None
        fat         = request.form.get("fat") or None
        diet_type   = request.form.get("diet_type", "").strip() or None

        if not name:
            flash("Food name is required.", "danger")
            return render_template("dashboard/add_food.html")

        filename = None
        image_file = request.files.get("image")
        if image_file and image_file.filename != "":
            if not allowed_file(image_file.filename):
                flash("Invalid image format. Allowed: png, jpg, jpeg, gif, webp", "danger")
                return render_template("dashboard/add_food.html")
            filename = secure_filename(image_file.filename)
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            image_file.save(os.path.join(UPLOAD_FOLDER, filename))

        food = FoodItem(
            name=name,
            description=description,
            diet_type=diet_type,
            price=float(price) if price else None,
            calories=float(calories) if calories else None,
            protein=float(protein) if protein else None,
            carbs=float(carbs) if carbs else None,
            fat=float(fat) if fat else None,
            image=filename,
            provider_id=current_user.id,
        )
        db.session.add(food)
        db.session.commit()

        flash("Food item added successfully!", "success")
        return redirect(url_for("food.provider_foods"))

    return render_template("dashboard/add_food.html")


# ── Edit Food ─────────────────────────────────────────────────────────────────
@food_bp.route("/foods/edit/<int:id>", methods=["GET", "POST"])
@login_required
@food_provider_required
def edit_food(id):
    food = FoodItem.query.get_or_404(id)

    if food.provider_id != current_user.id:
        flash("Unauthorized access.", "danger")
        return redirect(url_for("food.provider_foods"))

    if request.method == "POST":
        print("POST RECEIVED")
        print(request.form)
        name = request.form.get("name", "").strip()
        if not name:
            flash("Food name is required.", "danger")
            return render_template("dashboard/edit_food.html", food=food)

        food.name        = name
        food.description = request.form.get("description", "").strip()
        food.diet_type   = request.form.get("diet_type", "").strip() or None
        food.price       = float(request.form["price"])    if request.form.get("price")    else None
        food.calories    = float(request.form["calories"]) if request.form.get("calories") else None
        food.protein     = float(request.form["protein"])  if request.form.get("protein")  else None
        food.carbs       = float(request.form["carbs"])    if request.form.get("carbs")    else None
        food.fat         = float(request.form["fat"])      if request.form.get("fat")      else None

        image_file = request.files.get("image")
        if image_file and image_file.filename != "":
            if not allowed_file(image_file.filename):
                flash("Invalid image format.", "danger")
                return render_template("dashboard/edit_food.html", food=food)
            filename = secure_filename(image_file.filename)
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            image_file.save(os.path.join(UPLOAD_FOLDER, filename))
            food.image = filename

        db.session.add(food)
        db.session.commit()
        flash("Food item updated successfully!", "success")
        return redirect(url_for("food.provider_foods"))

    return render_template("dashboard/edit_food.html", food=food)


# ── Delete Food ───────────────────────────────────────────────────────────────
@food_bp.route("/foods/delete/<int:id>", methods=["POST"])
@login_required
@food_provider_required
def delete_food(id):
    food = FoodItem.query.get_or_404(id)

    if food.provider_id != current_user.id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("food.provider_foods"))

    db.session.delete(food)
    db.session.commit()
    flash("Food item deleted.", "success")
    return redirect(url_for("food.provider_foods"))


# ── Food Search (all logged-in users) ────────────────────────────────────────
@food_search_bp.route("/search")
@login_required
def search_foods():
    q         = request.args.get("q", "").strip()
    diet_type = request.args.get("diet_type", "").strip()
    max_cal   = request.args.get("max_cal",     type=float)
    min_pro   = request.args.get("min_protein", type=float)
    max_price = request.args.get("max_price",   type=float)
    sort_by   = request.args.get("sort", "name")
    page      = request.args.get("page", 1, type=int)

    query = FoodItem.query

    if q:
        query = query.filter(
            or_(
                FoodItem.name.ilike(f"%{q}%"),
                FoodItem.description.ilike(f"%{q}%")
            )
        )

    if diet_type:
        query = query.filter(FoodItem.diet_type == diet_type)
    if max_cal:
        query = query.filter(FoodItem.calories <= max_cal)
    if min_pro:
        query = query.filter(FoodItem.protein >= min_pro)
    if max_price:
        query = query.filter(FoodItem.price <= max_price)

    sort_options = {
        "name":     FoodItem.name.asc(),
        "cal_asc":  FoodItem.calories.asc(),
        "cal_desc": FoodItem.calories.desc(),
        "price":    FoodItem.price.asc(),
        "protein":  FoodItem.protein.desc(),
    }
    query = query.order_by(sort_options.get(sort_by, FoodItem.name.asc()))

    results = query.paginate(page=page, per_page=12, error_out=False)

    # Only apply user's diet preference if it's a valid filter value
    default_diet = ""
    if not diet_type and current_user.dietary_preference:
        pref = current_user.dietary_preference.diet_type or ""
        if pref.lower() in VALID_DIET_TYPES:
            default_diet = pref

    # Count only providers who have actually posted at least one food item
    total_providers = db.session.query(FoodItem.provider_id).distinct().count()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        cards_html = render_template(
            "partials/food_cards.html",
            foods=results.items
        )
        return jsonify({
            "html":     cards_html,
            "total":    results.total,
            "has_next": results.has_next,
            "page":     results.page,
            "food_ids": [f.id for f in results.items],
        })

    return render_template(
        "food/search.html",
        foods=results,
        q=q,
        diet_type=diet_type or default_diet,
        max_cal=max_cal,
        min_pro=min_pro,
        max_price=max_price,
        sort_by=sort_by,
        total_providers=total_providers,
    )