from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from app.models import db, FitnessGoal, BMIRecord
import json

fitness_goal_bp = Blueprint("fitness_goal", __name__)


@fitness_goal_bp.route("/fitness-goal", methods=["GET"])
@login_required
def index():
    """Render the fitness goal estimator page."""
    # Pre-fill from latest BMI record if available
    latest_bmi = (
        BMIRecord.query
        .filter_by(user_id=current_user.id)
        .order_by(BMIRecord.recorded_at.desc())
        .first()
    )

    # Latest saved goal estimate
    latest_goal = (
        FitnessGoal.query
        .filter_by(user_id=current_user.id)
        .order_by(FitnessGoal.created_at.desc())
        .first()
    )

    latest_result = None
    if latest_goal and latest_goal.ai_result_json:
        try:
            latest_result = json.loads(latest_goal.ai_result_json)
        except Exception:
            pass

    # Calorie goal from dietary preferences
    calorie_goal = None
    if current_user.dietary_preference and current_user.dietary_preference.calorie_goal:
        calorie_goal = current_user.dietary_preference.calorie_goal

    return render_template(
        "dashboard/fitness_goal.html",
        latest_bmi=latest_bmi,
        latest_goal=latest_goal,
        latest_result=latest_result,
        calorie_goal=calorie_goal,
    )


@fitness_goal_bp.route("/fitness-goal/estimate", methods=["POST"])
@login_required
def estimate():
    """AJAX: run the fitness goal estimation and return JSON."""
    data = request.get_json(silent=True) or {}

    try:
        current_weight = float(data.get("current_weight", 0))
        target_weight  = float(data.get("target_weight",  0))
        height_cm      = float(data.get("height_cm",      0))
        age            = int(data.get("age",               0))
        gender         = str(data.get("gender",       "male")).lower()
        activity_level = float(data.get("activity_level", 1.375))
        daily_calories = int(data.get("daily_calories",   2000))
    except (TypeError, ValueError) as e:
        return jsonify({"success": False, "error": f"Invalid input: {e}"}), 400

    if not all([current_weight, target_weight, height_cm, age]):
        return jsonify({"success": False, "error": "All fields are required."}), 400

    if current_weight <= 0 or target_weight <= 0 or height_cm <= 0 or age <= 0:
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

        # Persist to DB
        record = FitnessGoal(
            user_id        = current_user.id,
            current_weight = current_weight,
            target_weight  = target_weight,
            height_cm      = height_cm,
            age            = age,
            gender         = gender,
            activity_level = activity_level,
            daily_calories = daily_calories,
            tdee           = result.get("tdee"),
            daily_deficit  = result.get("daily_balance"),
            ai_result_json = json.dumps(result),
        )
        db.session.add(record)
        db.session.commit()

        return jsonify({"success": True, "result": result, "record_id": record.id})

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500