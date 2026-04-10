"""
Hybrid Nutrition Service for NutriConnect
==========================================
Pipeline:
  1. Groq AI  → parse free-text meal into structured food items (name, qty, unit)
  2. For each item:
     a. Exact / alias match in local FoodItem table
     b. Search Open Food Facts API
     c. Groq AI fallback estimate (if both fail)
  3. Convert portion → grams → multiply per-100g values
  4. Sum totals (calories, protein, carbs, fat)
  5. Cache new nutrition back into FoodItem table for future reuse

Also exposes:
  - estimate_fitness_goal()  → AI-powered weeks-to-goal estimator
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import requests
from groq import Groq

logger = logging.getLogger(__name__)

# ─── Groq client (singleton) ────────────────────────────────────────────────

_groq_client: Optional[Groq] = None


def _get_groq() -> Groq:
    global _groq_client
    if _groq_client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise EnvironmentError("GROQ_API_KEY is not set.")
        _groq_client = Groq(api_key=api_key)
    return _groq_client


def _groq_json(system: str, user: str, max_tokens: int = 800) -> Any:
    """Call Groq and parse JSON from the response."""
    client = _get_groq()
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.1,
        max_tokens=max_tokens,
    )
    raw = resp.choices[0].message.content.strip()

    if "```" in raw:
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]

    return json.loads(raw.strip())


# ─── Unit → grams conversion ────────────────────────────────────────────────

UNIT_GRAMS: Dict[str, float] = {
    "plate": 200.0,
    "serving": 150.0,
    "cup": 240.0,
    "bowl": 180.0,
    "glass": 250.0,
    "piece": 80.0,
    "slice": 30.0,
    "gram": 1.0,
    "grams": 1.0,
    "g": 1.0,
    "kg": 1000.0,
    "ml": 1.0,
    "l": 1000.0,
    "tbsp": 15.0,
    "tsp": 5.0,
    "handful": 40.0,
}

# Bangladeshi / South-Asian portion overrides
BD_PORTION_OVERRIDES: Dict[str, float] = {
    "roti": 35.0,
    "paratha": 80.0,
    "egg": 55.0,
    "banana": 120.0,
    "apple": 182.0,
    "orange": 131.0,
}


def _to_grams(qty: float, unit: str, food_name: str) -> float:
    unit = (unit or "serving").lower().strip()
    name_lower = (food_name or "").lower()

    for key, g in BD_PORTION_OVERRIDES.items():
        if key in name_lower:
            return qty * g

    return qty * UNIT_GRAMS.get(unit, 150.0)


def _parse_quantity_text(quantity_text: str) -> Tuple[float, str]:
    """Parse quantity text like '2 rotis' or '1 cup'."""
    raw = (quantity_text or "").strip()
    if not raw:
        return 1.0, "serving"

    parts = raw.split(None, 1)
    try:
        qty_num = float(parts[0])
        qty_unit = parts[1] if len(parts) > 1 else "serving"
    except (ValueError, IndexError):
        qty_num = 1.0
        qty_unit = raw or "serving"

    return qty_num, str(qty_unit).lower().strip()


def _per_100g_from_totals(
    *,
    calories: Any,
    protein: Any,
    carbs: Any,
    fat: Any,
    grams: float,
    source: str,
) -> Optional[Dict[str, Any]]:
    """Convert stored total nutrition into a reusable per-100g record."""
    if not grams or grams <= 0:
        return None

    factor = 100.0 / grams
    return {
        "calories": round(float(calories or 0) * factor, 4),
        "protein": round(float(protein or 0) * factor, 4),
        "carbs": round(float(carbs or 0) * factor, 4),
        "fat": round(float(fat or 0) * factor, 4),
        "source": source,
        "per_g": 100,
    }


# ─── Local DB lookup ────────────────────────────────────────────────────────

_local_cache: Dict[str, Dict] = {}

COMMON_NUTRITION: Dict[str, Dict] = {
    "rice": {"calories": 130, "protein": 2.7, "carbs": 28.0, "fat": 0.3},
    "white rice": {"calories": 130, "protein": 2.7, "carbs": 28.0, "fat": 0.3},
    "brown rice": {"calories": 112, "protein": 2.6, "carbs": 24.0, "fat": 0.9},
    "chicken curry": {"calories": 185, "protein": 18.0, "carbs": 6.0, "fat": 10.0},
    "chicken": {"calories": 165, "protein": 31.0, "carbs": 0.0, "fat": 3.6},
    "beef": {"calories": 217, "protein": 26.0, "carbs": 0.0, "fat": 12.0},
    "beef curry": {"calories": 210, "protein": 22.0, "carbs": 5.0, "fat": 11.0},
    "mutton": {"calories": 258, "protein": 25.0, "carbs": 0.0, "fat": 17.0},
    "fish": {"calories": 136, "protein": 20.0, "carbs": 0.0, "fat": 5.0},
    "fish curry": {"calories": 145, "protein": 18.0, "carbs": 4.0, "fat": 6.0},
    "egg": {"calories": 68, "protein": 5.5, "carbs": 0.5, "fat": 4.8},
    "dal": {"calories": 116, "protein": 9.0, "carbs": 20.0, "fat": 0.4},
    "lentil": {"calories": 116, "protein": 9.0, "carbs": 20.0, "fat": 0.4},
    "roti": {"calories": 206, "protein": 5.6, "carbs": 42.0, "fat": 2.5},
    "paratha": {"calories": 300, "protein": 5.0, "carbs": 40.0, "fat": 13.0},
    "bread": {"calories": 265, "protein": 9.0, "carbs": 49.0, "fat": 3.2},
    "salad": {"calories": 15, "protein": 1.0, "carbs": 3.0, "fat": 0.2},
    "cucumber": {"calories": 15, "protein": 0.6, "carbs": 3.6, "fat": 0.1},
    "tomato": {"calories": 18, "protein": 0.9, "carbs": 3.9, "fat": 0.2},
    "biryani": {"calories": 210, "protein": 9.0, "carbs": 28.0, "fat": 7.0},
    "khichuri": {"calories": 130, "protein": 5.0, "carbs": 22.0, "fat": 3.0},
    "orange juice": {"calories": 45, "protein": 0.7, "carbs": 10.0, "fat": 0.2},
    "milk": {"calories": 61, "protein": 3.2, "carbs": 4.8, "fat": 3.3},
    "banana": {"calories": 89, "protein": 1.1, "carbs": 23.0, "fat": 0.3},
    "apple": {"calories": 52, "protein": 0.3, "carbs": 14.0, "fat": 0.2},
    "potato": {"calories": 77, "protein": 2.0, "carbs": 17.0, "fat": 0.1},
    "onion": {"calories": 40, "protein": 1.1, "carbs": 9.3, "fat": 0.1},
    "yogurt": {"calories": 59, "protein": 3.5, "carbs": 3.6, "fat": 3.3},
    "dahi": {"calories": 59, "protein": 3.5, "carbs": 3.6, "fat": 3.3},
    "paneer": {"calories": 265, "protein": 18.3, "carbs": 1.2, "fat": 20.8},
    "noodles": {"calories": 138, "protein": 4.5, "carbs": 25.0, "fat": 2.1},
    "pasta": {"calories": 131, "protein": 5.0, "carbs": 25.0, "fat": 1.1},
    "pizza": {"calories": 266, "protein": 11.0, "carbs": 33.0, "fat": 10.0},
    "burger": {"calories": 295, "protein": 17.0, "carbs": 24.0, "fat": 14.0},
    "soup": {"calories": 50, "protein": 3.0, "carbs": 6.0, "fat": 1.5},
    "oats": {"calories": 389, "protein": 17.0, "carbs": 66.0, "fat": 7.0},
    "cornflakes": {"calories": 357, "protein": 8.0, "carbs": 84.0, "fat": 0.4},
}


def _local_lookup(name: str) -> Optional[Dict]:
    """Check in-memory cache first, then COMMON_NUTRITION with fuzzy matching."""
    key = name.lower().strip()
    if key in _local_cache:
        return _local_cache[key]

    if key in COMMON_NUTRITION:
        return {**COMMON_NUTRITION[key], "source": "local_db", "per_g": 100}

    for known, data in COMMON_NUTRITION.items():
        if known in key or key in known:
            return {**data, "source": "local_db", "per_g": 100}

    return None


def _meal_log_lookup(name: str) -> Optional[Dict[str, Any]]:
    """Reuse nutrition previously stored in meal logs before hitting external sources."""
    try:
        from app.models import MealLog

        key = (name or "").lower().strip()
        if not key:
            return None

        logs = (
            MealLog.query
            .filter(MealLog.calories.isnot(None))
            .order_by(MealLog.logged_at.desc())
            .limit(300)
            .all()
        )

        # 1) Exact food_name matches
        for log in logs:
            log_name = (log.food_name or "").lower().strip()
            if log_name != key:
                continue

            qty_num, qty_unit = _parse_quantity_text(log.quantity)
            grams = _to_grams(qty_num, qty_unit, log.food_name or name)
            original_source = log.nutrition_source or "meal_log_cache"

            nutrition = _per_100g_from_totals(
                calories=log.calories,
                protein=log.protein,
                carbs=log.carbs,
                fat=log.fat,
                grams=grams,
                source=original_source,
            )
            if nutrition:
                return nutrition

        # 2) Structured items from parsed_items_json
        for log in logs:
            raw_items = (log.parsed_items_json or "").strip()
            if not raw_items:
                continue

            try:
                items = json.loads(raw_items)
            except Exception:
                continue

            for item in items or []:
                item_name = str(item.get("name") or "").strip()
                if not item_name:
                    continue

                item_key = item_name.lower()
                if item_key != key and item_key not in key and key not in item_key:
                    continue

                grams = float(item.get("grams") or 0)
                if grams <= 0:
                    grams = _to_grams(
                        float(item.get("quantity") or 1.0),
                        str(item.get("unit") or "serving"),
                        item_name,
                    )

                original_source = str(item.get("source") or log.nutrition_source or "meal_log_cache")

                nutrition = _per_100g_from_totals(
                    calories=item.get("calories"),
                    protein=item.get("protein"),
                    carbs=item.get("carbs"),
                    fat=item.get("fat"),
                    grams=grams,
                    source=original_source,
                )
                if nutrition:
                    return nutrition

        # 3) Fuzzy fallback on plain food_name matches
        for log in logs:
            log_name = (log.food_name or "").lower().strip()
            if not log_name or (key not in log_name and log_name not in key):
                continue

            qty_num, qty_unit = _parse_quantity_text(log.quantity)
            grams = _to_grams(qty_num, qty_unit, log.food_name or name)
            original_source = log.nutrition_source or "meal_log_cache"

            nutrition = _per_100g_from_totals(
                calories=log.calories,
                protein=log.protein,
                carbs=log.carbs,
                fat=log.fat,
                grams=grams,
                source=original_source,
            )
            if nutrition:
                return nutrition

    except Exception as e:
        logger.warning(f"Meal log cache lookup failed for '{name}': {e}")

    return None


def _db_lookup(name: str) -> Optional[Dict]:
    """Search FoodItem table in the database."""
    try:
        from app.models import FoodItem

        name_lower = name.lower().strip()
        item = (
            FoodItem.query
            .filter(FoodItem.name.ilike(f"%{name_lower}%"))
            .filter(FoodItem.calories.isnot(None))
            .first()
        )
        if item and item.calories is not None:
            return {
                "calories": float(item.calories or 0),
                "protein": float(item.protein or 0),
                "carbs": float(item.carbs or 0),
                "fat": float(item.fat or 0),
                "source": "food_items_table",
                "per_g": 100,
            }
    except Exception as e:
        logger.warning(f"DB lookup failed for '{name}': {e}")

    return None


def _save_to_db(name: str, nutrition: Dict) -> None:
    """Update an existing FoodItem with discovered nutrition and warm the cache."""
    try:
        from app.models import FoodItem, db

        existing = FoodItem.query.filter(FoodItem.name.ilike(name)).first()
        cache_source = nutrition.get("source", "local_db")

        if existing:
            if existing.calories is None:
                existing.calories = nutrition.get("calories")
                existing.protein = nutrition.get("protein")
                existing.carbs = nutrition.get("carbs")
                existing.fat = nutrition.get("fat")
                db.session.commit()
                logger.info(f"Updated nutrition for FoodItem '{name}' from {nutrition.get('source')}")

            cache_source = "food_items_table"

        _local_cache[name.lower().strip()] = {
            **nutrition,
            "source": cache_source,
            "per_g": 100,
        }

    except Exception as e:
        logger.warning(f"Failed to save nutrition for '{name}': {e}")


# ─── Open Food Facts ────────────────────────────────────────────────────────

OFF_SEARCH = "https://world.openfoodfacts.org/cgi/search.pl"
OFF_TIMEOUT = 5


def _fetch_off(food_name: str) -> Optional[Dict]:
    """Search Open Food Facts and return per-100g nutrition dict."""
    try:
        params = {
            "search_terms": food_name,
            "search_simple": 1,
            "action": "process",
            "json": 1,
            "page_size": 5,
            "fields": "product_name,nutriments",
        }
        r = requests.get(OFF_SEARCH, params=params, timeout=OFF_TIMEOUT)
        data = r.json()
        products = data.get("products") or []

        for p in products:
            n = p.get("nutriments") or {}
            cals = n.get("energy-kcal_100g") or n.get("energy-kcal") or 0
            prot = n.get("proteins_100g", 0)
            carb = n.get("carbohydrates_100g", 0)
            fat = n.get("fat_100g", 0)

            if cals:
                return {
                    "calories": float(cals),
                    "protein": float(prot),
                    "carbs": float(carb),
                    "fat": float(fat),
                    "source": "open_food_facts",
                    "per_g": 100,
                }

    except Exception as e:
        logger.warning(f"Open Food Facts failed for '{food_name}': {e}")

    return None


# ─── AI fallback nutrition estimate ────────────────────────────────────────

def _ai_estimate_nutrition(food_name: str) -> Dict:
    """Ask Groq to estimate per-100g nutrition for an unknown food."""
    system = (
        "You are a nutrition database. Return ONLY valid JSON, no markdown:\n"
        '{"calories":0,"protein":0,"carbs":0,"fat":0}\n'
        "Values are per 100g cooked weight. Be realistic and conservative."
    )
    try:
        result = _groq_json(system, f"Estimate per-100g nutrition for: {food_name}")
        return {
            "calories": float(result.get("calories", 150)),
            "protein": float(result.get("protein", 5)),
            "carbs": float(result.get("carbs", 20)),
            "fat": float(result.get("fat", 5)),
            "source": "ai_estimate",
            "per_g": 100,
        }
    except Exception as e:
        logger.warning(f"AI nutrition estimate failed for '{food_name}': {e}")
        return {
            "calories": 150,
            "protein": 5,
            "carbs": 20,
            "fat": 5,
            "source": "ai_estimate",
            "per_g": 100,
        }


# ─── Main nutrition lookup ─────────────────────────────────────────────────

def get_nutrition(food_name: str) -> Dict:
    """
    Return per-100g nutrition dict + 'source' key.
    Search order: in-process cache → COMMON_NUTRITION → meal log cache
                  → FoodItem table → Open Food Facts → Groq AI estimate
    """
    key = food_name.lower().strip()

    result = _local_lookup(food_name)
    if result:
        _local_cache[key] = result
        return result

    result = _meal_log_lookup(food_name)
    if result:
        _local_cache[key] = result
        return result

    result = _db_lookup(food_name)
    if result:
        _local_cache[key] = result
        return result

    result = _fetch_off(food_name)
    if result:
        _save_to_db(food_name, result)
        return result

    result = _ai_estimate_nutrition(food_name)
    _save_to_db(food_name, result)
    return result


# ─── Step 1: Parse free-text meal with Groq ────────────────────────────────

def parse_meal_text(text: str) -> List[Dict]:
    """
    Use Groq to parse a free-text meal description into structured items.
    Returns a list like: [{"name":"rice","quantity":1.0,"unit":"plate"}, ...]
    """
    system = (
        "You are a meal parser for a nutrition tracker. "
        "Given a meal description, extract individual food items.\n"
        "Return ONLY a valid JSON array, no markdown, no explanation:\n"
        '[{"name":"food name","quantity":1.0,"unit":"plate|serving|cup|piece|gram|glass|bowl|slice"}]\n'
        "Rules:\n"
        "- Each distinct food is a separate entry.\n"
        "- Quantity must be a positive number.\n"
        "- Unit must be one of: plate, serving, cup, piece, gram, glass, bowl, slice, tbsp, tsp.\n"
        "- If no unit is mentioned, use 'serving'.\n"
        "- Do NOT include seasonings, oil, or garnishes unless they are a main ingredient.\n"
        "- Respond with JSON array only."
    )

    try:
        items = _groq_json(system, text, max_tokens=600)
        if not isinstance(items, list):
            raise ValueError("Expected a JSON array")

        cleaned = []
        for item in items:
            if isinstance(item, dict) and item.get("name"):
                cleaned.append(
                    {
                        "name": str(item["name"]).strip(),
                        "quantity": float(item.get("quantity") or 1.0),
                        "unit": str(item.get("unit") or "serving").lower().strip(),
                    }
                )
        return cleaned

    except Exception as e:
        logger.error(f"Meal parse failed: {e}")
        return [{"name": text[:80], "quantity": 1.0, "unit": "serving"}]


# ─── Full analysis pipeline ────────────────────────────────────────────────

SOURCE_LABELS = {
    "meal_log_cache": ("Saved Log", "high"),
    "local_db": ("Local DB", "high"),
    "food_items_table": ("Saved DB", "high"),
    "open_food_facts": ("Global DB", "medium"),
    "ai_estimate": ("Estimated", "low"),
}


def _calculate_totals(items: List[Dict[str, Any]]) -> Dict[str, float]:
    return {
        "calories": round(sum(float(i.get("calories") or 0) for i in items), 1),
        "protein": round(sum(float(i.get("protein") or 0) for i in items), 1),
        "carbs": round(sum(float(i.get("carbs") or 0) for i in items), 1),
        "fat": round(sum(float(i.get("fat") or 0) for i in items), 1),
    }


def _resolve_overall_quality(items: List[Dict[str, Any]]) -> Tuple[str, str]:
    """
    Decide overall source + confidence using weighted quality.
    One estimated item should not make the whole day 'low' automatically.
    """
    if not items:
        return "local_db", "high"

    source_weights = {
        "meal_log_cache": 1.00,
        "local_db": 1.00,
        "food_items_table": 0.95,
        "open_food_facts": 0.80,
        "ai_estimate": 0.45,
    }

    grouped_totals = {
        "local_db": 0.0,
        "open_food_facts": 0.0,
        "ai_estimate": 0.0,
    }

    weighted_score = 0.0
    total_basis = 0.0
    ai_item_count = 0

    for item in items:
        source = str(item.get("source") or "ai_estimate")

        basis = float(item.get("calories") or 0)
        if basis <= 0:
            basis = float(item.get("grams") or 0)
        if basis <= 0:
            basis = 1.0

        weighted_score += source_weights.get(source, 0.45) * basis
        total_basis += basis

        if source in ("meal_log_cache", "local_db", "food_items_table"):
            grouped_totals["local_db"] += basis
        elif source == "open_food_facts":
            grouped_totals["open_food_facts"] += basis
        else:
            grouped_totals["ai_estimate"] += basis
            ai_item_count += 1

    avg_score = weighted_score / total_basis if total_basis else 1.0
    overall_source = max(grouped_totals, key=grouped_totals.get)

    if grouped_totals["ai_estimate"] == 0 and avg_score >= 0.92:
        confidence = "high"
    elif avg_score >= 0.68 and ai_item_count <= max(1, len(items) // 3):
        confidence = "medium"
    else:
        confidence = "low"

    return overall_source, confidence


def _enrich_item(name: str, qty: float, unit: str) -> Dict[str, Any]:
    grams = _to_grams(qty, unit, name)
    nut = get_nutrition(name)
    per_g = nut.get("per_g", 100) or 100
    mult = grams / per_g if per_g else 1

    source = nut.get("source", "ai_estimate")
    label, confidence = SOURCE_LABELS.get(source, ("Unknown", "low"))

    return {
        "name": name,
        "quantity": qty,
        "unit": unit,
        "grams": round(grams, 1),
        "calories": round(float(nut.get("calories", 0)) * mult, 1),
        "protein": round(float(nut.get("protein", 0)) * mult, 1),
        "carbs": round(float(nut.get("carbs", 0)) * mult, 1),
        "fat": round(float(nut.get("fat", 0)) * mult, 1),
        "source": source,
        "source_label": label,
        "confidence": confidence,
        "is_ai": source == "ai_estimate",
    }


def analyze_meal(raw_text: str) -> Dict[str, Any]:
    """
    Full pipeline:
      raw_text → parse items → fetch nutrition → calculate totals
    Returns a dict with 'items', 'totals', and source metadata.
    """
    parsed_items = parse_meal_text(raw_text)
    enriched = [_enrich_item(item["name"], item["quantity"], item["unit"]) for item in parsed_items]
    totals = _calculate_totals(enriched)
    overall_source, overall_confidence = _resolve_overall_quality(enriched)

    return {
        "items": enriched,
        "totals": totals,
        "overall_source": overall_source,
        "overall_confidence": overall_confidence,
        "is_ai_estimated": overall_source == "ai_estimate",
    }


def analyze_logged_meals(user_id: int, log_date, meal_type: Optional[str] = None) -> Dict[str, Any]:
    """Calculate nutrition totals directly from saved meal logs for a specific date."""
    from app.models import MealLog, db

    query = MealLog.query.filter_by(user_id=user_id, log_date=log_date)
    if meal_type:
        query = query.filter_by(meal_type=meal_type)

    logs = query.order_by(MealLog.logged_at.asc()).all()
    if not logs:
        raise ValueError("No saved meal logs were found for the selected filters.")

    enriched: List[Dict[str, Any]] = []
    refreshed_logs = 0

    for log in logs:
        qty_num, qty_unit = _parse_quantity_text(log.quantity)
        grams = _to_grams(qty_num, qty_unit, log.food_name or "meal")

        if log.has_nutrition:
            source = log.nutrition_source or "meal_log_cache"
            label, confidence = SOURCE_LABELS.get(source, ("Saved Log", log.nutrition_confidence or "high"))

            item = {
                "name": log.food_name,
                "quantity": qty_num,
                "unit": qty_unit,
                "display_quantity": log.quantity,
                "meal_type": log.meal_type,
                "grams": round(grams, 1),
                "calories": round(float(log.calories or 0), 1),
                "protein": round(float(log.protein or 0), 1),
                "carbs": round(float(log.carbs or 0), 1),
                "fat": round(float(log.fat or 0), 1),
                "source": source,
                "source_label": label,
                "confidence": log.nutrition_confidence or confidence,
                "is_ai": source == "ai_estimate",
            }
        else:
            item = _enrich_item(log.food_name, qty_num, qty_unit)
            item["display_quantity"] = log.quantity
            item["meal_type"] = log.meal_type

            log.calories = item["calories"]
            log.protein = item["protein"]
            log.carbs = item["carbs"]
            log.fat = item["fat"]
            log.nutrition_source = item["source"]
            log.is_ai_estimated = item["is_ai"]
            log.nutrition_confidence = item["confidence"]
            refreshed_logs += 1

        enriched.append(item)

    if refreshed_logs:
        db.session.commit()

    totals = _calculate_totals(enriched)
    overall_source, overall_confidence = _resolve_overall_quality(enriched)

    labels = {
        "breakfast": "Breakfast",
        "lunch": "Lunch",
        "dinner": "Dinner",
        "snack": "Snack",
    }

    return {
        "items": enriched,
        "totals": totals,
        "overall_source": overall_source,
        "overall_confidence": overall_confidence,
        "is_ai_estimated": overall_source == "ai_estimate",
        "log_count": len(logs),
        "filled_logs_count": refreshed_logs,
        "log_date": log_date.strftime("%Y-%m-%d"),
        "meal_type": meal_type or "all",
        "meal_type_label": labels.get(meal_type, "All Meals"),
    }


# ─── Fitness Goal Estimator ────────────────────────────────────────────────

ACTIVITY_LABELS = {
    1.2: "Sedentary (office job, no exercise)",
    1.375: "Lightly active (light exercise 1–3 days/week)",
    1.55: "Moderately active (moderate exercise 3–5 days/week)",
    1.725: "Very active (hard exercise 6–7 days/week)",
    1.9: "Extra active (physical job + hard exercise)",
}


def estimate_fitness_goal(
    current_weight: float,
    target_weight: float,
    height_cm: float,
    age: int,
    gender: str,
    activity_level: float,
    daily_calories: int,
) -> Dict[str, Any]:
    """
    Use Mifflin-St Jeor BMR → TDEE → calorie balance,
    then ask Groq for a rich goal timeline + personalised advice.
    Returns a dict with calculation results and AI narrative.
    """
    if gender.lower() == "female":
        bmr = 10 * current_weight + 6.25 * height_cm - 5 * age - 161
    else:
        bmr = 10 * current_weight + 6.25 * height_cm - 5 * age + 5

    tdee = round(bmr * activity_level)
    daily_balance = daily_calories - tdee
    weight_change = target_weight - current_weight

    going_wrong_way = (weight_change < 0 and daily_balance > 0) or (
        weight_change > 0 and daily_balance < 0
    )

    KCAL_PER_KG = 7700.0

    if daily_balance == 0:
        weeks_raw = float("inf")
    else:
        weeks_raw = abs(weight_change * KCAL_PER_KG / (daily_balance * 7))

    weeks_estimated = round(weeks_raw) if weeks_raw != float("inf") and weeks_raw < 520 else None

    height_m = height_cm / 100
    bmi_current = round(current_weight / (height_m ** 2), 1)
    bmi_target = round(target_weight / (height_m ** 2), 1)

    weekly_change_kg = round(abs(daily_balance * 7 / KCAL_PER_KG), 2)

    if going_wrong_way:
        feasibility = "counter-productive"
    elif weekly_change_kg > 1.0:
        feasibility = "very aggressive"
    elif weekly_change_kg > 0.75:
        feasibility = "aggressive"
    elif weekly_change_kg >= 0.25:
        feasibility = "realistic"
    else:
        feasibility = "very slow"

    activity_label = ACTIVITY_LABELS.get(activity_level, f"PAL {activity_level}")

    milestones = []
    if weeks_estimated and weeks_estimated <= 104:
        step = -weekly_change_kg if weight_change < 0 else weekly_change_kg
        for w in range(4, weeks_estimated + 1, 4):
            projected = round(current_weight + step * w, 1)
            milestones.append({"week": w, "weight_kg": projected})
        milestones.append({"week": weeks_estimated, "weight_kg": target_weight})

    ai_data: Dict[str, Any] = {}
    try:
        system = (
            "You are a certified fitness and nutrition coach. "
            "Based on the user's stats, give precise, personalised advice. "
            "Return ONLY valid JSON, no markdown:\n"
            "{\n"
            '  "recommendation": "2-3 sentence personalised advice mentioning their specific numbers",\n'
            '  "nutrition_tip": "One concrete daily nutrition action",\n'
            '  "exercise_tip": "One concrete weekly exercise recommendation",\n'
            '  "risk_warning": "One sentence if the plan is risky, else empty string"\n'
            "}"
        )
        user_msg = (
            f"User stats:\n"
            f"  Gender: {gender}, Age: {age}\n"
            f"  Current weight: {current_weight} kg, Target weight: {target_weight} kg\n"
            f"  Height: {height_cm} cm\n"
            f"  Current BMI: {bmi_current} → Target BMI: {bmi_target}\n"
            f"  TDEE: {tdee} kcal/day, Daily intake: {daily_calories} kcal/day\n"
            f"  Daily balance: {daily_balance:+d} kcal (negative = deficit)\n"
            f"  Activity: {activity_label}\n"
            f"  Estimated weeks to goal: {weeks_estimated or 'N/A'}\n"
            f"  Weekly change rate: {weekly_change_kg} kg/week\n"
            f"  Feasibility: {feasibility}\n"
            f"  Goal direction: {'lose weight' if weight_change < 0 else 'gain weight'}"
        )
        ai_data = _groq_json(system, user_msg, max_tokens=500)
    except Exception as e:
        logger.warning(f"Fitness goal AI failed: {e}")
        ai_data = {
            "recommendation": (
                f"Your TDEE is {tdee} kcal/day and you're consuming {daily_calories} kcal/day — "
                f"a daily {'deficit' if daily_balance < 0 else 'surplus'} of {abs(daily_balance)} kcal. "
                f"At this rate you'll reach your target in approximately {weeks_estimated or '?'} weeks."
            ),
            "nutrition_tip": "Track your meals daily and aim for 1.6–2.2 g protein per kg body weight.",
            "exercise_tip": "Include 3–4 strength training sessions per week to preserve muscle mass.",
            "risk_warning": ""
            if feasibility in ("realistic", "aggressive")
            else "Your current calorie balance may be unsafe — consult a healthcare professional.",
        }

    return {
        "current_weight": current_weight,
        "target_weight": target_weight,
        "height_cm": height_cm,
        "age": age,
        "gender": gender,
        "activity_level": activity_level,
        "daily_calories": daily_calories,
        "bmr": round(bmr),
        "tdee": tdee,
        "daily_balance": daily_balance,
        "weekly_change_kg": weekly_change_kg,
        "bmi_current": bmi_current,
        "bmi_target": bmi_target,
        "weeks_estimated": weeks_estimated,
        "feasibility": feasibility,
        "going_wrong_way": going_wrong_way,
        "milestones": milestones,
        "recommendation": ai_data.get("recommendation", ""),
        "nutrition_tip": ai_data.get("nutrition_tip", ""),
        "exercise_tip": ai_data.get("exercise_tip", ""),
        "risk_warning": ai_data.get("risk_warning", ""),
    }