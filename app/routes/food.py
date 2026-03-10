from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from functools import wraps
import os

from app.models import db, FoodItem

food_bp = Blueprint("food", __name__)

UPLOAD_FOLDER = "app/static/uploads"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


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
    return render_template("dashboard/food_provider_dashboard.html", foods=foods)


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
        name = request.form.get("name", "").strip()
        if not name:
            flash("Food name is required.", "danger")
            return render_template("dashboard/edit_food.html", food=food)

        food.name        = name
        food.description = request.form.get("description", "").strip()
        food.price       = float(request.form["price"])    if request.form.get("price")    else None
        food.calories    = float(request.form["calories"]) if request.form.get("calories") else None
        food.protein     = float(request.form["protein"])  if request.form.get("protein")  else None
        food.carbs       = float(request.form["carbs"])    if request.form.get("carbs")    else None
        food.fat         = float(request.form["fat"])      if request.form.get("fat")      else None

        # Handle optional image replacement
        image_file = request.files.get("image")
        if image_file and image_file.filename != "":
            if not allowed_file(image_file.filename):
                flash("Invalid image format.", "danger")
                return render_template("dashboard/edit_food.html", food=food)
            filename = secure_filename(image_file.filename)
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            image_file.save(os.path.join(UPLOAD_FOLDER, filename))
            food.image = filename

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