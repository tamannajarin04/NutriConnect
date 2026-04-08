import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from flask import current_app
from sqlalchemy import func, or_

from app.models import db, FoodItem, FoodNutritionCache


BANGLADESHI_ALIASES = {
    "bhat": "rice",
    "plain bhat": "rice",
    "plain rice": "rice",
    "fried rice": "fried rice",
    "dal": "lentil soup",
    "daal": "lentil soup",
    "khichuri": "khichdi",
    "khichdi": "khichdi",
    "roti": "flatbread",
    "chapati": "flatbread",
    "parota": "paratha",
    "paratha": "paratha",
    "beef bhuna": "beef curry",
    "beef curry": "beef curry",
    "chicken curry": "chicken curry",
    "egg fry": "fried egg",
    "alu bhorta": "mashed potato",
    "dim": "egg",
    "ilish": "hilsa fish",
}

UNIT_TO_GRAMS = {
    "g": 1,
    "gram": 1,
    "grams": 1,
    "kg": 1000,
    "cup": 240,
    "cups": 240,
    "small cup": 180,
    "bowl": 250,
    "bowls": 250,
    "small bowl": 180,
    "medium bowl": 250,
    "large bowl": 320,
    "plate": 320,
    "plates": 320,
    "small plate": 220,
    "glass": 250,
    "mug": 300,
    "serving": 150,
    "servings": 150,
    "piece": 90,
    "pieces": 90,
    "slice": 30,
    "slices": 30,
    "egg": 50,
    "eggs": 50,
    "banana": 118,
    "apple": 182,
}

COMMON_BASELINES_PER_100G = {
    "bread": {"calories": 265, "protein": 9.0, "carbs": 49.0, "fat": 3.2},
    "banana": {"calories": 89, "protein": 1.1, "carbs": 22.8, "fat": 0.3},
    "egg": {"calories": 143, "protein": 12.6, "carbs": 0.7, "fat": 9.5},
    "fried egg": {"calories": 196, "protein": 13.6, "carbs": 1.1, "fat": 15.0},
    "rice": {"calories": 130, "protein": 2.7, "carbs": 28.0, "fat": 0.3},
    "fried rice": {"calories": 168, "protein": 4.0, "carbs": 21.0, "fat": 7.0},
    "lentil soup": {"calories": 82, "protein": 5.0, "carbs": 13.5, "fat": 0.4},
    "flatbread": {"calories": 297, "protein": 9.0, "carbs": 55.0, "fat": 3.3},
    "paratha": {"calories": 326, "protein": 7.0, "carbs": 40.0, "fat": 15.0},
    "chicken curry": {"calories": 180, "protein": 15.0, "carbs": 5.0, "fat": 11.0},
    "beef curry": {"calories": 220, "protein": 18.0, "carbs": 4.0, "fat": 15.0},
    "mashed potato": {"calories": 88, "protein": 1.9, "carbs": 20.0, "fat": 0.1},
    "salad": {"calories": 33, "protein": 1.3, "carbs": 6.4, "fat": 0.2},
    "cucumber salad": {"calories": 20, "protein": 0.8, "carbs": 4.0, "fat": 0.1},
    "apple": {"calories": 52, "protein": 0.3, "carbs": 14.0, "fat": 0.2},
    "milk": {"calories": 61, "protein": 3.2, "carbs": 4.8, "fat": 3.3},
    "yogurt": {"calories": 61, "protein": 3.5, "carbs": 4.7, "fat": 3.3},
    "tea": {"calories": 1, "protein": 0.0, "carbs": 0.2, "fat": 0.0},
}

PER_100G_SOURCES = {
    "openfoodfacts",
    "usda",
    "ollama_estimate",
    "ai_estimate",
    "common_baseline",
    "compound_resolved",
    "cached_external",
    "cached_ai",
    "legacy_fooditem_migration",
}

_http = requests.Session()
_http.headers.update({"User-Agent": "NutriConnect/1.0"})


def normalize_food_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9\s/&,+-]", " ", (name or "").strip().lower())
    cleaned = cleaned.replace("+", " + ").replace("/", " / ").replace("&", " & ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return BANGLADESHI_ALIASES.get(cleaned, cleaned)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def ollama_chat_json(prompt: str, schema: Dict[str, Any], timeout: int = 18) -> Optional[Dict[str, Any]]:
    base_url = (current_app.config.get("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
    model = current_app.config.get("OLLAMA_MODEL") or "qwen3:4b"

    payload = {
        "model": model,
        "stream": False,
        "format": schema,
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": "Return only valid JSON that follows the provided schema."},
            {"role": "user", "content": prompt},
        ],
    }

    try:
        response = _http.post(f"{base_url}/api/chat", json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        content = (((data or {}).get("message") or {}).get("content") or "").strip()
        return json.loads(content) if content else None
    except Exception:
        return None


def parse_quantity_fallback(quantity_text: str, food_name: str) -> Dict[str, Any]:
    quantity_text = (quantity_text or "").strip().lower()
    match = re.search(r"(\d+(?:\.\d+)?)", quantity_text)
    amount = safe_float(match.group(1), 1.0) if match else 1.0

    unit = "serving"
    for known_unit in sorted(UNIT_TO_GRAMS.keys(), key=len, reverse=True):
        if known_unit in quantity_text:
            unit = known_unit
            break

    grams = amount * UNIT_TO_GRAMS.get(unit, 150)

    return {
        "food_name": food_name,
        "normalized_name": normalize_food_name(food_name),
        "amount": round(amount, 2),
        "unit": unit,
        "estimated_grams": round(grams, 2),
        "confidence": "medium",
    }


def parse_meal_logs_with_ollama(logs: List[Any]) -> List[Dict[str, Any]]:
    if not logs:
        return []

    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "log_id": {"type": "integer"},
                        "food_name": {"type": "string"},
                        "normalized_name": {"type": "string"},
                        "amount": {"type": "number"},
                        "unit": {"type": "string"},
                        "estimated_grams": {"type": "number"},
                        "confidence": {"type": "string"},
                    },
                    "required": [
                        "log_id",
                        "food_name",
                        "normalized_name",
                        "amount",
                        "unit",
                        "estimated_grams",
                        "confidence",
                    ],
                },
            }
        },
        "required": ["items"],
    }

    lines = []
    for log in logs:
        lines.append(f"ID={log.id} | meal_type={log.meal_type} | quantity={log.quantity} | food={log.food_name}")

    prompt = f"""Parse these meal log rows into structured nutrition-analysis items.
Use practical household portion estimates in grams.
Normalize Bangladeshi foods when useful (for example bhat->rice, dal->lentil soup, roti->flatbread).
Return JSON only.

Rows:
{chr(10).join(lines)}
"""

    parsed = ollama_chat_json(prompt, schema)
    parsed_items = []
    by_id = {log.id: log for log in logs}

    if parsed and isinstance(parsed.get("items"), list):
        for item in parsed["items"]:
            log = by_id.get(item.get("log_id"))
            if not log:
                continue
            parsed_items.append({
                **item,
                "meal_type": log.meal_type,
                "quantity_text": log.quantity,
            })

    if parsed_items:
        return parsed_items

    fallback = []
    for log in logs:
        item = parse_quantity_fallback(log.quantity, log.food_name)
        item.update({
            "log_id": log.id,
            "meal_type": log.meal_type,
            "quantity_text": log.quantity,
        })
        fallback.append(item)
    return fallback


def _ordered_food_query(query):
    if hasattr(FoodItem, "created_at"):
        return query.order_by(FoodItem.created_at.desc())
    if hasattr(FoodItem, "id"):
        return query.order_by(FoodItem.id.desc())
    return query


def _ordered_cache_query(query):
    if hasattr(FoodNutritionCache, "updated_at"):
        return query.order_by(FoodNutritionCache.updated_at.desc())
    if hasattr(FoodNutritionCache, "created_at"):
        return query.order_by(FoodNutritionCache.created_at.desc())
    if hasattr(FoodNutritionCache, "id"):
        return query.order_by(FoodNutritionCache.id.desc())
    return query


def find_matching_fooditem_record(food_name: str) -> Optional[Any]:
    normalized = normalize_food_name(food_name)
    if not normalized:
        return None

    record = _ordered_food_query(
        FoodItem.query.filter(func.lower(FoodItem.name) == normalized)
    ).first()
    if record:
        return record

    record = _ordered_food_query(
        FoodItem.query.filter(FoodItem.name.ilike(f"%{normalized}%"))
    ).first()
    if record:
        return record

    tokens = [token for token in normalized.split() if len(token) > 2]
    for token in tokens:
        record = _ordered_food_query(
            FoodItem.query.filter(FoodItem.name.ilike(f"%{token}%"))
        ).first()
        if record:
            return record

    return None


def find_matching_food_cache_record(food_name: str) -> Optional[Any]:
    normalized = normalize_food_name(food_name)
    if not normalized:
        return None

    record = _ordered_cache_query(
        FoodNutritionCache.query.filter(FoodNutritionCache.normalized_name == normalized)
    ).first()
    if record:
        return record

    record = _ordered_cache_query(
        FoodNutritionCache.query.filter(func.lower(FoodNutritionCache.name) == normalized)
    ).first()
    if record:
        return record

    record = _ordered_cache_query(
        FoodNutritionCache.query.filter(
            or_(
                FoodNutritionCache.normalized_name.ilike(f"%{normalized}%"),
                FoodNutritionCache.name.ilike(f"%{normalized}%"),
            )
        )
    ).first()
    if record:
        return record

    tokens = [token for token in normalized.split() if len(token) > 2]
    for token in tokens:
        record = _ordered_cache_query(
            FoodNutritionCache.query.filter(
                or_(
                    FoodNutritionCache.normalized_name.ilike(f"%{token}%"),
                    FoodNutritionCache.name.ilike(f"%{token}%"),
                )
            )
        ).first()
        if record:
            return record

    return None


def has_nutrition_values(record: Any) -> bool:
    return any(
        getattr(record, field, None) not in (None, "")
        for field in ["calories", "protein", "carbs", "fat"]
    )


def is_external_cached_record(record: Any) -> bool:
    source = str(getattr(record, "nutrition_source", "") or "").strip().lower()
    if source in PER_100G_SOURCES:
        return True

    if hasattr(record, "nutrition_basis"):
        basis = str(getattr(record, "nutrition_basis", "") or "").strip().lower()
        if basis in {"100g", "per_100g", "per100g"}:
            return True

    if hasattr(record, "is_ai_estimated") and getattr(record, "is_ai_estimated"):
        return True

    if hasattr(record, "ai_estimated") and getattr(record, "ai_estimated"):
        return True

    return False


def lookup_cached_food(food_name: str) -> Optional[Dict[str, Any]]:
    record = find_matching_food_cache_record(food_name)
    if not record or not has_nutrition_values(record):
        return None

    return {
        "name": record.name,
        "calories": safe_float(getattr(record, "calories", 0)),
        "protein": safe_float(getattr(record, "protein", 0)),
        "carbs": safe_float(getattr(record, "carbs", 0)),
        "fat": safe_float(getattr(record, "fat", 0)),
        "source": getattr(record, "nutrition_source", None) or "cached_ai",
        "confidence": getattr(record, "nutrition_confidence", None) or "high",
        "per": getattr(record, "nutrition_basis", None) or "100g",
    }


def lookup_local_food(food_name: str) -> Optional[Dict[str, Any]]:
    record = find_matching_fooditem_record(food_name)
    if not record or not has_nutrition_values(record):
        return None

    if is_external_cached_record(record):
        per_basis = "100g"
        source = getattr(record, "nutrition_source", None) or "local_cache"
        confidence = getattr(record, "nutrition_confidence", None) or "high"
    else:
        per_basis = "serving"
        source = "local_db"
        confidence = "high"

    return {
        "name": record.name,
        "calories": safe_float(getattr(record, "calories", 0)),
        "protein": safe_float(getattr(record, "protein", 0)),
        "carbs": safe_float(getattr(record, "carbs", 0)),
        "fat": safe_float(getattr(record, "fat", 0)),
        "source": source,
        "confidence": confidence,
        "per": per_basis,
    }


def fetch_openfoodfacts(food_name: str) -> Optional[Dict[str, Any]]:
    query = normalize_food_name(food_name)
    if not query:
        return None

    url = "https://world.openfoodfacts.org/cgi/search.pl"
    params = {
        "search_terms": query,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": 6,
    }

    try:
        response = _http.get(url, params=params, timeout=6)
        response.raise_for_status()
        products = (response.json() or {}).get("products") or []
    except Exception:
        return None

    for product in products:
        nutriments = product.get("nutriments") or {}
        calories = nutriments.get("energy-kcal_100g") or nutriments.get("energy-kcal")
        protein = nutriments.get("proteins_100g")
        carbs = nutriments.get("carbohydrates_100g")
        fat = nutriments.get("fat_100g")

        if calories is None and protein is None and carbs is None and fat is None:
            continue

        product_name = (
            product.get("product_name_en")
            or product.get("product_name")
            or product.get("generic_name")
            or query
        )

        return {
            "name": product_name,
            "calories": safe_float(calories),
            "protein": safe_float(protein),
            "carbs": safe_float(carbs),
            "fat": safe_float(fat),
            "source": "openfoodfacts",
            "confidence": "medium",
            "per": "100g",
        }

    return None


def common_baseline_lookup(food_name: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_food_name(food_name)
    if not normalized:
        return None

    if normalized in COMMON_BASELINES_PER_100G:
        values = COMMON_BASELINES_PER_100G[normalized]
        return {
            "name": normalized,
            "calories": safe_float(values["calories"]),
            "protein": safe_float(values["protein"]),
            "carbs": safe_float(values["carbs"]),
            "fat": safe_float(values["fat"]),
            "source": "common_baseline",
            "confidence": "medium",
            "per": "100g",
        }

    tokens = [t for t in normalized.split() if len(t) > 2]
    for token in tokens:
        if token in COMMON_BASELINES_PER_100G:
            values = COMMON_BASELINES_PER_100G[token]
            return {
                "name": token,
                "calories": safe_float(values["calories"]),
                "protein": safe_float(values["protein"]),
                "carbs": safe_float(values["carbs"]),
                "fat": safe_float(values["fat"]),
                "source": "common_baseline",
                "confidence": "medium",
                "per": "100g",
            }

    return None


def estimate_with_ollama(food_name: str) -> Optional[Dict[str, Any]]:
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "calories": {"type": "number"},
            "protein": {"type": "number"},
            "carbs": {"type": "number"},
            "fat": {"type": "number"},
            "confidence": {"type": "string"},
        },
        "required": ["name", "calories", "protein", "carbs", "fat", "confidence"],
    }

    prompt = f"""Estimate typical nutrition per 100 grams for this food: {food_name}.
Use common cooked-edible values, not raw ingredients.
Prefer South Asian or Bangladeshi style when the name suggests it.
Return JSON only.
"""

    result = ollama_chat_json(prompt, schema, timeout=12)
    if not result:
        return None

    return {
        "name": result.get("name") or food_name,
        "calories": safe_float(result.get("calories")),
        "protein": safe_float(result.get("protein")),
        "carbs": safe_float(result.get("carbs")),
        "fat": safe_float(result.get("fat")),
        "source": "ollama_estimate",
        "confidence": result.get("confidence") or "low",
        "per": "100g",
    }


def split_compound_food_name(food_name: str) -> List[str]:
    normalized = normalize_food_name(food_name)
    if not normalized:
        return []

    parts = re.split(r"\s*(?:&|,|\+|/|\band\b|\bwith\b)\s*", normalized)
    cleaned_parts = []
    for part in parts:
        part = normalize_food_name(part)
        part = re.sub(r"\b(of|the|a|an)\b", " ", part)
        part = re.sub(r"\s+", " ", part).strip()
        if part and len(part) > 1:
            cleaned_parts.append(part)

    seen = set()
    unique = []
    for item in cleaned_parts:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def persist_nutrition_to_cache(search_name: str, nutrition: Dict[str, Any]) -> bool:
    if not nutrition or nutrition.get("source") in {"local_db", "unresolved"}:
        return False

    normalized_name = normalize_food_name(search_name or nutrition.get("name") or "")
    if not normalized_name:
        return False

    record = find_matching_food_cache_record(normalized_name)
    if not record:
        record = FoodNutritionCache(
            name=nutrition.get("name") or search_name,
            normalized_name=normalized_name,
        )
        db.session.add(record)

    try:
        record.name = nutrition.get("name") or search_name or normalized_name
        record.normalized_name = normalized_name
        record.calories = safe_float(nutrition.get("calories"))
        record.protein = safe_float(nutrition.get("protein"))
        record.carbs = safe_float(nutrition.get("carbs"))
        record.fat = safe_float(nutrition.get("fat"))
        record.nutrition_source = nutrition.get("source") or "ai_estimate"
        record.nutrition_confidence = nutrition.get("confidence") or "medium"
        record.nutrition_basis = nutrition.get("per") or "100g"
        record.is_ai_estimated = record.nutrition_source in {
            "ollama_estimate",
            "compound_resolved",
            "ai_estimate",
        }
        record.last_nutrition_sync = datetime.utcnow()

        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


def resolve_single_food_nutrition(food_name: str, persist: bool = True, allow_ai: bool = True) -> Dict[str, Any]:
    cached = lookup_cached_food(food_name)
    if cached:
        return cached

    local = lookup_local_food(food_name)
    if local:
        return local

    external = fetch_openfoodfacts(food_name)
    if external:
        if persist:
            persist_nutrition_to_cache(food_name, external)
        return external

    baseline = common_baseline_lookup(food_name)
    if baseline:
        if persist:
            persist_nutrition_to_cache(food_name, baseline)
        return baseline

    if allow_ai:
        estimated = estimate_with_ollama(food_name)
        if estimated:
            if persist:
                persist_nutrition_to_cache(food_name, estimated)
            return estimated

    return {
        "name": food_name,
        "calories": 0.0,
        "protein": 0.0,
        "carbs": 0.0,
        "fat": 0.0,
        "source": "unresolved",
        "confidence": "low",
        "per": "100g",
    }


def resolve_compound_food_nutrition(food_name: str, persist: bool = True) -> Optional[Dict[str, Any]]:
    parts = split_compound_food_name(food_name)
    if len(parts) < 2:
        return None

    resolved_parts = []
    for part in parts:
        result = resolve_single_food_nutrition(part, persist=persist, allow_ai=False)
        if result.get("source") == "unresolved":
            result = resolve_single_food_nutrition(part, persist=persist, allow_ai=True)

        if result.get("source") != "unresolved":
            resolved_parts.append(result)

    if not resolved_parts:
        return None

    count = len(resolved_parts)
    combined = {
        "name": normalize_food_name(food_name),
        "calories": round(sum(safe_float(x["calories"]) for x in resolved_parts) / count, 2),
        "protein": round(sum(safe_float(x["protein"]) for x in resolved_parts) / count, 2),
        "carbs": round(sum(safe_float(x["carbs"]) for x in resolved_parts) / count, 2),
        "fat": round(sum(safe_float(x["fat"]) for x in resolved_parts) / count, 2),
        "source": "compound_resolved",
        "confidence": "medium" if count == len(parts) else "low",
        "per": "100g",
    }

    if persist:
        persist_nutrition_to_cache(food_name, combined)

    return combined


def find_food_nutrition(food_name: str, persist: bool = True) -> Dict[str, Any]:
    exact_cache = lookup_cached_food(food_name)
    if exact_cache:
        return exact_cache

    exact_local = lookup_local_food(food_name)
    if exact_local:
        return exact_local

    compound = resolve_compound_food_nutrition(food_name, persist=persist)
    if compound:
        return compound

    return resolve_single_food_nutrition(food_name, persist=persist, allow_ai=True)


def calculate_item_totals(parsed_item: Dict[str, Any], nutrition: Dict[str, Any]) -> Dict[str, Any]:
    amount = max(safe_float(parsed_item.get("amount"), 1.0), 0.1)
    grams = max(safe_float(parsed_item.get("estimated_grams"), 150.0), 1.0)

    if nutrition.get("per") == "serving":
        multiplier = amount
    else:
        multiplier = grams / 100.0

    calories = round(safe_float(nutrition.get("calories")) * multiplier, 2)
    protein = round(safe_float(nutrition.get("protein")) * multiplier, 2)
    carbs = round(safe_float(nutrition.get("carbs")) * multiplier, 2)
    fat = round(safe_float(nutrition.get("fat")) * multiplier, 2)

    return {
        "food_name": parsed_item.get("food_name"),
        "normalized_name": parsed_item.get("normalized_name"),
        "meal_type": parsed_item.get("meal_type"),
        "quantity_text": parsed_item.get("quantity_text"),
        "amount": amount,
        "unit": parsed_item.get("unit"),
        "estimated_grams": round(grams, 2),
        "matched_name": nutrition.get("name"),
        "source": nutrition.get("source"),
        "confidence": nutrition.get("confidence"),
        "calories": calories,
        "protein": protein,
        "carbs": carbs,
        "fat": fat,
    }


def analyze_meal_logs(logs: List[Any]) -> Dict[str, Any]:
    parsed_items = parse_meal_logs_with_ollama(logs)
    results = []
    lookup_cache: Dict[str, Dict[str, Any]] = {}
    persisted_count = 0

    for parsed in parsed_items:
        lookup_name = parsed.get("normalized_name") or parsed.get("food_name") or ""
        cache_key = normalize_food_name(lookup_name)

        if cache_key in lookup_cache:
            nutrition = lookup_cache[cache_key]
        else:
            before_cached = lookup_cached_food(lookup_name)
            before_local = lookup_local_food(lookup_name)

            nutrition = before_cached or before_local or find_food_nutrition(lookup_name, persist=True)
            lookup_cache[cache_key] = nutrition

            if not before_cached and nutrition.get("source") not in {"unresolved", "local_db"}:
                persisted_count += 1

        results.append(calculate_item_totals(parsed, nutrition))

    totals = {
        "calories": round(sum(item["calories"] for item in results), 2),
        "protein": round(sum(item["protein"] for item in results), 2),
        "carbs": round(sum(item["carbs"] for item in results), 2),
        "fat": round(sum(item["fat"] for item in results), 2),
    }

    per_meal = {}
    for meal_type in ["breakfast", "lunch", "dinner", "snack"]:
        meal_items = [item for item in results if item.get("meal_type") == meal_type]
        per_meal[meal_type] = {
            "count": len(meal_items),
            "calories": round(sum(item["calories"] for item in meal_items), 2),
        }

    return {
        "items": results,
        "totals": totals,
        "per_meal": per_meal,
        "item_count": len(results),
        "resolved_count": len([item for item in results if item.get("source") != "unresolved"]),
        "persisted_count": persisted_count,
    }


def apply_nutrition_to_fooditem_record(item: FoodItem, nutrition: Dict[str, Any]) -> bool:
    if not item or not nutrition or nutrition.get("source") == "unresolved":
        return False

    item.calories = safe_float(nutrition.get("calories"))
    item.protein = safe_float(nutrition.get("protein"))
    item.carbs = safe_float(nutrition.get("carbs"))
    item.fat = safe_float(nutrition.get("fat"))

    if hasattr(item, "nutrition_source"):
        item.nutrition_source = nutrition.get("source")

    if hasattr(item, "nutrition_confidence"):
        item.nutrition_confidence = nutrition.get("confidence")

    if hasattr(item, "nutrition_basis"):
        item.nutrition_basis = nutrition.get("per", "100g")

    if hasattr(item, "is_ai_estimated"):
        item.is_ai_estimated = nutrition.get("source") in {"ollama_estimate", "compound_resolved"}

    if hasattr(item, "ai_estimated"):
        item.ai_estimated = nutrition.get("source") in {"ollama_estimate", "compound_resolved"}

    if hasattr(item, "last_nutrition_sync"):
        item.last_nutrition_sync = datetime.utcnow()

    return True


def auto_enrich_stored_fooditems(limit: int = 50) -> Dict[str, Any]:
    missing_items = (
        FoodItem.query
        .filter(
            or_(
                FoodItem.calories.is_(None),
                FoodItem.protein.is_(None),
                FoodItem.carbs.is_(None),
                FoodItem.fat.is_(None),
            )
        )
        .limit(limit)
        .all()
    )

    enriched = 0
    failed = 0

    for item in missing_items:
        try:
            nutrition = find_food_nutrition(item.name, persist=True)
            if nutrition.get("source") != "unresolved":
                apply_nutrition_to_fooditem_record(item, nutrition)
                db.session.commit()
                enriched += 1
            else:
                failed += 1
        except Exception:
            db.session.rollback()
            failed += 1

    return {
        "checked": len(missing_items),
        "enriched": enriched,
        "failed": failed,
    }


def migrate_legacy_ai_fooditems_to_cache(delete_from_food_items: bool = True) -> Dict[str, Any]:
    """
    One-time cleanup helper.
    Moves old AI-created/cache-like FoodItem rows into FoodNutritionCache.

    Safe candidate rules:
    - no provider_id
    - no order/view history
    - no ratings/favorites/recent views/gallery/order items
    - no real marketplace price
    """

    candidates = FoodItem.query.filter(FoodItem.provider_id.is_(None)).all()

    moved = 0
    deleted = 0
    skipped = 0

    for item in candidates:
        try:
            if not has_nutrition_values(item):
                skipped += 1
                continue

            if getattr(item, "price", None) not in (None, 0, 0.0):
                skipped += 1
                continue

            related_counts = 0
            if hasattr(item, "order_items"):
                related_counts += item.order_items.count()
            if hasattr(item, "ratings"):
                related_counts += item.ratings.count()
            if hasattr(item, "favorites"):
                related_counts += item.favorites.count()
            if hasattr(item, "recent_views"):
                related_counts += item.recent_views.count()
            if hasattr(item, "views"):
                related_counts += item.views.count()
            if hasattr(item, "gallery_images"):
                related_counts += item.gallery_images.count()

            if related_counts > 0:
                skipped += 1
                continue

            payload = {
                "name": item.name,
                "calories": safe_float(item.calories),
                "protein": safe_float(item.protein),
                "carbs": safe_float(item.carbs),
                "fat": safe_float(item.fat),
                "source": getattr(item, "nutrition_source", None) or "legacy_fooditem_migration",
                "confidence": getattr(item, "nutrition_confidence", None) or "medium",
                "per": getattr(item, "nutrition_basis", None) or "100g",
            }

            ok = persist_nutrition_to_cache(item.name, payload)
            if not ok:
                skipped += 1
                continue

            moved += 1

            if delete_from_food_items:
                db.session.delete(item)
                db.session.commit()
                deleted += 1

        except Exception:
            db.session.rollback()
            skipped += 1

    return {
        "checked": len(candidates),
        "moved": moved,
        "deleted": deleted,
        "skipped": skipped,
    }


def predict_fitness_goal(
    current_weight: float,
    target_weight: float,
    daily_calorie_intake: float,
    latest_height: Optional[float] = None,
) -> Dict[str, Any]:
    difference = round(target_weight - current_weight, 2)
    if abs(difference) < 0.01:
        goal_type = "maintain_weight"
    elif difference < 0:
        goal_type = "weight_loss"
    else:
        goal_type = "weight_gain"

    maintenance_kcal = round(max(1400, current_weight * 30), 2)
    daily_gap = round(daily_calorie_intake - maintenance_kcal, 2)

    if goal_type == "weight_loss":
        effective_gap = max(maintenance_kcal - daily_calorie_intake, 0)
        weekly_change = round((effective_gap * 7) / 7700, 3)
        total_kg = abs(difference)
    elif goal_type == "weight_gain":
        effective_gap = max(daily_calorie_intake - maintenance_kcal, 0)
        weekly_change = round((effective_gap * 7) / 7700, 3)
        total_kg = abs(difference)
    else:
        weekly_change = 0
        total_kg = 0

    if goal_type == "maintain_weight":
        estimated_weeks = 0
        expected_days = 0
        issue = None
    elif weekly_change <= 0:
        estimated_weeks = None
        expected_days = None
        issue = "The selected calorie intake does not create a strong enough calorie deficit or surplus for this goal under the current baseline assumption."
    else:
        estimated_weeks = round(total_kg / weekly_change, 1)
        expected_days = max(1, int(round(estimated_weeks * 7)))
        issue = None

    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "confidence": {"type": "string"},
            "tips": {"type": "array", "items": {"type": "string"}},
            "warning": {"type": "string"},
        },
        "required": ["summary", "confidence", "tips", "warning"],
    }

    prompt = f"""You are helping with a fitness-goal ETA.
Use this deterministic baseline and stay close to it.
current_weight_kg={current_weight}
target_weight_kg={target_weight}
daily_calorie_intake={daily_calorie_intake}
estimated_maintenance_kcal={maintenance_kcal}
goal_type={goal_type}
estimated_weekly_weight_change_kg={weekly_change}
estimated_weeks={estimated_weeks}
height_m={latest_height if latest_height else 'unknown'}

Return a short explanation, confidence level, 3 practical tips, and one warning. JSON only.
"""

    ai_result = ollama_chat_json(prompt, schema, timeout=12) or {}

    summary = ai_result.get("summary") or (
        "Your estimate is based on a simple calorie-balance model combined with your goal direction. "
        "Treat it as a planning estimate, not a medical guarantee."
    )

    warning = ai_result.get("warning") or issue or "Large calorie changes should be reviewed with a professional if you have health concerns."

    return {
        "goal_type": goal_type,
        "current_weight": current_weight,
        "target_weight": target_weight,
        "daily_calorie_intake": daily_calorie_intake,
        "maintenance_kcal": maintenance_kcal,
        "daily_gap": daily_gap,
        "weekly_change": weekly_change,
        "estimated_weeks": estimated_weeks,
        "expected_days": expected_days,
        "summary": summary,
        "confidence": ai_result.get("confidence") or ("medium" if estimated_weeks is not None else "low"),
        "tips": ai_result.get("tips") or [
            "Track your average intake for at least 7 days.",
            "Recheck your weight trend weekly instead of daily.",
            "Update calories if progress stalls for 2 to 3 weeks.",
        ],
        "warning": warning,
        "issue": issue,
    }