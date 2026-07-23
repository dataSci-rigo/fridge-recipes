"""Per-ingredient nutrition lookup with a local-DB cache in front.

Adapted from food/nutrition.py's multi-provider cascade (same env keys,
same provider APIs) -- kept as a separate copy rather than importing food/
directly so fridge_recipes stays a standalone, independently testable
package and the live nutrition-logging system in food/ is never touched by
changes here.

Call order for a canonical ingredient name:
    1. ingredient_nutrition table (bulk-backfilled offline by
       etl/nutrition_backfill.py from a USDA FoodData Central dump -- free,
       no rate limit)
    2. live provider cascade (USDA search API, then Nutritionix) -- only for
       the misses, and the result is cached back into ingredient_nutrition
       so the same ingredient never triggers a second live call.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from core.config import RECIPES_DB_PATH
from core.db import connect

USDA_API_KEY = os.environ.get("USDA_API_KEY", "DEMO_KEY")
NUTRITIONIX_APP_ID = os.environ.get("NUTRITIONIX_APP_ID", "")
NUTRITIONIX_API_KEY = os.environ.get("NUTRITIONIX_API_KEY", "")
TIMEOUT = 10
HEADERS = {"User-Agent": "fridge-recipes/0.1 (contact@example.com)"}


@dataclass
class NutritionPer100g:
    canonical_name: str
    cal_per_100g: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    sat_fat_g: float | None = None
    carbs_g: float | None = None
    sugar_g: float | None = None
    fiber_g: float | None = None
    sodium_mg: float | None = None
    source: str = "unknown"


def _f(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    return float(m.group()) if m else None


# Nutrient numbers per USDA FoodData Central (same mapping food/nutrition.py
# uses, reused here so the bulk backfill and the live fallback agree).
_USDA_MAP = {
    "cal_per_100g": ("1008", "energy"),
    "protein_g": ("1003", "protein"),
    "fat_g": ("1004", "total lipid"),
    "sat_fat_g": ("1258", "fatty acids, total saturated"),
    "carbs_g": ("1005", "carbohydrate"),
    "sugar_g": ("2000", "sugars, total"),
    "fiber_g": ("1079", "fiber"),
    "sodium_mg": ("1093", "sodium"),
}


def _usda_search(canonical_name: str) -> NutritionPer100g | None:
    url = "https://api.nal.usda.gov/fdc/v1/foods/search"
    params = {"query": canonical_name, "pageSize": 1, "api_key": USDA_API_KEY}
    r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    foods = r.json().get("foods", [])
    if not foods:
        return None
    food = foods[0]

    by_num, by_name = {}, {}
    for fn in food.get("foodNutrients", []):
        val = fn.get("value")
        num = str(fn.get("nutrientNumber") or fn.get("number") or "")
        name = (fn.get("nutrientName") or fn.get("name") or "").lower()
        if num:
            by_num[num] = val
        if name:
            by_name[name] = val

    def pick(num, name_sub):
        if num in by_num:
            return _f(by_num[num])
        for nm, val in by_name.items():
            if name_sub in nm:
                return _f(val)
        return None

    fields = {k: pick(num, sub) for k, (num, sub) in _USDA_MAP.items()}
    return NutritionPer100g(canonical_name=canonical_name, source="usda", **fields)


def _nutritionix_search(canonical_name: str) -> NutritionPer100g | None:
    if not NUTRITIONIX_APP_ID or not NUTRITIONIX_API_KEY:
        return None
    nx_headers = {
        **HEADERS,
        "x-app-id": NUTRITIONIX_APP_ID,
        "x-app-key": NUTRITIONIX_API_KEY,
    }
    try:
        r = requests.get(
            "https://trackapi.nutritionix.com/v2/search/instant",
            params={"query": canonical_name, "branded": False, "common": True},
            headers=nx_headers,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        common = r.json().get("common", [])
        if not common:
            return None
        r2 = requests.post(
            "https://trackapi.nutritionix.com/v2/natural/nutrients",
            json={"query": common[0].get("food_name", canonical_name)},
            headers=nx_headers,
            timeout=TIMEOUT,
        )
        r2.raise_for_status()
        foods = r2.json().get("foods", [])
        if not foods:
            return None
        f = foods[0]
        grams = _f(f.get("serving_weight_grams")) or 100.0
        scale = 100.0 / grams
        return NutritionPer100g(
            canonical_name=canonical_name,
            cal_per_100g=(_f(f.get("nf_calories")) or 0) * scale,
            protein_g=(_f(f.get("nf_protein")) or 0) * scale,
            fat_g=(_f(f.get("nf_total_fat")) or 0) * scale,
            sat_fat_g=(_f(f.get("nf_saturated_fat")) or 0) * scale,
            carbs_g=(_f(f.get("nf_total_carbohydrate")) or 0) * scale,
            sugar_g=(_f(f.get("nf_sugars")) or 0) * scale,
            fiber_g=(_f(f.get("nf_dietary_fiber")) or 0) * scale,
            sodium_mg=(_f(f.get("nf_sodium")) or 0) * scale,
            source="nutritionix",
        )
    except Exception:
        return None


def _cache_get(canonical_name: str, db_path: Path) -> NutritionPer100g | None:
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT cal_per_100g, protein_g, fat_g, sat_fat_g, carbs_g, sugar_g, "
            "fiber_g, sodium_mg, source FROM ingredient_nutrition WHERE canonical_name = ?",
            (canonical_name,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    (cal, protein, fat, sat_fat, carbs, sugar, fiber, sodium, source) = row
    if all(v is None for v in (cal, protein, fat, sat_fat, carbs, sugar, fiber, sodium)):
        return None
    return NutritionPer100g(
        canonical_name=canonical_name,
        cal_per_100g=cal, protein_g=protein, fat_g=fat, sat_fat_g=sat_fat,
        carbs_g=carbs, sugar_g=sugar, fiber_g=fiber, sodium_mg=sodium,
        source=source or "cache",
    )


def _cache_set(result: NutritionPer100g, db_path: Path) -> None:
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT INTO ingredient_nutrition "
            "(canonical_name, cal_per_100g, protein_g, fat_g, sat_fat_g, carbs_g, "
            "sugar_g, fiber_g, sodium_mg, source, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(canonical_name) DO UPDATE SET "
            "cal_per_100g=excluded.cal_per_100g, protein_g=excluded.protein_g, "
            "fat_g=excluded.fat_g, sat_fat_g=excluded.sat_fat_g, carbs_g=excluded.carbs_g, "
            "sugar_g=excluded.sugar_g, fiber_g=excluded.fiber_g, sodium_mg=excluded.sodium_mg, "
            "source=excluded.source, updated_at=excluded.updated_at",
            (
                result.canonical_name, result.cal_per_100g, result.protein_g,
                result.fat_g, result.sat_fat_g, result.carbs_g, result.sugar_g,
                result.fiber_g, result.sodium_mg, result.source,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def lookup(canonical_name: str, db_path: Path = RECIPES_DB_PATH) -> NutritionPer100g | None:
    """Per-100g nutrition for a canonical ingredient name.

    Checks the bulk-backfilled/cached ingredient_nutrition table first; only
    falls through to a live API call (and caches the result) on a miss, so a
    given ingredient never triggers more than one live lookup ever.
    """
    cached = _cache_get(canonical_name, db_path)
    if cached is not None:
        return cached

    result = _nutritionix_search(canonical_name) or _usda_search(canonical_name)
    if result is not None:
        _cache_set(result, db_path)
    return result
