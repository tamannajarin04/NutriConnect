from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import text
from app.models import db, BMIRecord

bmi_bp = Blueprint("bmi", __name__)


@bmi_bp.route("/bmi", methods=["GET", "POST"])
@login_required
def bmi():
    result = None

    if request.method == "POST":
        height_raw = (request.form.get("height") or "").strip()
        weight_raw = (request.form.get("weight") or "").strip()

        if not height_raw or not weight_raw:
            flash("Please enter both height and weight.", "danger")
            return redirect(url_for("bmi.bmi"))

        try:
            height = float(height_raw)
            weight = float(weight_raw)
        except ValueError:
            flash("Please enter valid numbers.", "danger")
            return redirect(url_for("bmi.bmi"))

        if height <= 0 or weight <= 0:
            flash("Height and weight must be positive numbers.", "danger")
            return redirect(url_for("bmi.bmi"))

        if not 0.5 <= height <= 3.0:
            flash("Please enter a valid height between 0.5m and 3.0m.", "danger")
            return redirect(url_for("bmi.bmi"))

        bmi_value = round(weight / (height ** 2), 2)

        if bmi_value < 18.5:
            category = "Underweight"
        elif bmi_value < 25:
            category = "Normal"
        elif bmi_value < 30:
            category = "Overweight"
        else:
            category = "Obese"

        record = BMIRecord(
            user_id=current_user.id,
            height=height,
            weight=weight,
            bmi=bmi_value,
            category=category
        )

        try:
            db.session.add(record)
            db.session.commit()
            flash("BMI calculated successfully.", "success")
        except Exception:
            db.session.rollback()
            flash("BMI was calculated, but the record could not be saved.", "warning")

        result = {
            "bmi": bmi_value,
            "category": category,
            "height": height,
            "weight": weight
        }

    past_records = (
        BMIRecord.query
        .filter_by(user_id=current_user.id)
        .order_by(BMIRecord.recorded_at.desc())
        .limit(5)
        .all()
    )

    recent_notifs = []
    notif_count = 0

    notifications_query = getattr(current_user, "notifications", None)
    if notifications_query is not None and hasattr(notifications_query, "order_by"):
        try:
            recent_notifs = notifications_query.order_by(text("created_at desc")).limit(10).all()
        except Exception:
            recent_notifs = []

    try:
        notif_count = int(getattr(current_user, "unread_notifications_count", 0) or 0)
    except Exception:
        notif_count = 0

    return render_template(
        "bmi.html",
        result=result,
        past_records=past_records,
        recent_notifs=recent_notifs,
        notif_count=notif_count
    )