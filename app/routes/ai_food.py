from flask import Blueprint, jsonify, abort
from flask_login import login_required, current_user

from app.models import FoodItem
from app.services.ai_food_advisor import get_food_advice

ai_food_bp = Blueprint("ai_food", __name__, url_prefix="/food-advice")


@ai_food_bp.get("/<int:food_id>")
@login_required
def food_advice(food_id: int):
    food = FoodItem.query.get(food_id)
    if food is None:
        abort(404, description=f"Food item {food_id} not found.")

    result = get_food_advice(user=current_user, food=food)

    if not result["success"]:
        return jsonify({
            "success": False,
            "error": result.get("error", "AI advice unavailable. Please try again later."),
        }), 503

    return jsonify({
        "success":       True,
        "food_name":     food.name,
        "analysis":      result["analysis"],
        "suggestion":    result["suggestion"],
        "verdict":       result["verdict"],
        "reasoning":     result["reasoning"],
        "using_fallback": result.get("using_fallback", False),  # tells frontend if AI ran or not
    })