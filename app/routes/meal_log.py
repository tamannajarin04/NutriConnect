from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from app.models import db, MealLog, MEAL_GOAL_CHOICES
from datetime import datetime, date, timedelta
import json

meal_log_bp = Blueprint("meal_log", __name__)

DAY_START_HOUR = 5


def get_logical_today() -> date:
    now = datetime.now()
    if now.hour < DAY_START_HOUR:
        return now.date() - timedelta(days=1)
    return now.date()


def _coerce_float(value, default=0.0):
    try:
        if value in (None, ""):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _get_nutrition_goals_for_user(user):
    """
    Safely collect calorie + macro goals from whatever goal-related object exists.
    Works even if some fields are missing.
    """
    sources = []

    try:
        dietary_preference = getattr(user, "dietary_preference", None)
        if dietary_preference:
            sources.append(dietary_preference)
    except Exception:
        pass

    try:
        fitness_goal = getattr(user, "fitness_goal", None)
        if fitness_goal:
            sources.append(fitness_goal)
    except Exception:
        pass

    try:
        fitness_goals = getattr(user, "fitness_goals", None)
        if fitness_goals:
            if hasattr(fitness_goals, "__iter__"):
                fitness_goals = list(fitness_goals)
                if fitness_goals:
                    sources.append(fitness_goals[-1])
    except Exception:
        pass

    def pick(*names, default=0.0):
        for source in sources:
            for name in names:
                try:
                    value = getattr(source, name, None)
                except Exception:
                    value = None
                if value not in (None, ""):
                    return _coerce_float(value, default)
        return float(default)

    return {
        "calories": pick("calorie_goal", "daily_calories", "target_calories", default=0.0),
        "protein": pick("protein_goal", "daily_protein", "target_protein", "protein_target", default=0.0),
        "carbs": pick("carbs_goal", "carb_goal", "daily_carbs", "target_carbs", "carbs_target", default=0.0),
        "fat": pick("fat_goal", "daily_fat", "target_fat", "fat_target", default=0.0),
    }


@meal_log_bp.route("/", methods=["GET"])
@login_required
def index():
    logical_today = get_logical_today()
    date_str = request.args.get("date")

    try:
        selected_date = (
            datetime.strptime(date_str, "%Y-%m-%d").date()
            if date_str else logical_today
        )
    except ValueError:
        selected_date = logical_today

    if selected_date > logical_today:
        selected_date = logical_today

    logs = (
        MealLog.query
        .filter_by(user_id=current_user.id, log_date=selected_date)
        .order_by(MealLog.logged_at.asc())
        .all()
    )

    grouped = {"breakfast": [], "lunch": [], "dinner": [], "snack": []}
    for log in logs:
        grouped.setdefault(log.meal_type, []).append(log)

    daily_totals = {
        "calories": round(sum(l.calories or 0 for l in logs), 1),
        "protein": round(sum(l.protein or 0 for l in logs), 1),
        "carbs": round(sum(l.carbs or 0 for l in logs), 1),
        "fat": round(sum(l.fat or 0 for l in logs), 1),
    }
    logs_with_nutrition = sum(1 for l in logs if l.has_nutrition)

    goals = _get_nutrition_goals_for_user(current_user)

    return render_template(
        "meal_log/index.html",
        grouped=grouped,
        selected_date=selected_date,
        today=logical_today,
        timedelta=timedelta,
        goal_choices=MEAL_GOAL_CHOICES,
        daily_totals=daily_totals,
        logs_with_nutrition=logs_with_nutrition,
        total_logs=len(logs),
        calorie_goal=goals["calories"] if goals["calories"] > 0 else None,
    )


@meal_log_bp.route("/add", methods=["POST"])
@login_required
def add():
    food_name = request.form.get("food_name", "").strip()
    meal_type = request.form.get("meal_type", "").strip()
    quantity = request.form.get("quantity", "").strip()
    log_date_str = request.form.get("log_date", "").strip()
    goal = request.form.get("goal", "").strip()

    logical_today = get_logical_today()
    try:
        log_date = (
            datetime.strptime(log_date_str, "%Y-%m-%d").date()
            if log_date_str else logical_today
        )
    except ValueError:
        log_date = logical_today

    if not food_name:
        flash("Please enter the food name.", "danger")
        return redirect(url_for("meal_log.index", date=log_date_str))

    if not meal_type or meal_type not in ["breakfast", "lunch", "dinner", "snack"]:
        flash("Please select a valid meal type.", "danger")
        return redirect(url_for("meal_log.index", date=log_date_str))

    if not quantity:
        flash("Please enter the quantity.", "danger")
        return redirect(url_for("meal_log.index", date=log_date_str))

    if goal and goal not in MEAL_GOAL_CHOICES:
        flash("Please select a valid goal.", "danger")
        return redirect(url_for("meal_log.index", date=log_date_str))

    log = MealLog(
        user_id=current_user.id,
        food_name=food_name,
        meal_type=meal_type,
        quantity=quantity,
        log_date=log_date,
        logged_at=datetime.utcnow(),
        goal=goal if goal else None,
    )

    try:
        from app.services.nutrition import get_nutrition, _to_grams

        parts = quantity.strip().split(None, 1)
        try:
            qty_num = float(parts[0])
            qty_unit = parts[1] if len(parts) > 1 else "serving"
        except (ValueError, IndexError):
            qty_num = 1.0
            qty_unit = quantity if quantity else "serving"

        nut = get_nutrition(food_name)
        grams = _to_grams(qty_num, qty_unit, food_name)
        mult = grams / (nut.get("per_g", 100) or 100)

        log.calories = round(nut["calories"] * mult, 1)
        log.protein = round(nut["protein"] * mult, 1)
        log.carbs = round(nut["carbs"] * mult, 1)
        log.fat = round(nut["fat"] * mult, 1)
        log.nutrition_source = nut.get("source", "unknown")
        log.is_ai_estimated = nut.get("source") == "ai_estimate"
        log.nutrition_confidence = (
            "high" if nut.get("source") in ("meal_log_cache", "local_db", "food_items_table")
            else "medium" if nut.get("source") == "open_food_facts"
            else "low"
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Nutrition lookup failed for '{food_name}': {e}")

    db.session.add(log)
    db.session.commit()
    flash("Meal logged successfully!", "success")
    return redirect(url_for("meal_log.index", date=log_date.strftime("%Y-%m-%d")))


@meal_log_bp.route("/nutrition-analyzer", methods=["GET"])
@login_required
def nutrition_analyzer():
    logical_today = get_logical_today()
    goals = _get_nutrition_goals_for_user(current_user)

    return render_template(
        "meal_log/nutrition_analyzer.html",
        today=logical_today,
        calorie_goal=goals["calories"],
        protein_goal=goals["protein"],
        carb_goal=goals["carbs"],
        fat_goal=goals["fat"],
    )


@meal_log_bp.route("/analyze", methods=["POST"])
@login_required
def analyze():
    data = request.get_json(silent=True) or {}
    meal_text = (data.get("meal_text") or "").strip()

    if not meal_text:
        return jsonify({"success": False, "error": "meal_text is required"}), 400

    try:
        from app.services.nutrition import analyze_meal
        result = analyze_meal(meal_text)
        return jsonify({"success": True, **result})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@meal_log_bp.route("/analyze-logs", methods=["POST"])
@login_required
def analyze_logs():
    data = request.get_json(silent=True) or {}
    log_date_str = (data.get("log_date") or get_logical_today().strftime("%Y-%m-%d")).strip()
    meal_type = (data.get("meal_type") or "all").strip().lower()

    if meal_type not in {"all", "breakfast", "lunch", "dinner", "snack"}:
        return jsonify({"success": False, "error": "Invalid meal type."}), 400

    try:
        log_date = datetime.strptime(log_date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"success": False, "error": "Invalid log date."}), 400

    try:
        from app.services.nutrition import analyze_logged_meals

        result = analyze_logged_meals(
            user_id=current_user.id,
            log_date=log_date,
            meal_type=None if meal_type == "all" else meal_type,
        )
        result["goals"] = _get_nutrition_goals_for_user(current_user)
        return jsonify({"success": True, **result})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 404
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@meal_log_bp.route("/save-analyzed", methods=["POST"])
@login_required
def save_analyzed():
    data = request.get_json(silent=True) or {}
    food_name = (data.get("food_name") or "").strip()
    meal_type = (data.get("meal_type") or "lunch").strip()
    quantity = (data.get("quantity") or "1 serving").strip()
    log_date_str = (data.get("log_date") or date.today().strftime("%Y-%m-%d")).strip()
    goal = (data.get("goal") or "").strip()

    if not food_name or meal_type not in ["breakfast", "lunch", "dinner", "snack"]:
        return jsonify({"success": False, "error": "Invalid fields"}), 400

    try:
        log_date = datetime.strptime(log_date_str, "%Y-%m-%d").date()
    except ValueError:
        log_date = date.today()

    log = MealLog(
        user_id=current_user.id,
        food_name=food_name,
        meal_type=meal_type,
        quantity=quantity,
        log_date=log_date,
        logged_at=datetime.utcnow(),
        goal=goal or None,
        calories=data.get("calories"),
        protein=data.get("protein"),
        carbs=data.get("carbs"),
        fat=data.get("fat"),
        nutrition_source=data.get("nutrition_source"),
        is_ai_estimated=bool(data.get("is_ai_estimated", False)),
        nutrition_confidence=data.get("nutrition_confidence"),
        parsed_items_json=json.dumps(data.get("items", [])),
    )
    db.session.add(log)
    db.session.commit()
    return jsonify({"success": True, "log_id": log.id})


@meal_log_bp.route("/delete/<int:log_id>", methods=["POST"])
@login_required
def delete(log_id):
    log = MealLog.query.filter_by(id=log_id, user_id=current_user.id).first_or_404()
    log_date = log.log_date
    db.session.delete(log)
    db.session.commit()
    flash("Meal entry removed.", "info")
    return redirect(url_for("meal_log.index", date=log_date.strftime("%Y-%m-%d")))