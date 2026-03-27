from flask import Blueprint, render_template, jsonify, redirect
from flask_login import login_required, current_user
from app.models import BMIRecord, MealLog, DietSuggestion, db
import os, json, traceback
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

diet_suggestions_bp = Blueprint("diet_suggestions", __name__)

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
        "goal": latest_log.goal if latest_log and latest_log.goal else "maintain_weight",
        "bmi": latest_bmi.bmi if latest_bmi else None,
        "bmi_category": latest_bmi.category if latest_bmi else "Unknown",
        "calorie_goal": pref.calorie_goal if pref else 2000,
        "meals_per_day": pref.meals_per_day if pref else 3,
        "diet_type": pref.diet_type if pref else "none",
        "allergies": pref.allergies if pref else [],
        "avoid_foods": pref.avoid_foods if pref else [],
        "preferred_cuisine": pref.preferred_cuisine if pref else [],
        "protein_goal": pref.protein_goal if pref else 50,
        "carbs_goal": pref.carbs_goal if pref else 250,
        "fat_goal": pref.fat_goal if pref else 70,
        "has_preference": pref is not None,
    }

    return render_template("dashboard/diet_suggestions.html", profile=profile)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_prompt(profile):
    """Build the strongly-constrained dietitian prompt."""
    return f"""
You are a certified registered dietitian AI embedded in NutriConnect, a clinical nutrition platform.
Your ONLY job is to return a single, valid JSON object. Any deviation will break the application.

=== USER PROFILE ===
- Health Goal: {profile['goal']}
- BMI: {profile['bmi']} ({profile['bmi_category']})
- Daily Calorie Target: {profile['calorie_goal']} kcal
- Meals Per Day: {profile['meals_per_day']}
- Diet Type: {profile['diet_type']}
- Allergies / Restrictions: {profile['allergies']}
- Foods to Avoid: {profile['avoid_foods']}
- Preferred Cuisines: {profile['preferred_cuisine']}
- Macro Targets: Protein {profile['protein_goal']}g | Carbs {profile['carbs_goal']}g | Fat {profile['fat_goal']}g

=== STRICT OUTPUT RULES ===
1.  Output ONLY raw JSON. No markdown. No ```json fences. No explanations. No comments. No trailing text.
2.  The JSON must be parseable by Python's json.loads() without any preprocessing.
3.  Never invent or hallucinate nutritional values — use realistic, evidence-based figures.
4.  NEVER include any food that conflicts with the user's allergies, diet type, or avoid list — this is a safety-critical rule.
5.  match_score must reflect genuine compatibility: penalise heavily for any mismatch with goal, diet type, or macros.
6.  All calorie and macro numbers must be integers, not strings.
7.  meal_plan must contain EXACTLY {profile['meals_per_day']} entries — no more, no less.
8.  foods list must contain EXACTLY 6 items — no more, no less.
9.  Every meal_plan entry's "meal_type" must be one of: breakfast, mid-morning snack, lunch, afternoon snack, dinner, evening snack — chosen logically based on meals_per_day.
10. The sum of meal_plan calories must be within ±100 kcal of the user's daily calorie target of {profile['calorie_goal']} kcal.
11. Macro totals across ALL meal_plan entries must approximate these targets:
    - Protein: {profile['protein_goal']}g  (±10g acceptable)
    - Carbs:   {profile['carbs_goal']}g    (±20g acceptable)
    - Fat:     {profile['fat_goal']}g      (±10g acceptable)
12. Each meal's macros must follow the calorie-per-gram rule:
    - 1g protein = 4 kcal, 1g carbs = 4 kcal, 1g fat = 9 kcal
    - (protein*4 + carbs*4 + fat*9) for each meal must approximately equal that meal's reported calorie value.
    - This is a hard mathematical constraint — do NOT violate it.
13. Carbohydrates must account for at least 30% of total daily calories. Do not under-report carbs.
14. diet_type classification rules (safety-critical — never misclassify):
    - Any food made entirely from plants (grains, vegetables, fruits, legumes, nuts, seeds, tea, coffee)
      MUST be classified as "vegetarian" or "vegan". NEVER classify plant foods as "non-vegetarian".
    - "non-vegetarian" is ONLY for foods containing animal flesh (chicken, beef, pork, fish, seafood).
    - Beverages (tea, coffee, water, juices) must still have realistic calorie/macro values — even if minimal.
      Green tea = 2 kcal, 0g protein, 0g carbs, 0g fat is acceptable. Do NOT leave all fields at 0.

=== REQUIRED JSON STRUCTURE (follow exactly) ===
{{
  "insight": "2-3 sentences explaining the overall diet strategy and why it fits this user's goal and BMI category. Be specific, not generic.",
  "foods": [
    {{
      "name": "food name",
      "diet_type": "vegetarian | vegan | non-vegetarian | pescatarian | keto | paleo",
      "calories": 120,
      "protein": 8,
      "carbs": 15,
      "fat": 4,
      "match_score": 87,
      "reason": "1-2 sentences explaining why this food suits THIS user's specific goal, BMI, and restrictions."
    }}
  ],
  "meal_plan": [
    {{
      "meal_type": "breakfast",
      "food": "food name",
      "calories": 400,
      "protein": 30,
      "carbs": 45,
      "fat": 10,
      "why": "One sentence tied directly to the user's goal of {profile['goal']} and their calorie/macro targets."
    }}
  ]
}}

Now generate the JSON for this user. Output nothing except the JSON object.
"""

def _extract_json(raw):
    """Robustly extract a JSON object from a raw AI response string."""
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    # Surgically extract just the outermost JSON object
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No valid JSON object found in AI response.")
    return raw[start:end]

def _enforce_int_types(result):
    """Cast all numeric fields to int to prevent string/float type issues on frontend."""
    for food in result.get("foods", []):
        for key in ("calories", "protein", "carbs", "fat", "match_score"):
            if key in food:
                try:
                    food[key] = int(round(float(food[key])))
                except (ValueError, TypeError):
                    food[key] = 0

    for meal in result.get("meal_plan", []):
        for key in ("calories", "protein", "carbs", "fat"):
            if key in meal:
                try:
                    meal[key] = int(round(float(meal[key])))
                except (ValueError, TypeError):
                    meal[key] = 0

    return result

# Canonical vegetarian/vegan ingredients — used to auto-correct misclassified foods
_VEGETARIAN_KEYWORDS = {
    "rice", "broccoli", "oat", "banana", "apple", "spinach", "lentil", "bean",
    "tofu", "quinoa", "almond", "walnut", "yogurt", "egg", "milk", "cheese",
    "paneer", "chickpea", "sweet potato", "carrot", "cucumber", "tomato",
    "mushroom", "avocado", "peanut", "cashew", "date", "mango", "orange",
    "blueberry", "strawberry", "olive", "honey", "tea", "coffee", "oats",
    "bread", "pasta", "potato", "corn", "pumpkin", "zucchini", "eggplant",
    "cauliflower", "kale", "lettuce", "celery", "garlic", "onion", "ginger",
    "turmeric", "flaxseed", "chia", "hemp", "soy", "edamame", "tempeh",
    "seitan", "lemon", "lime", "grape", "watermelon", "pineapple", "coconut",
}

_VEGAN_EXCLUSIONS = {"yogurt", "egg", "milk", "cheese", "paneer", "honey"}

def _sanitise_foods(result):
    """
    1. Auto-correct obvious diet_type misclassifications (e.g. Brown Rice → vegetarian).
    2. Flag zero-macro foods (beverages etc.) so the frontend can render them gracefully.
    """
    for food in result.get("foods", []):
        name_lower = (food.get("name") or "").lower()
        diet_lower = (food.get("diet_type") or "").lower()

        # Check if any vegetarian keyword matches the food name
        is_plant_based = any(kw in name_lower for kw in _VEGETARIAN_KEYWORDS)

        if is_plant_based and diet_lower == "non-vegetarian":
            # Determine vegan vs vegetarian
            is_vegan = not any(exc in name_lower for exc in _VEGAN_EXCLUSIONS)
            food["diet_type"] = "vegan" if is_vegan else "vegetarian"
            print(f"[SANITISE] Corrected '{food['name']}' diet_type: non-vegetarian → {food['diet_type']}")

        # Flag zero-macro foods (e.g. green tea, black coffee, water)
        total_macros = (food.get("protein", 0) or 0) + (food.get("carbs", 0) or 0) + (food.get("fat", 0) or 0)
        if total_macros == 0 and (food.get("calories", 0) or 0) == 0:
            food["is_beverage"] = True  # frontend can use this to style differently

    return result

def _validate_result(result, profile):
    """
    Validate AI response against nutritional constraints.
    Returns (is_valid: bool, warnings: list[str]).
    A result is valid only if calorie gap ≤150 kcal AND carb ratio ≥25%.
    """
    warnings = []
    meal_plan     = result.get("meal_plan", [])
    meals_count   = len(meal_plan)
    foods_count   = len(result.get("foods", []))

    total_cals    = sum(m.get("calories", 0) for m in meal_plan)
    total_protein = sum(m.get("protein",  0) for m in meal_plan)
    total_carbs   = sum(m.get("carbs",    0) for m in meal_plan)
    total_fat     = sum(m.get("fat",      0) for m in meal_plan)

    calorie_goal  = profile["calorie_goal"]
    cal_gap       = abs(total_cals - calorie_goal)
    carb_ratio    = (total_carbs * 4) / total_cals if total_cals > 0 else 0

    # Macro math consistency check
    expected_from_macros = (total_protein * 4) + (total_carbs * 4) + (total_fat * 9)
    macro_math_gap = abs(expected_from_macros - total_cals)

    print(
        f"[VALIDATE] meals={meals_count} foods={foods_count} "
        f"cals={total_cals} protein={total_protein}g "
        f"carbs={total_carbs}g fat={total_fat}g"
    )
    print(
        f"[VALIDATE] cal_gap={cal_gap} carb_ratio={carb_ratio:.0%} "
        f"macro_math_gap={macro_math_gap}"
    )

    if meals_count != profile["meals_per_day"]:
        warnings.append(f"Expected {profile['meals_per_day']} meals, got {meals_count}.")

    if foods_count != 6:
        warnings.append(f"Expected 6 foods, got {foods_count}.")

    if cal_gap > 150:
        warnings.append(
            f"Meal plan calories ({total_cals} kcal) deviate from goal "
            f"({calorie_goal} kcal) by {cal_gap} kcal."
        )

    if carb_ratio < 0.25:
        warnings.append(
            f"Carb ratio too low ({carb_ratio:.0%}). "
            "Macros may be inaccurate — try refreshing."
        )

    if macro_math_gap > 100:
        warnings.append(
            f"Macro math mismatch: macros imply {expected_from_macros} kcal "
            f"but meal calories total {total_cals} kcal."
        )

    for w in warnings:
        print(f"[WARN] {w}")

    is_valid = cal_gap <= 150 and carb_ratio >= 0.25
    return is_valid, warnings

# ── Main Route ────────────────────────────────────────────────────────────────

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
            "goal":           latest_log.goal if latest_log and latest_log.goal else "maintain_weight",
            "bmi":            latest_bmi.bmi if latest_bmi else "unknown",
            "bmi_category":   latest_bmi.category if latest_bmi else "Unknown",
            "calorie_goal":   pref.calorie_goal if pref else 2000,
            "meals_per_day":  pref.meals_per_day if pref else 3,
            "diet_type":      pref.diet_type if pref else "none",
            "allergies":      ", ".join(pref.allergies)         if pref and pref.allergies         else "none",
            "avoid_foods":    ", ".join(pref.avoid_foods)       if pref and pref.avoid_foods       else "none",
            "preferred_cuisine": ", ".join(pref.preferred_cuisine) if pref and pref.preferred_cuisine else "any",
            "protein_goal":   pref.protein_goal if pref else 50,
            "carbs_goal":     pref.carbs_goal   if pref else 250,
            "fat_goal":       pref.fat_goal     if pref else 70,
        }

        prompt = _build_prompt(profile)

        system_message = (
            "You are a clinical dietitian AI. You output ONLY valid raw JSON — "
            "no markdown, no code fences, no explanation, no preamble, no postamble. "
            "Your entire response must be a single JSON object parseable by json.loads(). "
            "You must strictly respect all macro math: 1g protein=4 kcal, 1g carbs=4 kcal, "
            "1g fat=9 kcal. Carbohydrates must account for at least 30% of total daily calories."
        )

        # ── Retry loop: up to 2 attempts to get a valid response ─────────────
        MAX_RETRIES = 2
        result       = None
        last_warnings = []

        for attempt in range(MAX_RETRIES):
            print(f"[ATTEMPT {attempt + 1}/{MAX_RETRIES}] Calling Groq API...")

            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                max_tokens=1500,
                temperature=0.4,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user",   "content": prompt},
                ],
            )

            raw    = response.choices[0].message.content.strip()
            raw    = _extract_json(raw)
            parsed = json.loads(raw)
            parsed = _enforce_int_types(parsed)
            parsed = _sanitise_foods(parsed)       # fix misclassified diet_types & flag beverages

            is_valid, last_warnings = _validate_result(parsed, profile)
            result = parsed  # Always keep the latest attempt

            if is_valid:
                print(f"[ATTEMPT {attempt + 1}] Passed validation ✓")
                break

            print(f"[ATTEMPT {attempt + 1}] Failed validation — retrying...")

        # ── Attach user-facing macro warning if macros are still off ─────────
        if last_warnings:
            macro_issues = [
                w for w in last_warnings
                if "Carb" in w or "macro" in w.lower() or "mismatch" in w.lower()
            ]
            if macro_issues:
                result["macro_warning"] = (
                    "Macro distribution may be slightly inaccurate. "
                    "Try refreshing for a more balanced breakdown."
                )

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
                macro_warning  = result.get("macro_warning"),
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
