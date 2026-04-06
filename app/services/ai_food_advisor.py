from __future__ import annotations

import os
import json
import logging
from typing import TYPE_CHECKING, Dict, Any
from datetime import datetime, timedelta

from groq import Groq
from dotenv import load_dotenv

load_dotenv()

if TYPE_CHECKING:
    from app.models import User, FoodItem

logger = logging.getLogger(__name__)

_client: Groq | None = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise EnvironmentError("GROQ_API_KEY is not set.")
        _client = Groq(api_key=api_key)
    return _client


def _safe_float(val):
    try:
        return float(val) if val is not None else 0
    except:
        return 0


def _safe_int(val):
    try:
        return int(val) if val is not None else 0
    except:
        return 0


def _extract_user_data(user: "User") -> Dict[str, Any]:
    from app.models import BMIRecord, MealLog, FoodItem

    data = {"basic": {}, "preferences": {}, "goals": {}, "recent": {}}

    try:
        latest_bmi = user.bmi_records.order_by(BMIRecord.recorded_at.desc()).first()
        if latest_bmi:
            data["basic"]["bmi"] = latest_bmi.bmi
            data["basic"]["bmi_category"] = latest_bmi.category
            data["basic"]["height"] = latest_bmi.height
            data["basic"]["weight"] = latest_bmi.weight
    except Exception as e:
        logger.warning(f"BMI fetch failed: {e}")

    if user.dietary_preference:
        try:
            pref = user.dietary_preference
            data["preferences"]["diet_type"] = pref.diet_type
            data["preferences"]["food_restrictions"] = pref.food_restrictions or []
            data["preferences"]["allergies"] = pref.allergies or []
            data["preferences"]["preferred_cuisine"] = pref.preferred_cuisine or []
            data["preferences"]["avoid_foods"] = pref.avoid_foods or []
            data["preferences"]["favorite_foods"] = pref.favorite_foods or []
            data["goals"]["meals_per_day"] = pref.meals_per_day
            data["goals"]["calorie_goal"] = pref.calorie_goal
            data["goals"]["protein_goal"] = pref.protein_goal
            data["goals"]["carbs_goal"] = pref.carbs_goal
            data["goals"]["fat_goal"] = pref.fat_goal
        except Exception as e:
            logger.warning(f"Preferences fetch failed: {e}")

    try:
        recent_meals = user.meal_logs.order_by(MealLog.logged_at.desc()).limit(10).all()
        if recent_meals:
            data["recent"]["meal_patterns"] = [
                {
                    "food": m.food_name,
                    "meal_type": m.meal_type,
                    "date": m.logged_at.strftime("%Y-%m-%d"),
                    "goal": m.goal,
                }
                for m in recent_meals
            ]
        seven_days_ago = datetime.utcnow().replace(hour=0, minute=0, second=0) - timedelta(days=7)
        last_7_days = user.meal_logs.filter(MealLog.logged_at >= seven_days_ago).all()
        if last_7_days:
            total_cal, count = 0, 0
            for meal in last_7_days:
                fi = FoodItem.query.filter_by(name=meal.food_name).first()
                if fi and fi.calories:
                    total_cal += _safe_float(fi.calories)
                    count += 1
            if count > 0:
                data["recent"]["avg_daily_calories"] = total_cal / 7
    except Exception as e:
        logger.warning(f"Meal log fetch failed: {e}")

    return data


def _build_enhanced_prompt(user_data: Dict[str, Any], food: "FoodItem") -> str:
    food_name   = getattr(food, "name", "Unknown Food")
    calories    = _safe_float(getattr(food, "calories", 0))
    protein     = _safe_float(getattr(food, "protein", 0))
    carbs       = _safe_float(getattr(food, "carbs", 0))
    fat         = _safe_float(getattr(food, "fat", 0))
    diet_type   = getattr(food, "diet_type", None)
    description = getattr(food, "description", "")

    b = user_data["basic"]
    g = user_data["goals"]
    p = user_data["preferences"]
    r = user_data["recent"]

    bmi           = b.get("bmi")
    bmi_category  = b.get("bmi_category", "unknown")
    weight        = b.get("weight")
    height        = b.get("height")
    calorie_goal  = g.get("calorie_goal")
    protein_goal  = g.get("protein_goal")
    carbs_goal    = g.get("carbs_goal")
    fat_goal      = g.get("fat_goal")
    meals_per_day = g.get("meals_per_day")
    user_diet     = p.get("diet_type", "not specified")
    allergies     = p.get("allergies", [])
    restrictions  = p.get("food_restrictions", [])
    avoid_foods   = p.get("avoid_foods", [])
    fav_foods     = p.get("favorite_foods", [])
    cuisine       = p.get("preferred_cuisine", [])
    avg_cal       = r.get("avg_daily_calories")
    recent_meals  = r.get("meal_patterns", [])

    per_meal_budget = ""
    if calorie_goal and meals_per_day:
        budget = calorie_goal / meals_per_day
        per_meal_budget = f"Per-meal calorie budget: {budget:.0f} kcal (based on {meals_per_day} meals/day)"

    calorie_pct = ""
    if calorie_goal and calories:
        pct = (calories / calorie_goal) * 100
        calorie_pct = f"This food = {pct:.1f}% of daily calorie goal"

    macro_context = ""
    if protein_goal and protein:
        macro_context += f"Protein: {protein:.1f}g out of {protein_goal:.0f}g daily goal. "
    if carbs_goal and carbs:
        macro_context += f"Carbs: {carbs:.1f}g out of {carbs_goal:.0f}g daily goal. "
    if fat_goal and fat:
        macro_context += f"Fat: {fat:.1f}g out of {fat_goal:.0f}g daily goal."

    recent_context = ""
    if recent_meals:
        names = [m["food"] for m in recent_meals[:5]]
        recent_context = f"Recent meals logged: {', '.join(names)}"
    if avg_cal:
        recent_context += f". Avg daily intake last 7 days: {avg_cal:.0f} kcal"

    prompt = f"""You are a sharp, direct clinical nutritionist texting a client — not writing a report.

═══════════════════════════════════════
USER DATA
═══════════════════════════════════════
Body Metrics:
  - BMI: {bmi:.1f} (Category: {bmi_category}) {"← weight loss is a priority" if bmi_category in ["obese", "overweight"] else "← gaining weight is a priority" if bmi_category == "underweight" else "← healthy range"}
  - Weight: {weight:.1f} kg | Height: {height:.1f} cm

Daily Goals:
  - Calorie Goal: {calorie_goal or "not set"} kcal
  - Protein: {protein_goal or "not set"}g | Carbs: {carbs_goal or "not set"}g | Fat: {fat_goal or "not set"}g
  - Meals Per Day: {meals_per_day or "not set"}
  {per_meal_budget}

Dietary Profile:
  - Diet Type: {user_diet}
  - Allergies: {', '.join(allergies) if allergies else "none"}
  - Restrictions: {', '.join(restrictions) if restrictions else "none"}
  - Foods to Avoid: {', '.join(avoid_foods) if avoid_foods else "none"}
  - Favorite Foods: {', '.join(fav_foods) if fav_foods else "none"}
  - Preferred Cuisines: {', '.join(cuisine) if cuisine else "none"}

Recent Eating Pattern:
  {recent_context if recent_context else "No recent meal history"}

═══════════════════════════════════════
FOOD BEING EVALUATED
═══════════════════════════════════════
Name: {food_name}
Dietary Label: {diet_type or "unspecified"}
Description: {description or "none"}
Calories: {calories:.0f} kcal | Protein: {protein:.1f}g | Carbs: {carbs:.1f}g | Fat: {fat:.1f}g
{calorie_pct}
{macro_context}

═══════════════════════════════════════
INSTRUCTIONS
═══════════════════════════════════════
Write like a sharp, direct nutritionist texting a client — not a report.
- "analysis": 1 crisp sentence. Lead with the single most important insight for THIS user.
  No openers like "Given your profile" or "Based on your data". Just the insight.
  Mention ONE concrete number (BMI, calories, or a macro) max.
- "suggestion": 1 actionable sentence. Exact portion size + best timing + what to pair it with.
  No vague advice like "eat in moderation". Be specific.
- "verdict": exactly one of "Good", "Limit", or "Avoid".
- "reasoning": the one data point that decided the verdict.

If the user has an allergy or restriction that matches, lead with that — it overrides everything else.
If they've eaten high-calorie foods recently and this is also high-cal, call it out.

Return ONLY this JSON, no markdown, no extra text:
{{
  "analysis": "1 punchy sentence — the most important thing about this food for this specific user",
  "suggestion": "Exact portion, best meal time, and what to pair it with",
  "verdict": "Good | Limit | Avoid",
  "reasoning": "The one data point that decided this verdict"
}}"""

    return prompt.strip()


def _smart_fallback_analysis(user_data: Dict[str, Any], food: "FoodItem") -> Dict[str, Any]:
    calories       = _safe_float(getattr(food, "calories", 0))
    protein        = _safe_float(getattr(food, "protein", 0))
    fat            = _safe_float(getattr(food, "fat", 0))
    food_name      = getattr(food, "name", "this food").lower()
    food_diet_type = getattr(food, "diet_type", "").lower()

    bmi          = user_data["basic"].get("bmi")
    bmi_category = user_data["basic"].get("bmi_category", "").lower()
    calorie_goal = user_data["goals"].get("calorie_goal", 2000)
    protein_goal = user_data["goals"].get("protein_goal", 50)
    diet_type    = user_data["preferences"].get("diet_type", "").lower()
    allergies    = user_data["preferences"].get("allergies", [])
    restrictions = user_data["preferences"].get("food_restrictions", [])
    avoid_foods  = user_data["preferences"].get("avoid_foods", [])

    # Allergy check — highest priority
    for allergy in allergies:
        if allergy and allergy.lower() in food_name:
            return {
                "success": True,
                "analysis": f"This contains {allergy} — which is on your allergy list.",
                "suggestion": "Skip it entirely and check labels on any similar products.",
                "verdict": "Avoid",
                "reasoning": f"Allergen match: {allergy}",
                "error": None,
                "using_fallback": True,
            }

    # Restriction / avoid-foods check
    for r in restrictions + avoid_foods:
        if r and r.lower() in food_name:
            return {
                "success": True,
                "analysis": f"'{r}' is on your avoid list — this one's out.",
                "suggestion": f"Find a {diet_type or 'diet-friendly'} alternative that hits the same craving.",
                "verdict": "Avoid",
                "reasoning": f"Matches avoid/restriction entry: {r}",
                "error": None,
                "using_fallback": True,
            }

    # Diet type mismatch check
    if diet_type and food_diet_type and diet_type != food_diet_type:
        return {
            "success": True,
            "analysis": f"You eat {diet_type} but this is labeled {food_diet_type} — doesn't align.",
            "suggestion": f"Look for a {diet_type}-certified version of this instead.",
            "verdict": "Avoid",
            "reasoning": f"Diet mismatch: need {diet_type}, food is {food_diet_type}",
            "error": None,
            "using_fallback": True,
        }

    calorie_pct = (calories / calorie_goal * 100) if calorie_goal else 0
    bmi_str     = f"BMI {bmi:.1f} ({bmi_category})" if bmi else "your current profile"

    # Verdict logic
    verdict = "Limit"
    if bmi_category == "underweight" and calories > 400:
        verdict = "Good"
    elif calories < 300 and protein >= 15:
        verdict = "Good"
    elif calories < 500 and protein >= 10:
        verdict = "Good"
    elif calories > 800 or fat > 40:
        verdict = "Avoid"
    elif bmi_category == "obese" and calories > 500:
        verdict = "Avoid"

    # Tight, direct copy for each verdict
    if verdict == "Good":
        analysis   = f"At {calories:.0f} kcal and {protein:.0f}g protein, this fits your {calorie_goal} kcal goal well."
        suggestion = "Have one serving at breakfast or lunch when you need the energy most."
        reasoning  = f"{bmi_str} + solid protein-to-calorie ratio"
    elif verdict == "Limit":
        analysis   = f"At {calorie_pct:.0f}% of your {calorie_goal} kcal daily goal, this works occasionally — not every day."
        suggestion = "Stick to half a serving; pair with leafy greens or lean protein to keep the meal balanced."
        reasoning  = f"{bmi_str} — calorie load is borderline at {calories:.0f} kcal"
    else:
        analysis   = f"{calories:.0f} kcal in one item is too steep for your {calorie_goal} kcal goal and {bmi_category} BMI."
        suggestion = "Swap for a lean protein or veggie-based option — same satisfaction, far fewer calories."
        reasoning  = (
            f"{bmi_str} + {calories:.0f} kcal = {calorie_pct:.0f}% of daily goal in one shot"
            if calories > 500 else bmi_str
        )

    return {
        "success": True,
        "analysis": analysis,
        "suggestion": suggestion,
        "verdict": verdict,
        "reasoning": reasoning,
        "error": None,
        "using_fallback": True,
    }


def get_food_advice(user: "User", food: "FoodItem") -> Dict[str, Any]:
    try:
        user_data = _extract_user_data(user)

        try:
            client = _get_client()
            prompt = _build_enhanced_prompt(user_data, food)

            print("DEBUG: Sending to Groq API...")
            print(
                f"DEBUG: User data populated — "
                f"basic={bool(user_data['basic'])}, "
                f"goals={bool(user_data['goals'])}, "
                f"prefs={bool(user_data['preferences'])}"
            )

            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a sharp clinical nutritionist giving direct, personalised dietary advice. "
                            "Every sentence must reference the user's actual numbers — no generic statements. "
                            "Return only valid JSON with no markdown formatting."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
                max_tokens=600,
            )

            raw = completion.choices[0].message.content.strip()
            print(f"DEBUG: Raw response: {raw[:200]}")

            # Strip markdown fences if present
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            parsed = json.loads(raw)

            return {
                "success":        True,
                "analysis":       parsed.get("analysis", ""),
                "suggestion":     parsed.get("suggestion", ""),
                "verdict":        parsed.get("verdict", "Limit"),
                "reasoning":      parsed.get("reasoning", ""),
                "error":          None,
                "using_fallback": False,
            }

        except Exception as e:
            print(f"ERROR: AI failed — {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
            result = _smart_fallback_analysis(user_data, food)
            result["ai_error"] = str(e)
            return result

    except Exception as e:
        print(f"CRITICAL ERROR: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        return {
            "success":        False,
            "analysis":       f"This food has {_safe_float(getattr(food, 'calories', 0)):.0f} kcal.",
            "suggestion":     "Service error — please try again.",
            "verdict":        "Limit",
            "reasoning":      f"Error: {str(e)[:100]}",
            "error":          str(e),
            "using_fallback": True,
        }