from flask import Blueprint, render_template, jsonify, redirect
from flask_login import login_required, current_user
from app.models import BMIRecord, MealLog, DietSuggestion, db
import os, json, traceback
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

diet_suggestions_bp = Blueprint("diet_suggestions", __name__)

# ── Index Route ───────────────────────────────────────────────────────────────

@diet_suggestions_bp.route("/diet-suggestions")
@login_required
def index():
    if not current_user.has_role("user"):
        return redirect("/")

    pref = current_user.dietary_preference
    latest_bmi = (
        BMIRecord.query.filter_by(user_id=current_user.id)
        .order_by(BMIRecord.recorded_at.desc())
        .first()
    )
    latest_log = (
        MealLog.query.filter_by(user_id=current_user.id)
        .order_by(MealLog.logged_at.desc())
        .first()
    )

    profile = {
        "goal":             latest_log.goal if latest_log and latest_log.goal else "maintain_weight",
        "bmi":              latest_bmi.bmi if latest_bmi else None,
        "bmi_category":     latest_bmi.category if latest_bmi else "Unknown",
        "calorie_goal":     pref.calorie_goal if pref else 2000,
        "meals_per_day":    pref.meals_per_day if pref else 3,
        "diet_type":        pref.diet_type if pref else "none",
        "allergies":        pref.allergies if pref else [],
        "avoid_foods":      pref.avoid_foods if pref else [],
        "preferred_cuisine":pref.preferred_cuisine if pref else [],
        "protein_goal":     pref.protein_goal if pref else 50,
        "carbs_goal":       pref.carbs_goal if pref else 250,
        "fat_goal":         pref.fat_goal if pref else 70,
        "has_preference":   pref is not None,
    }

    return render_template("dashboard/diet_suggestions.html", profile=profile)


# ── Prompt Builder ────────────────────────────────────────────────────────────

def _build_prompt(profile, retry_feedback: list[str] | None = None):
    """
    Build the dietitian prompt. On retries, inject concrete feedback so
    the model understands exactly what went wrong last time.
    """
    retry_block = ""
    if retry_feedback:
        issues = "\n".join(f"  - {w}" for w in retry_feedback)
        retry_block = f"""
=== ⚠ PREVIOUS ATTEMPT FAILED — FIX THESE ISSUES ===
{issues}
The most common cause: you set "calories" independently of macros.
DO NOT do that. ALWAYS derive calories as: protein*4 + carbs*4 + fat*9.
=====================================================
"""

    # Determine valid meal types for this meals_per_day count
    meal_type_map = {
        1: ["lunch"],
        2: ["breakfast", "dinner"],
        3: ["breakfast", "lunch", "dinner"],
        4: ["breakfast", "lunch", "afternoon snack", "dinner"],
        5: ["breakfast", "mid-morning snack", "lunch", "afternoon snack", "dinner"],
        6: ["breakfast", "mid-morning snack", "lunch", "afternoon snack", "dinner", "evening snack"],
    }
    valid_meal_types = meal_type_map.get(profile["meals_per_day"], meal_type_map[3])
    meal_types_str   = ", ".join(f'"{m}"' for m in valid_meal_types)

    return f"""
You are a certified registered dietitian AI embedded in NutriConnect.
Your ONLY job: return a single valid JSON object. Any deviation breaks the app.
{retry_block}
=== USER PROFILE ===
- Health Goal:        {profile['goal']}
- BMI:                {profile['bmi']} ({profile['bmi_category']})
- Daily Calorie Target: {profile['calorie_goal']} kcal
- Meals Per Day:      {profile['meals_per_day']}
- Diet Type:          {profile['diet_type']}
- Allergies:          {profile['allergies']}
- Foods to Avoid:     {profile['avoid_foods']}
- Preferred Cuisines: {profile['preferred_cuisine']}
- Macro Targets — Protein: {profile['protein_goal']}g | Carbs: {profile['carbs_goal']}g | Fat: {profile['fat_goal']}g

=== ⚠ CRITICAL MATH RULE (most important rule) ===
Every "calories" value MUST equal: (protein × 4) + (carbs × 4) + (fat × 9).
This is non-negotiable. Never set calories to an arbitrary number.
Example: protein=30g, carbs=45g, fat=10g → calories = 30×4 + 45×4 + 10×9 = 120+180+90 = 390

=== STRICT OUTPUT RULES ===
1.  Output ONLY raw JSON. No markdown. No ```json fences. No explanations. No trailing text.
2.  The JSON must be parseable by Python's json.loads() without any preprocessing.
3.  NEVER include any food conflicting with allergies, diet type, or avoid list. Safety-critical.
4.  match_score must reflect genuine compatibility; penalise for any goal/diet/macro mismatch.
5.  All calorie and macro numbers must be integers (never strings or floats).
6.  meal_plan must contain EXACTLY {profile['meals_per_day']} entries.
7.  foods list must contain EXACTLY 6 items.
8.  meal_type must be chosen from ONLY these options: {meal_types_str}.
9.  Total meal_plan calories must be within ±100 kcal of {profile['calorie_goal']} kcal.
10. Total macros across ALL meals must approximate:
    - Protein: {profile['protein_goal']}g (±15g), Carbs: {profile['carbs_goal']}g (±25g), Fat: {profile['fat_goal']}g (±15g)
11. Carbohydrates must supply ≥30% of total daily calories. Under-reporting carbs is an error.
12. diet_type classification (safety-critical):
    - Plant-based foods (grains, vegetables, fruits, legumes, nuts, seeds, tea, coffee)
      → MUST be "vegetarian" or "vegan". NEVER classify as "non-vegetarian".
    - "non-vegetarian" ONLY for animal flesh (chicken, beef, pork, fish, seafood).
    - Beverages: use realistic values (green tea = 2 kcal, 0g P, 0g C, 0g F).

=== REQUIRED JSON STRUCTURE ===
{{
  "insight": "2-3 specific sentences on why this diet suits this user's goal and BMI.",
  "foods": [
    {{
      "name": "food name",
      "diet_type": "vegetarian | vegan | non-vegetarian | pescatarian | keto | paleo",
      "calories": 390,
      "protein": 30,
      "carbs": 45,
      "fat": 10,
      "match_score": 87,
      "reason": "1-2 sentences tied to this user's specific goal and restrictions."
    }}
  ],
  "meal_plan": [
    {{
      "meal_type": "breakfast",
      "food": "food name",
      "calories": 390,
      "protein": 30,
      "carbs": 45,
      "fat": 10,
      "why": "One sentence tied to the user's goal of {profile['goal']} and macro targets."
    }}
  ]
}}

Now generate the JSON. Output nothing except the JSON object.
"""


# ── Post-processing Helpers ───────────────────────────────────────────────────

def _extract_json(raw: str) -> str:
    """Robustly extract a JSON object from a raw AI response string."""
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    start = raw.find("{")
    end   = raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No valid JSON object found in AI response.")
    return raw[start:end]


def _enforce_int_types(result: dict) -> dict:
    """Cast all numeric fields to int."""
    int_fields_food = ("calories", "protein", "carbs", "fat", "match_score")
    int_fields_meal = ("calories", "protein", "carbs", "fat")

    for food in result.get("foods", []):
        for k in int_fields_food:
            if k in food:
                try:    food[k] = int(round(float(food[k])))
                except: food[k] = 0

    for meal in result.get("meal_plan", []):
        for k in int_fields_meal:
            if k in meal:
                try:    meal[k] = int(round(float(meal[k])))
                except: meal[k] = 0

    return result


def _recalculate_calories_from_macros(result: dict) -> dict:
    """
    *** THE CORE FIX ***
    Overwrite every calorie value with the mathematically correct figure
    derived from macros: calories = protein*4 + carbs*4 + fat*9.

    This eliminates macro-math mismatches entirely regardless of what the
    AI hallucinated for the calories field.
    """
    for food in result.get("foods", []):
        p = food.get("protein", 0) or 0
        c = food.get("carbs",   0) or 0
        f = food.get("fat",     0) or 0
        food["calories"] = int(round(p * 4 + c * 4 + f * 9))

    for meal in result.get("meal_plan", []):
        p = meal.get("protein", 0) or 0
        c = meal.get("carbs",   0) or 0
        f = meal.get("fat",     0) or 0
        meal["calories"] = int(round(p * 4 + c * 4 + f * 9))

    return result


def _scale_meal_plan_to_calorie_goal(result: dict, profile: dict) -> dict:
    """
    If total meal-plan calories (after macro recalculation) still deviate
    from the user's goal by more than 100 kcal, scale all macros
    proportionally so the total lands on target.
    """
    meal_plan  = result.get("meal_plan", [])
    total_cals = sum(m.get("calories", 0) for m in meal_plan)
    goal       = profile["calorie_goal"]

    if total_cals == 0:
        return result

    gap = abs(total_cals - goal)
    if gap > 100:
        scale = goal / total_cals
        for meal in meal_plan:
            meal["protein"] = int(round((meal.get("protein", 0) or 0) * scale))
            meal["carbs"]   = int(round((meal.get("carbs",   0) or 0) * scale))
            meal["fat"]     = int(round((meal.get("fat",     0) or 0) * scale))
            # Recompute calories after scaling
            meal["calories"] = int(round(
                meal["protein"] * 4 + meal["carbs"] * 4 + meal["fat"] * 9
            ))
        new_total = sum(m["calories"] for m in meal_plan)
        print(f"[SCALE] Adjusted meal plan: {total_cals} kcal → {new_total} kcal (goal: {goal})")

    return result


# Vegetarian/vegan keyword sets for diet_type correction
_VEGETARIAN_KEYWORDS = {
    "rice", "broccoli", "oat", "oats", "banana", "apple", "spinach", "lentil",
    "bean", "tofu", "quinoa", "almond", "walnut", "yogurt", "egg", "milk",
    "cheese", "paneer", "chickpea", "sweet potato", "carrot", "cucumber",
    "tomato", "mushroom", "avocado", "peanut", "cashew", "date", "mango",
    "orange", "blueberry", "strawberry", "olive", "honey", "tea", "coffee",
    "bread", "pasta", "potato", "corn", "pumpkin", "zucchini", "eggplant",
    "cauliflower", "kale", "lettuce", "celery", "garlic", "onion", "ginger",
    "turmeric", "flaxseed", "chia", "hemp", "soy", "edamame", "tempeh",
    "seitan", "lemon", "lime", "grape", "watermelon", "pineapple", "coconut",
    "barley", "millet", "buckwheat", "rye", "granola", "hummus", "tahini",
}
_VEGAN_EXCLUSIONS = {"yogurt", "egg", "milk", "cheese", "paneer", "honey"}


def _sanitise_foods(result: dict) -> dict:
    """
    1. Auto-correct obvious diet_type misclassifications.
    2. Flag zero-macro beverages so the frontend can style them gracefully.
    """
    for food in result.get("foods", []):
        name_lower = (food.get("name") or "").lower()
        diet_lower = (food.get("diet_type") or "").lower()

        is_plant_based = any(kw in name_lower for kw in _VEGETARIAN_KEYWORDS)
        if is_plant_based and diet_lower == "non-vegetarian":
            is_vegan      = not any(exc in name_lower for exc in _VEGAN_EXCLUSIONS)
            corrected     = "vegan" if is_vegan else "vegetarian"
            food["diet_type"] = corrected
            print(f"[SANITISE] Corrected '{food['name']}': non-vegetarian → {corrected}")

        total_macros = (
            (food.get("protein", 0) or 0) +
            (food.get("carbs",   0) or 0) +
            (food.get("fat",     0) or 0)
        )
        if total_macros == 0 and (food.get("calories", 0) or 0) <= 5:
            food["is_beverage"] = True

    return result


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_result(result: dict, profile: dict) -> tuple[bool, list[str]]:
    """
    Validate the AI response after all post-processing has been applied.
    Returns (is_valid, warnings).

    After _recalculate_calories_from_macros() runs, macro-math mismatches
    should always be 0, so validation focuses on:
      - Correct meal / food count
      - Calorie goal proximity
      - Minimum carb ratio
    """
    warnings   = []
    meal_plan  = result.get("meal_plan", [])
    meals_n    = len(meal_plan)
    foods_n    = len(result.get("foods", []))

    total_cals    = sum(m.get("calories", 0) for m in meal_plan)
    total_protein = sum(m.get("protein",  0) for m in meal_plan)
    total_carbs   = sum(m.get("carbs",    0) for m in meal_plan)
    total_fat     = sum(m.get("fat",      0) for m in meal_plan)

    goal      = profile["calorie_goal"]
    cal_gap   = abs(total_cals - goal)
    carb_ratio = (total_carbs * 4) / total_cals if total_cals > 0 else 0

    # Macro math gap — should be 0 after recalculation; logged for debugging only
    macro_implied = total_protein * 4 + total_carbs * 4 + total_fat * 9
    macro_gap     = abs(macro_implied - total_cals)

    print(
        f"[VALIDATE] meals={meals_n} foods={foods_n} "
        f"cals={total_cals} protein={total_protein}g "
        f"carbs={total_carbs}g fat={total_fat}g"
    )
    print(
        f"[VALIDATE] cal_gap={cal_gap} carb_ratio={carb_ratio:.0%} "
        f"macro_math_gap={macro_gap}"
    )

    if meals_n != profile["meals_per_day"]:
        warnings.append(
            f"Expected {profile['meals_per_day']} meals, got {meals_n}. "
            "meal_plan must have EXACTLY the right count."
        )

    if foods_n != 6:
        warnings.append(
            f"Expected 6 foods in the foods list, got {foods_n}."
        )

    if cal_gap > 150:
        warnings.append(
            f"After scaling, calories are still {cal_gap} kcal off "
            f"(got {total_cals}, need {goal}). "
            "Adjust protein/carbs/fat proportions."
        )

    if carb_ratio < 0.25:
        warnings.append(
            f"Carb ratio is only {carb_ratio:.0%} — must be ≥30%. "
            "Increase carbs significantly; reduce fat."
        )

    is_valid = (
        meals_n == profile["meals_per_day"]
        and foods_n == 6
        and cal_gap <= 150
        and carb_ratio >= 0.25
    )
    return is_valid, warnings


# ── Generate Route ────────────────────────────────────────────────────────────

MAX_RETRIES = 3

@diet_suggestions_bp.route("/diet-suggestions/generate", methods=["POST"])
@login_required
def generate():
    try:
        if not current_user.has_role("user"):
            return jsonify({"error": "Unauthorized"}), 403

        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            return jsonify({"error": "Groq API key is not configured on the server."}), 500

        client = Groq(api_key=api_key)

        # ── Build user profile ────────────────────────────────────────────────
        pref       = current_user.dietary_preference
        latest_bmi = (
            BMIRecord.query.filter_by(user_id=current_user.id)
            .order_by(BMIRecord.recorded_at.desc())
            .first()
        )
        latest_log = (
            MealLog.query.filter_by(user_id=current_user.id)
            .order_by(MealLog.logged_at.desc())
            .first()
        )

        profile = {
            "goal":              latest_log.goal if latest_log and latest_log.goal else "maintain_weight",
            "bmi":               latest_bmi.bmi if latest_bmi else "unknown",
            "bmi_category":      latest_bmi.category if latest_bmi else "Unknown",
            "calorie_goal":      pref.calorie_goal if pref else 2000,
            "meals_per_day":     pref.meals_per_day if pref else 3,
            "diet_type":         pref.diet_type if pref else "none",
            "allergies":         ", ".join(pref.allergies)          if pref and pref.allergies          else "none",
            "avoid_foods":       ", ".join(pref.avoid_foods)        if pref and pref.avoid_foods        else "none",
            "preferred_cuisine": ", ".join(pref.preferred_cuisine)  if pref and pref.preferred_cuisine  else "any",
            "protein_goal":      pref.protein_goal if pref else 50,
            "carbs_goal":        pref.carbs_goal   if pref else 250,
            "fat_goal":          pref.fat_goal     if pref else 70,
        }

        system_message = (
            "You are a clinical dietitian AI. Output ONLY valid raw JSON — "
            "no markdown, no code fences, no explanation, no preamble, no postamble. "
            "Your entire response must be a single JSON object parseable by json.loads(). "
            "THE MOST IMPORTANT RULE: every calories value MUST equal "
            "protein×4 + carbs×4 + fat×9. Never set calories to an arbitrary number."
        )

        # ── Retry loop ────────────────────────────────────────────────────────
        result         = None
        last_warnings  = []
        retry_feedback = None   # Injected into prompt on retries

        for attempt in range(MAX_RETRIES):
            print(f"[ATTEMPT {attempt + 1}/{MAX_RETRIES}] Calling Groq API...")

            prompt = _build_prompt(profile, retry_feedback=retry_feedback)

            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                max_tokens=1800,
                temperature=0.35,           # Slightly lower = more deterministic math
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user",   "content": prompt},
                ],
            )

            raw    = response.choices[0].message.content.strip()
            raw    = _extract_json(raw)
            parsed = json.loads(raw)

            # ── Post-processing pipeline ──────────────────────────────────────
            parsed = _enforce_int_types(parsed)
            parsed = _recalculate_calories_from_macros(parsed)   # ← core fix
            parsed = _scale_meal_plan_to_calorie_goal(parsed, profile)
            parsed = _recalculate_calories_from_macros(parsed)   # recalc after scaling
            parsed = _sanitise_foods(parsed)

            is_valid, last_warnings = _validate_result(parsed, profile)
            result = parsed

            if is_valid:
                print(f"[ATTEMPT {attempt + 1}] Passed validation ✓")
                break

            # Feed specific warnings back into the next prompt
            retry_feedback = last_warnings
            print(f"[ATTEMPT {attempt + 1}] Failed — retrying with feedback: {last_warnings}")

        # ── Persist to database ───────────────────────────────────────────────
        try:
            suggestion = DietSuggestion(
                user_id        = current_user.id,
                goal           = profile["goal"],
                bmi            = float(profile["bmi"]) if profile["bmi"] not in (None, "unknown") else None,
                bmi_category   = profile["bmi_category"],
                calorie_goal   = profile["calorie_goal"],
                diet_type      = profile["diet_type"],
                insight        = result.get("insight"),
                foods_json     = json.dumps(result.get("foods", [])),
                meal_plan_json = json.dumps(result.get("meal_plan", [])),
                macro_warning  = None,   # No longer needed — math is corrected server-side
            )
            db.session.add(suggestion)
            db.session.commit()
            result["suggestion_id"] = suggestion.id
            print(f"[DB] Saved suggestion id={suggestion.id} for user={current_user.id}")
        except Exception as db_err:
            db.session.rollback()
            print(f"[DB ERROR] Failed to save suggestion: {db_err}")

        return jsonify(result)

    except json.JSONDecodeError as e:
        traceback.print_exc()
        return jsonify({"error": f"Failed to parse AI response as JSON: {str(e)}"}), 500

    except ValueError as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"AI generation failed: {str(e)}"}), 500


# ── Read Routes ───────────────────────────────────────────────────────────────

@diet_suggestions_bp.route("/diet-suggestions/latest")
@login_required
def latest():
    """Return the most recent suggestion for the current user."""
    if not current_user.has_role("user"):
        return jsonify({"error": "Unauthorized"}), 403

    suggestion = (
        DietSuggestion.query
        .filter_by(user_id=current_user.id)
        .order_by(DietSuggestion.created_at.desc())
        .first()
    )
    if not suggestion:
        return jsonify({"none": True}), 200

    return jsonify(suggestion.to_dict())


@diet_suggestions_bp.route("/diet-suggestions/history")
@login_required
def history():
    """Return the last 5 suggestions for the current user."""
    if not current_user.has_role("user"):
        return jsonify({"error": "Unauthorized"}), 403

    suggestions = (
        DietSuggestion.query
        .filter_by(user_id=current_user.id)
        .order_by(DietSuggestion.created_at.desc())
        .limit(5)
        .all()
    )
    return jsonify([s.to_dict() for s in suggestions])