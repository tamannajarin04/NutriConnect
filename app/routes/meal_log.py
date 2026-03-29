from collections import defaultdict
from datetime import datetime, date, timedelta
from difflib import SequenceMatcher
import os
import re
from typing import List, Optional

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user

from app.models import db, MealLog, MEAL_GOAL_CHOICES, FoodItem, BMIRecord

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None

meal_log_bp = Blueprint("meal_log", __name__)


DAY_START_HOUR = 5
MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"]
GOAL_LABELS = {
    "weight_loss": "Weight Loss",
    "weight_gain": "Weight Gain",
    "maintain_weight": "Maintain Weight",
}


# ---------------------------
# Helper: "Logical Today"
# ---------------------------
def get_logical_today() -> date:
    now = datetime.now()
    if now.hour < DAY_START_HOUR:
        return now.date() - timedelta(days=1)
    return now.date()


# ---------------------------
# Shared helpers
# ---------------------------
def parse_selected_date(raw_value: Optional[str]) -> date:
    logical_today = get_logical_today()
    try:
        selected_date = datetime.strptime(raw_value, "%Y-%m-%d").date() if raw_value else logical_today
    except (TypeError, ValueError):
        selected_date = logical_today

    if selected_date > logical_today:
        return logical_today
    return selected_date


def normalize_text(value: Optional[str]) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


NUMBER_WORDS = {
    "half": 0.5,
    "one": 1.0,
    "two": 2.0,
    "three": 3.0,
    "four": 4.0,
    "five": 5.0,
}


def parse_quantity_multiplier(quantity_text: Optional[str]) -> float:
    text = (quantity_text or "").strip().lower()
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if match:
        value = float(match.group(1))
        return value if value > 0 else 1.0

    for word, value in NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", text):
            return value

    return 1.0


def auto_match_food_item(food_name: str, foods: List[FoodItem]) -> Optional[FoodItem]:
    if not food_name or not foods:
        return None

    target = normalize_text(food_name)
    if not target:
        return None

    exact_match = next((food for food in foods if normalize_text(food.name) == target), None)
    if exact_match:
        return exact_match

    contains_match = next(
        (
            food
            for food in foods
            if target in normalize_text(food.name) or normalize_text(food.name) in target
        ),
        None,
    )
    if contains_match:
        return contains_match

    best_food = None
    best_score = 0.0

    for food in foods:
        score = SequenceMatcher(None, target, normalize_text(food.name)).ratio()
        if score > best_score:
            best_score = score
            best_food = food

    return best_food if best_score >= 0.58 else None



def build_nutrition_rows(logs: List[MealLog], foods: List[FoodItem], form_data=None):
    rows = []
    meal_totals = defaultdict(lambda: {"calories": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0})
    totals = {"calories": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0}
    unresolved_count = 0

    foods_by_id = {food.id: food for food in foods}

    for log in logs:
        matched_food = auto_match_food_item(log.food_name, foods)
        auto_food_id = matched_food.id if matched_food else ""
        selected_food_id = auto_food_id

        if form_data is not None:
            selected_food_id = (form_data.get(f"food_id_{log.id}") or "").strip()

        chosen_food = foods_by_id.get(int(selected_food_id)) if str(selected_food_id).isdigit() else matched_food

        default_multiplier = parse_quantity_multiplier(log.quantity)
        multiplier = default_multiplier
        if form_data is not None:
            raw_multiplier = (form_data.get(f"multiplier_{log.id}") or "").strip()
            try:
                parsed_multiplier = float(raw_multiplier)
                if parsed_multiplier > 0:
                    multiplier = parsed_multiplier
            except (TypeError, ValueError):
                multiplier = default_multiplier

        calories = (chosen_food.calories or 0.0) * multiplier if chosen_food else 0.0
        protein = (chosen_food.protein or 0.0) * multiplier if chosen_food else 0.0
        carbs = (chosen_food.carbs or 0.0) * multiplier if chosen_food else 0.0
        fat = (chosen_food.fat or 0.0) * multiplier if chosen_food else 0.0

        if chosen_food:
            totals["calories"] += calories
            totals["protein"] += protein
            totals["carbs"] += carbs
            totals["fat"] += fat

            meal_totals[log.meal_type]["calories"] += calories
            meal_totals[log.meal_type]["protein"] += protein
            meal_totals[log.meal_type]["carbs"] += carbs
            meal_totals[log.meal_type]["fat"] += fat
        else:
            unresolved_count += 1

        rows.append(
            {
                "log": log,
                "chosen_food": chosen_food,
                "auto_matched_food": matched_food,
                "multiplier": round(multiplier, 2),
                "calories": round(calories, 2),
                "protein": round(protein, 2),
                "carbs": round(carbs, 2),
                "fat": round(fat, 2),
            }
        )

    rounded_meal_totals = {
        meal_type: {
            "calories": round(values["calories"], 2),
            "protein": round(values["protein"], 2),
            "carbs": round(values["carbs"], 2),
            "fat": round(values["fat"], 2),
        }
        for meal_type, values in meal_totals.items()
    }

    totals = {key: round(value, 2) for key, value in totals.items()}
    return rows, totals, rounded_meal_totals, unresolved_count



def calculate_baseline_goal_timeline(current_weight: float, target_weight: float, daily_calories: float):
    delta = round(abs(target_weight - current_weight), 2)

    if delta == 0:
        return {
            "goal_type": "maintain_weight",
            "goal_label": GOAL_LABELS["maintain_weight"],
            "weight_delta": 0.0,
            "weekly_change_min": 0.0,
            "weekly_change_max": 0.0,
            "weeks_min": 0.0,
            "weeks_max": 0.0,
            "summary": "You are already at your target weight, so the focus is maintaining your current routine.",
        }

    if target_weight < current_weight:
        goal_type = "weight_loss"
        goal_label = GOAL_LABELS[goal_type]
        if daily_calories <= 1400:
            weekly_change_min, weekly_change_max = 0.45, 0.90
        elif daily_calories <= 1900:
            weekly_change_min, weekly_change_max = 0.30, 0.70
        else:
            weekly_change_min, weekly_change_max = 0.20, 0.45
    else:
        goal_type = "weight_gain"
        goal_label = GOAL_LABELS[goal_type]
        if daily_calories >= 2800:
            weekly_change_min, weekly_change_max = 0.35, 0.65
        elif daily_calories >= 2200:
            weekly_change_min, weekly_change_max = 0.25, 0.50
        else:
            weekly_change_min, weekly_change_max = 0.10, 0.30

    weeks_min = round(delta / weekly_change_max, 1) if weekly_change_max > 0 else 0.0
    weeks_max = round(delta / weekly_change_min, 1) if weekly_change_min > 0 else 0.0

    return {
        "goal_type": goal_type,
        "goal_label": goal_label,
        "weight_delta": delta,
        "weekly_change_min": weekly_change_min,
        "weekly_change_max": weekly_change_max,
        "weeks_min": weeks_min,
        "weeks_max": weeks_max,
        "summary": (
            f"Based on a general healthy pace of {weekly_change_min:.2f}–{weekly_change_max:.2f} kg per week, "
            f"your goal may take around {weeks_min:.1f}–{weeks_max:.1f} weeks."
        ),
    }



def estimate_goal_timeline_with_ai(current_weight: float, target_weight: float, daily_calories: float, baseline: dict):
    if OpenAI is None:
        return None, "OpenAI SDK is not installed."

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None, "OPENAI_API_KEY is not set."

    model = current_app.config.get("OPENAI_DIET_MODEL") or os.environ.get("OPENAI_DIET_MODEL") or "gpt-5.4"

    latest_bmi = (
        BMIRecord.query.filter_by(user_id=current_user.id)
        .order_by(BMIRecord.recorded_at.desc())
        .first()
    )
    calorie_goal = getattr(getattr(current_user, "dietary_preference", None), "calorie_goal", None)

    context_bits = [
        f"Current weight: {current_weight} kg",
        f"Target weight: {target_weight} kg",
        f"Daily calorie intake: {daily_calories} kcal/day",
        f"Baseline healthy estimate: {baseline['weeks_min']} to {baseline['weeks_max']} weeks",
        f"Goal type: {baseline['goal_label']}",
    ]

    if latest_bmi:
        context_bits.append(
            f"Most recent BMI context: height {latest_bmi.height} m, BMI {latest_bmi.bmi}, category {latest_bmi.category}"
        )
    if calorie_goal:
        context_bits.append(f"Stored calorie goal in profile: {calorie_goal} kcal/day")

    prompt = (
        "You are a careful nutrition and fitness planning assistant. "
        "Give an approximate time estimate only, not medical advice. "
        "Use a practical, realistic tone. "
        "Keep the answer under 160 words and include: "
        "(1) an estimated time range in weeks, "
        "(2) the main assumption behind that range, "
        "(3) one short caution that real results vary.\n\n"
        + "\n".join(context_bits)
    )

    try:
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=model,
            input=prompt,
        )
        text = (getattr(response, "output_text", "") or "").strip()
        if not text:
            return None, "The AI model returned an empty response."
        return text, None
    except Exception as exc:  # pragma: no cover
        return None, str(exc)


# ---------------------------
# View meal logs for a date
# ---------------------------
@meal_log_bp.route("/", methods=["GET"])
@login_required
def index():
    selected_date = parse_selected_date(request.args.get("date"))

    logs = (
        MealLog.query.filter_by(user_id=current_user.id, log_date=selected_date)
        .order_by(MealLog.logged_at.asc())
        .all()
    )

    grouped = {meal_type: [] for meal_type in MEAL_TYPES}
    for log in logs:
        grouped.setdefault(log.meal_type, []).append(log)

    return render_template(
        "meal_log/index.html",
        grouped=grouped,
        selected_date=selected_date,
        today=get_logical_today(),
        timedelta=timedelta,
        goal_choices=MEAL_GOAL_CHOICES,
    )


# ---------------------------
# Add a meal log entry
# ---------------------------
@meal_log_bp.route("/add", methods=["POST"])
@login_required
def add():
    food_name = request.form.get("food_name", "").strip()
    meal_type = request.form.get("meal_type", "").strip()
    quantity = request.form.get("quantity", "").strip()
    log_date_str = request.form.get("log_date", "").strip()
    goal = request.form.get("goal", "").strip()

    log_date = parse_selected_date(log_date_str)

    if not food_name:
        flash("Please enter the food name.", "danger")
        return redirect(url_for("meal_log.index", date=log_date.strftime("%Y-%m-%d")))

    if meal_type not in MEAL_TYPES:
        flash("Please select a valid meal type.", "danger")
        return redirect(url_for("meal_log.index", date=log_date.strftime("%Y-%m-%d")))

    if not quantity:
        flash("Please enter the quantity.", "danger")
        return redirect(url_for("meal_log.index", date=log_date.strftime("%Y-%m-%d")))

    if goal and goal not in MEAL_GOAL_CHOICES:
        flash("Please select a valid goal.", "danger")
        return redirect(url_for("meal_log.index", date=log_date.strftime("%Y-%m-%d")))

    log = MealLog(
        user_id=current_user.id,
        food_name=food_name,
        meal_type=meal_type,
        quantity=quantity,
        log_date=log_date,
        logged_at=datetime.utcnow(),
        goal=goal if goal else None,
    )

    db.session.add(log)
    db.session.commit()

    flash("Meal logged successfully!", "success")
    return redirect(url_for("meal_log.index", date=log_date.strftime("%Y-%m-%d")))


# ---------------------------
# Delete a meal log entry
# ---------------------------
@meal_log_bp.route("/delete/<int:log_id>", methods=["POST"])
@login_required
def delete(log_id):
    log = MealLog.query.filter_by(id=log_id, user_id=current_user.id).first_or_404()
    log_date = log.log_date
    db.session.delete(log)
    db.session.commit()
    flash("Meal entry removed.", "info")
    return redirect(url_for("meal_log.index", date=log_date.strftime("%Y-%m-%d")))


# ---------------------------
# Nutrition analyzer
# ---------------------------
@meal_log_bp.route("/nutrition-analyzer", methods=["GET", "POST"])
@login_required
def nutrition_analyzer():
    selected_date_raw = request.form.get("selected_date") if request.method == "POST" else request.args.get("date")
    selected_date = parse_selected_date(selected_date_raw)

    logs = (
        MealLog.query.filter_by(user_id=current_user.id, log_date=selected_date)
        .order_by(MealLog.logged_at.asc())
        .all()
    )
    food_items = FoodItem.query.order_by(FoodItem.name.asc()).all()

    rows, totals, meal_totals, unresolved_count = build_nutrition_rows(
        logs=logs,
        foods=food_items,
        form_data=request.form if request.method == "POST" else None,
    )

    return render_template(
        "meal_log/nutrition_analyzer.html",
        selected_date=selected_date,
        today=get_logical_today(),
        timedelta=timedelta,
        rows=rows,
        totals=totals,
        meal_totals=meal_totals,
        unresolved_count=unresolved_count,
        food_items=food_items,
        total_entries=len(logs),
    )


# ---------------------------
# AI goal timeline estimator
# ---------------------------
@meal_log_bp.route("/goal-time-estimator", methods=["GET", "POST"])
@login_required
def goal_time_estimator():
    result = None
    ai_text = None
    ai_error = None

    if request.method == "POST":
        current_weight_raw = (request.form.get("current_weight") or "").strip()
        target_weight_raw = (request.form.get("target_weight") or "").strip()
        daily_calories_raw = (request.form.get("daily_calorie_intake") or "").strip()

        try:
            current_weight = float(current_weight_raw)
            target_weight = float(target_weight_raw)
            daily_calories = float(daily_calories_raw)
        except ValueError:
            flash("Please enter valid numeric values for all fields.", "danger")
            return render_template("meal_log/goal_time_estimator.html", result=result, ai_text=ai_text, ai_error=ai_error)

        if current_weight <= 0 or target_weight <= 0 or daily_calories <= 0:
            flash("All values must be positive numbers.", "danger")
            return render_template("meal_log/goal_time_estimator.html", result=result, ai_text=ai_text, ai_error=ai_error)

        result = calculate_baseline_goal_timeline(current_weight, target_weight, daily_calories)
        result["current_weight"] = current_weight
        result["target_weight"] = target_weight
        result["daily_calorie_intake"] = daily_calories

        ai_text, ai_error = estimate_goal_timeline_with_ai(
            current_weight=current_weight,
            target_weight=target_weight,
            daily_calories=daily_calories,
            baseline=result,
        )

        if ai_error and not ai_text:
            flash("AI estimate could not be generated, so a baseline estimate is shown instead.", "warning")

    return render_template(
        "meal_log/goal_time_estimator.html",
        result=result,
        ai_text=ai_text,
        ai_error=ai_error,
    )