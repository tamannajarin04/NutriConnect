import os
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app.models import db, User, DietaryPreference, BMIRecord  # ✅ added BMIRecord

user_dashboard_bp = Blueprint("user_dashboard", __name__)

@user_dashboard_bp.route("/")
@login_required
def index():
    if current_user.is_user():
        # ✅ BMI data fetched here — not in the template
        latest_bmi = BMIRecord.query.filter_by(user_id=current_user.id)\
                     .order_by(BMIRecord.recorded_at.desc()).first()

        bmi_records = BMIRecord.query.filter_by(user_id=current_user.id)\
                      .order_by(BMIRecord.recorded_at.desc()).limit(3).all()

        return render_template("dashboard/user_dashboard.html",
            user=current_user,
            latest_bmi=latest_bmi,
            bmi_records=bmi_records
        )

    if current_user.is_food_provider():
        return render_template("dashboard/food_provider_dashboard.html", user=current_user)

    if current_user.is_admin():
        return render_template("dashboard/admin_dashboard.html", user=current_user)

    flash("No role assigned. Please contact admin.", "warning")
    return redirect(url_for("auth.logout"))


@user_dashboard_bp.route("/profile")
@login_required
def view_profile():
    return render_template("dashboard/profile.html", user=current_user)


@user_dashboard_bp.route("/profile/edit", methods=["GET", "POST"])
@login_required
def edit_profile():
    if request.method == "POST":
        current_user.first_name = (request.form.get("first_name") or "").strip()
        current_user.last_name = (request.form.get("last_name") or "").strip()

        new_email = (request.form.get("email") or "").strip().lower()
        if new_email and new_email != current_user.email:
            existing = User.query.filter_by(email=new_email).first()
            if existing:
                flash("Email already in use by another account.", "danger")
                return redirect(url_for("user_dashboard.edit_profile"))
            current_user.email = new_email

        file = request.files.get("profile_picture")
        if file and file.filename:
            allowed = {"png", "jpg", "jpeg", "gif"}
            ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""

            if ext not in allowed:
                flash("Invalid image type. Use PNG/JPG/JPEG/GIF only.", "danger")
                return redirect(url_for("user_dashboard.edit_profile"))

            filename = secure_filename(f"{current_user.id}_{file.filename}")

            upload_dir = os.path.join(
                current_app.root_path,
                "static",
                "uploads",
                "profiles"
            )

            if os.path.exists(upload_dir) and not os.path.isdir(upload_dir):
                raise RuntimeError("'profiles' exists but is not a directory")

            os.makedirs(upload_dir, exist_ok=True)

            file.save(os.path.join(upload_dir, filename))
            current_user.profile_picture = f"uploads/profiles/{filename}"

        db.session.commit()
        flash("Profile updated successfully!", "success")
        return redirect(url_for("user_dashboard.view_profile"))

    return render_template("dashboard/edit_profile.html", user=current_user)


@user_dashboard_bp.route("/dietary-preferences", methods=["GET", "POST"])
@login_required
def dietary_preferences():
    if not current_user.is_user():
        flash("Dietary preferences are only available for regular users.", "warning")
        return redirect(url_for("user_dashboard.index"))

    preference = current_user.dietary_preference

    if request.method == "POST":
        diet_type = request.form.get("diet_type") or None
        food_restrictions = request.form.getlist("food_restrictions") or []
        allergies = request.form.getlist("allergies") or []
        preferred_cuisine = request.form.getlist("preferred_cuisine") or []

        avoid_foods = [x.strip() for x in (request.form.get("avoid_foods") or "").split(",") if x.strip()]
        favorite_foods = [x.strip() for x in (request.form.get("favorite_foods") or "").split(",") if x.strip()]

        meals_per_day_raw = request.form.get("meals_per_day") or "3"
        try:
            meals_per_day = int(meals_per_day_raw)
        except:
            meals_per_day = 3

        def to_int(v):
            v = (v or "").strip()
            return int(v) if v.isdigit() else None

        def to_float(v):
            v = (v or "").strip()
            try:
                return float(v) if v else None
            except:
                return None

        calorie_goal = to_int(request.form.get("calorie_goal"))
        protein_goal = to_float(request.form.get("protein_goal"))
        carbs_goal = to_float(request.form.get("carbs_goal"))
        fat_goal = to_float(request.form.get("fat_goal"))

        if not preference:
            preference = DietaryPreference(user_id=current_user.id)
            db.session.add(preference)

        preference.diet_type = diet_type
        preference.food_restrictions = food_restrictions
        preference.allergies = allergies
        preference.preferred_cuisine = preferred_cuisine
        preference.avoid_foods = avoid_foods
        preference.favorite_foods = favorite_foods
        preference.meals_per_day = meals_per_day
        preference.calorie_goal = calorie_goal
        preference.protein_goal = protein_goal
        preference.carbs_goal = carbs_goal
        preference.fat_goal = fat_goal

        db.session.commit()
        flash("Dietary preferences updated successfully!", "success")
        return redirect(url_for("user_dashboard.view_profile"))

    return render_template("dashboard/dietary_preferences.html", preference=preference)
