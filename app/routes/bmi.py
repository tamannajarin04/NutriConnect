# app/routes/bmi.py

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.models import db, BMIRecord

bmi_bp = Blueprint("bmi", __name__)


@bmi_bp.route("/bmi", methods=["GET", "POST"])
@login_required
def bmi():
    result = None

    if request.method == "POST":
        try:
            height = float(request.form["height"])  # meters
            weight = float(request.form["weight"])  # kg

            # --- Validation ---
            if height <= 0 or weight <= 0:
                flash("Height and weight must be positive numbers.", "danger")
                return redirect(url_for("bmi.bmi"))

            if height > 3.0 or height < 0.5:
                flash("Please enter a valid height between 0.5m and 3.0m.", "danger")
                return redirect(url_for("bmi.bmi"))

            # --- Calculate BMI ---
            bmi_value = round(weight / (height ** 2), 2)

            # --- Category ---
            if bmi_value < 18.5:
                category = "Underweight"
            elif bmi_value < 25:
                category = "Normal"
            elif bmi_value < 30:
                category = "Overweight"
            else:
                category = "Obese"

            # --- Save to DB ---
            record = BMIRecord(
                user_id=current_user.id,
                height=height,
                weight=weight,
                bmi=bmi_value,
                category=category
            )
            db.session.add(record)
            db.session.commit()

            result = {
                "bmi": bmi_value,
                "category": category,
                "height": height,
                "weight": weight
            }

        except ValueError:
            flash("Please enter valid numbers.", "danger")
            return redirect(url_for("bmi.bmi"))

    # Load last 5 records for this user
    past_records = (
        BMIRecord.query
        .filter_by(user_id=current_user.id)
        .order_by(BMIRecord.recorded_at.desc())
        .limit(5)
        .all()
    )

    return render_template("bmi.html", result=result, past_records=past_records)