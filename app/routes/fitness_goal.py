from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from app.models import db, FitnessGoal, BMIRecord, MealLog
from datetime import datetime, timedelta
import json

fitness_goal_bp = Blueprint("fitness_goal", __name__)

DAY_START_HOUR = 5


def get_logical_today():
    now = datetime.now()
    if now.hour < DAY_START_HOUR:
        return now.date() - timedelta(days=1)
    return now.date()


def _to_float(value, default=0.0):
    try:
        if value in (None, ""):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_optional_float(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_optional_int(value):
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _get_latest_bmi():
    return (
        BMIRecord.query
        .filter_by(user_id=current_user.id)
        .order_by(BMIRecord.recorded_at.desc())
        .first()
    )


def _get_latest_goal():
    return (
        FitnessGoal.query
        .filter_by(user_id=current_user.id)
        .order_by(FitnessGoal.created_at.desc())
        .first()
    )


def _get_latest_result_dict(latest_goal):
    if not latest_goal or not latest_goal.ai_result_json:
        return None

    try:
        data = json.loads(latest_goal.ai_result_json)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _get_calorie_goal(user, latest_goal=None):
    try:
        dietary_preference = getattr(user, "dietary_preference", None)
        if dietary_preference:
            goal = getattr(dietary_preference, "calorie_goal", None)
            if goal not in (None, ""):
                return int(float(goal))
    except Exception:
        pass

    if latest_goal and getattr(latest_goal, "daily_calories", None) not in (None, ""):
        try:
            return int(float(latest_goal.daily_calories))
        except Exception:
            pass

    return None


def _build_weekly_tracker(user_id, calorie_target):
    logical_today = get_logical_today()
    start_date = logical_today - timedelta(days=6)

    daily_totals = {}
    for i in range(7):
        d = start_date + timedelta(days=i)
        daily_totals[d] = 0.0

    logs = (
        MealLog.query
        .filter(MealLog.user_id == user_id)
        .filter(MealLog.log_date >= start_date, MealLog.log_date <= logical_today)
        .all()
    )

    for log in logs:
        if log.log_date in daily_totals:
            daily_totals[log.log_date] += _to_float(getattr(log, "calories", 0), 0.0)

    tracker = []
    for i in range(7):
        d = start_date + timedelta(days=i)
        tracker.append({
            "date": d.strftime("%Y-%m-%d"),
            "label": d.strftime("%a"),
            "short_date": d.strftime("%d %b"),
            "consumed": round(daily_totals.get(d, 0.0), 1),
            "target": round(float(calorie_target), 1) if calorie_target else 0,
        })

    return tracker


def _build_weight_history(user_id, limit=8):
    rows = (
        BMIRecord.query
        .filter_by(user_id=user_id)
        .order_by(BMIRecord.recorded_at.asc())
        .limit(limit)
        .all()
    )

    history = []
    for row in rows:
        weight = _to_optional_float(getattr(row, "weight", None))
        recorded_at = getattr(row, "recorded_at", None)
        if weight is None or recorded_at is None:
            continue

        history.append({
            "label": recorded_at.strftime("%d %b"),
            "weight": round(weight, 1)
        })

    return history


def _build_form_seed(latest_bmi, latest_goal, latest_result, calorie_goal):
    bmi_weight = _to_optional_float(getattr(latest_bmi, "weight", None)) if latest_bmi else None
    bmi_height_m = _to_optional_float(getattr(latest_bmi, "height", None)) if latest_bmi else None
    bmi_height_cm = int(round(bmi_height_m * 100)) if bmi_height_m else None

    result_current = _to_optional_float((latest_result or {}).get("current_weight"))
    result_target = _to_optional_float((latest_result or {}).get("target_weight"))
    result_calories = _to_optional_int((latest_result or {}).get("daily_calories"))

    goal_target = _to_optional_float(getattr(latest_goal, "target_weight", None)) if latest_goal else None
    goal_age = _to_optional_int(getattr(latest_goal, "age", None)) if latest_goal else None
    goal_activity = _to_optional_float(getattr(latest_goal, "activity_level", None)) if latest_goal else None
    goal_gender = getattr(latest_goal, "gender", None) if latest_goal else None
    goal_daily_calories = _to_optional_int(getattr(latest_goal, "daily_calories", None)) if latest_goal else None

    return {
        "current_weight": bmi_weight if bmi_weight is not None else result_current,
        "target_weight": goal_target if goal_target is not None else result_target,
        "height_cm": bmi_height_cm,
        "age": goal_age,
        "gender": goal_gender or "male",
        "activity_level": goal_activity if goal_activity is not None else 1.375,
        "daily_calories": calorie_goal if calorie_goal is not None else (goal_daily_calories if goal_daily_calories is not None else result_calories),
    }


@fitness_goal_bp.route("/fitness-goal", methods=["GET"])
@login_required
def index():
    latest_bmi = _get_latest_bmi()
    latest_goal = _get_latest_goal()
    latest_result = _get_latest_result_dict(latest_goal)
    calorie_goal = _get_calorie_goal(current_user, latest_goal=latest_goal)

    form_seed = _build_form_seed(latest_bmi, latest_goal, latest_result, calorie_goal)

    if latest_result:
        latest_result["current_weight"] = (
            latest_result.get("current_weight")
            if latest_result.get("current_weight") not in (None, "")
            else form_seed["current_weight"]
        )
        latest_result["target_weight"] = (
            latest_result.get("target_weight")
            if latest_result.get("target_weight") not in (None, "")
            else form_seed["target_weight"]
        )
        latest_result["daily_calories"] = (
            latest_result.get("daily_calories")
            if latest_result.get("daily_calories") not in (None, "")
            else form_seed["daily_calories"]
        )

    weekly_tracker = _build_weekly_tracker(current_user.id, form_seed["daily_calories"])
    weight_history = _build_weight_history(current_user.id)

    return render_template(
        "dashboard/fitness_goal.html",
        latest_bmi=latest_bmi,
        latest_goal=latest_goal,
        latest_result=latest_result,
        calorie_goal=calorie_goal,
        weekly_tracker=weekly_tracker,
        weight_history=weight_history,
        form_seed=form_seed,
    )


@fitness_goal_bp.route("/fitness-goal/estimate", methods=["POST"])
@login_required
def estimate():
    data = request.get_json(silent=True) or {}

    try:
        current_weight = float(data.get("current_weight", 0))
        target_weight = float(data.get("target_weight", 0))
        height_cm = float(data.get("height_cm", 0))
        age = int(data.get("age", 0))
        gender = str(data.get("gender", "male")).strip().lower()
        activity_level = float(data.get("activity_level", 1.375))
        daily_calories = int(data.get("daily_calories", 0))
    except (TypeError, ValueError) as e:
        return jsonify({"success": False, "error": f"Invalid input: {e}"}), 400

    if not all([current_weight, target_weight, height_cm, age, daily_calories]):
        return jsonify({"success": False, "error": "All fields are required."}), 400

    if current_weight <= 0 or target_weight <= 0 or height_cm <= 0 or age <= 0 or daily_calories <= 0:
        return jsonify({"success": False, "error": "All values must be positive."}), 400

    if gender not in ("male", "female"):
        return jsonify({"success": False, "error": "Gender must be 'male' or 'female'."}), 400

    try:
        from app.services.nutrition import estimate_fitness_goal

        result = estimate_fitness_goal(
            current_weight=current_weight,
            target_weight=target_weight,
            height_cm=height_cm,
            age=age,
            gender=gender,
            activity_level=activity_level,
            daily_calories=daily_calories,
        )

        if not isinstance(result, dict):
            return jsonify({"success": False, "error": "Estimator returned an invalid response."}), 500

        # Ensure frontend-required fields are always present
        result["current_weight"] = current_weight
        result["target_weight"] = target_weight
        result["height_cm"] = height_cm
        result["age"] = age
        result["gender"] = gender
        result["activity_level"] = activity_level
        result["daily_calories"] = daily_calories

        record = FitnessGoal(
            user_id=current_user.id,
            current_weight=current_weight,
            target_weight=target_weight,
            height_cm=height_cm,
            age=age,
            gender=gender,
            activity_level=activity_level,
            daily_calories=daily_calories,
            tdee=result.get("tdee"),
            daily_deficit=result.get("daily_balance"),
            ai_result_json=json.dumps(result),
        )
        db.session.add(record)
        db.session.commit()

        return jsonify({
            "success": True,
            "result": result,
            "record_id": record.id
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500