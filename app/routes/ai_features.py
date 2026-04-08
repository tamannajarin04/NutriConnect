from datetime import date, datetime, timedelta
from typing import Optional

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.models import BMIRecord, MealLog
from app.services.hybrid_ai import analyze_meal_logs, predict_fitness_goal


ai_features_bp = Blueprint("ai_features", __name__)

DAY_START_HOUR = 5


def get_logical_today() -> date:
    now = datetime.now()
    if now.hour < DAY_START_HOUR:
        return now.date() - timedelta(days=1)
    return now.date()


def parse_selected_date(raw_value: Optional[str]) -> date:
    logical_today = get_logical_today()
    try:
        selected_date = datetime.strptime((raw_value or "").strip(), "%Y-%m-%d").date() if raw_value else logical_today
    except ValueError:
        selected_date = logical_today

    if selected_date > logical_today:
        selected_date = logical_today

    return selected_date


@ai_features_bp.route("/nutrition-analyzer", methods=["GET", "POST"])
@login_required
def nutrition_analyzer():
    logical_today = get_logical_today()

    period = (request.values.get("period") or "specific_date").strip()
    if period not in {"today", "this_week", "specific_date"}:
        period = "specific_date"

    selected_date = parse_selected_date(request.values.get("date"))

    if period == "today":
        start_date = logical_today
        end_date = logical_today
        selected_date = logical_today
        analysis_scope = "Today"

    elif period == "this_week":
        week_start = logical_today - timedelta(days=logical_today.weekday())
        start_date = week_start
        end_date = logical_today
        selected_date = logical_today
        analysis_scope = f"This Week ({start_date.strftime('%d %b')} - {end_date.strftime('%d %b %Y')})"

    else:
        start_date = selected_date
        end_date = selected_date
        analysis_scope = selected_date.strftime("%A, %B %d, %Y")

    logs = (
        MealLog.query
        .filter(MealLog.user_id == current_user.id)
        .filter(MealLog.log_date >= start_date, MealLog.log_date <= end_date)
        .order_by(MealLog.log_date.asc(), MealLog.logged_at.asc())
        .all()
    )

    analysis = None

    # Only calculate when the user clicks the button on nutrition_analyzer.html
    if request.method == "POST":
        if not logs:
            flash("No meal logs found for the selected range.", "warning")
        else:
            analysis = analyze_meal_logs(logs)
            if analysis.get("item_count"):
                flash("Nutrition analysis generated successfully.", "success")
            else:
                flash("The analyzer could not resolve any nutrition values for the selected range.", "warning")

    return render_template(
        "dashboard/nutrition_analyzer.html",
        selected_date=selected_date,
        today=logical_today,
        logs=logs,
        analysis=analysis,
        period=period,
        start_date=start_date,
        end_date=end_date,
        analysis_scope=analysis_scope,
        timedelta=timedelta,
    )


@ai_features_bp.route("/fitness-goal-predictor", methods=["GET", "POST"])
@login_required
def fitness_goal_predictor():
    result = None
    latest_bmi = (
        BMIRecord.query
        .filter_by(user_id=current_user.id)
        .order_by(BMIRecord.recorded_at.desc())
        .first()
    )

    if request.method == "POST":
        current_weight_raw = (request.form.get("current_weight") or "").strip()
        target_weight_raw = (request.form.get("target_weight") or "").strip()
        daily_calorie_raw = (request.form.get("daily_calorie_intake") or "").strip()

        try:
            current_weight = float(current_weight_raw)
            target_weight = float(target_weight_raw)
            daily_calorie_intake = float(daily_calorie_raw)
        except ValueError:
            flash("Please enter valid numbers for all fields.", "danger")
            return redirect(url_for("ai_features.fitness_goal_predictor"))

        if current_weight <= 0 or target_weight <= 0 or daily_calorie_intake <= 0:
            flash("All values must be positive numbers.", "danger")
            return redirect(url_for("ai_features.fitness_goal_predictor"))

        latest_height = latest_bmi.height if latest_bmi else None
        result = predict_fitness_goal(
            current_weight=current_weight,
            target_weight=target_weight,
            daily_calorie_intake=daily_calorie_intake,
            latest_height=latest_height,
        )

        flash("Fitness goal estimate generated successfully.", "success")

    return render_template(
        "dashboard/fitness_goal_predictor.html",
        result=result,
        latest_bmi=latest_bmi,
    )