"""One-time bulk backfill of core/nutrition.py's cache table
(ingredient_nutrition) from USDA FoodData Central's free bulk CSV dumps
(Foundation Foods + SR Legacy -- no API key, no per-request rate limit).

This is what lets us cover ~77k canonical ingredients without hammering the
rate-limited live provider APIs in core/nutrition.py -- those are only used
afterward, lazily, for the ingredients this bulk match misses.

Usage: python -m etl.nutrition_backfill [--usda-dir data/raw/usda] [--out data/recipes.db]

Expects data/raw/usda/{foundation,sr_legacy}/{food.csv,food_nutrient.csv,nutrient.csv}
(see fridge-recipe-system-plan.md / integration plan for the download URLs).
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import inflect

from core import db
from core.config import RECIPES_DB_PATH

RAW_USDA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "usda"

# nutrient_nbr (USDA's stable nutrient number) -> our column name.
_NUTRIENT_MAP = {
    "1008": "cal_per_100g",
    "1003": "protein_g",
    "1004": "fat_g",
    "1258": "sat_fat_g",
    "1005": "carbs_g",
    "2000": "sugar_g",
    "1079": "fiber_g",
    "1093": "sodium_mg",
}

_WORD_RE = re.compile(r"[a-z]+")
_inflect = inflect.engine()

# USDA descriptions for prepared/processed products (vs. the plain raw/whole
# form we usually want as the "default" nutrition for an ingredient) --
# these push a candidate down in ranking rather than excluding it outright,
# since sometimes they're the only match available.
_PROCESSED_KEYWORDS = {
    "powder", "dried", "dehydrated", "freeze-dried", "souffle", "cracker",
    "bread", "tender", "breaded", "battered", "fried", "juice", "sauce",
    "paste", "extract", "syrup", "candied", "pickled", "canned", "frozen",
    "chip", "puff", "cake", "cookie", "flake", "instant", "dip", "spread",
}


@lru_cache(maxsize=None)
def _singular_word(word: str) -> str:
    try:
        singular = _inflect.singular_noun(word)
    except TypeError:
        return word
    return singular if singular else word


def _tokenize(text: str) -> frozenset[str]:
    return frozenset(_singular_word(w) for w in _WORD_RE.findall(text.lower()))


@dataclass
class UsdaFood:
    fdc_id: str
    description: str
    # Tokens from the first few comma-separated segments -- USDA descriptions
    # are "Base name, cut/prep, ..., raw|cooked" so the head identifies what
    # the food actually *is*, while the tail is prep detail we don't want to
    # require a literal word-match on (e.g. "Onions, raw" vs. our query
    # "onion" shouldn't lose to "Spices, onion powder" just because the full
    # description also happens to contain the word "onion").
    first_segment_tokens: frozenset[str]
    head_tokens: frozenset[str]
    all_tokens: frozenset[str]
    processed_penalty: int
    priority: int  # lower = preferred on ties (SR Legacy is cleaner than Foundation samples)
    nutrients: dict[str, float] = field(default_factory=dict)


def _load_bundle(bundle_dir: Path, priority: int) -> dict[str, UsdaFood]:
    # food_nutrient.nutrient_id is a direct FK to nutrient.id, and nutrient.id
    # is already the "nutrient number" scheme _NUTRIENT_MAP uses (1008 =
    # energy, 1003 = protein, ...) -- nutrient.csv's separate `nutrient_nbr`
    # column is a *different*, legacy SR numbering (e.g. "208" for energy)
    # and is not what food_nutrient.csv references. No join needed.
    foods: dict[str, UsdaFood] = {}
    with open(bundle_dir / "food.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            desc = row["description"]
            segments = [s.strip() for s in desc.split(",")]
            head = ", ".join(segments[:2])
            lower_desc = desc.lower()
            foods[row["fdc_id"]] = UsdaFood(
                fdc_id=row["fdc_id"],
                description=desc,
                first_segment_tokens=_tokenize(segments[0]),
                head_tokens=_tokenize(head),
                all_tokens=_tokenize(desc),
                processed_penalty=sum(
                    1 for kw in _PROCESSED_KEYWORDS if kw in lower_desc
                ),
                priority=priority,
            )

    with open(bundle_dir / "food_nutrient.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            food = foods.get(row["fdc_id"])
            if food is None:
                continue
            col = _NUTRIENT_MAP.get(row["nutrient_id"])
            if col is None or not row["amount"]:
                continue
            try:
                food.nutrients[col] = float(row["amount"])
            except ValueError:
                continue

    return foods


def load_usda_foods(usda_dir: Path) -> list[UsdaFood]:
    foods: dict[str, UsdaFood] = {}
    # SR Legacy first (priority 0, preferred) so Foundation Foods (priority 1,
    # includes many brand/sample-specific entries) only fills gaps.
    for name, priority in (("sr_legacy", 0), ("foundation", 1)):
        bundle_dir = usda_dir / name
        if not bundle_dir.exists():
            print(f"skipping missing bundle: {bundle_dir}", file=sys.stderr)
            continue
        for fdc_id, food in _load_bundle(bundle_dir, priority).items():
            foods[f"{name}:{fdc_id}"] = food
    return list(foods.values())


def build_word_index(foods: list[UsdaFood]) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for i, food in enumerate(foods):
        for tok in food.all_tokens:
            index.setdefault(tok, []).append(i)
    return index


def match_ingredient(
    canonical_name: str, foods: list[UsdaFood], index: dict[str, list[int]]
) -> UsdaFood | None:
    query_tokens = _tokenize(canonical_name)
    if not query_tokens:
        return None

    candidate_idxs: set[int] = set()
    for tok in query_tokens:
        candidate_idxs.update(index.get(tok, ()))
    if not candidate_idxs:
        return None

    # Tier 0: query fully explained by the food's own first name segment
    # (e.g. "Rice" for query "rice", "Egg" for query "egg") -- USDA's own
    # identity for the food, the strongest possible signal. Tier 1: query
    # explained by the first two segments (name + one modifier, e.g. "Onions,
    # raw"). Tier 2: the head is a *subset* of a multi-word query (e.g. head
    # "chicken" for query "chicken breast" -- still the primary name, just
    # less specific than asked). Tier 3: query only found somewhere in the
    # full description (weakest -- could be a tail qualifier, e.g. "egg" in
    # "Noodles, egg, cooked").
    scored = []
    for i in candidate_idxs:
        food = foods[i]
        first, head = food.first_segment_tokens, food.head_tokens
        if query_tokens <= first:
            tier, extra = 0, len(first - query_tokens)
        elif query_tokens <= head:
            tier, extra = 1, len(head - query_tokens)
        elif head and head <= query_tokens:
            tier, extra = 2, len(query_tokens - head)
        elif query_tokens <= food.all_tokens:
            tier, extra = 3, len(food.all_tokens - query_tokens)
        else:
            continue
        has_no_nutrients = not food.nutrients
        scored.append(
            (has_no_nutrients, tier, food.processed_penalty, extra, food.priority, food)
        )
    if not scored:
        return None
    scored.sort(key=lambda t: t[:5])
    return scored[0][5]


def backfill(usda_dir: Path, out_path: Path) -> None:
    print("loading USDA bundles...")
    foods = load_usda_foods(usda_dir)
    print(f"loaded {len(foods)} USDA food records")
    index = build_word_index(foods)

    conn = db.connect(out_path)
    canonical_names = [
        row[0] for row in conn.execute("SELECT canonical_name FROM ingredients")
    ]
    print(f"matching {len(canonical_names)} canonical ingredients...")

    matched = 0
    now = datetime.now(timezone.utc).isoformat()
    for name in canonical_names:
        food = match_ingredient(name, foods, index)
        if food is None or not food.nutrients:
            continue
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
                name,
                food.nutrients.get("cal_per_100g"),
                food.nutrients.get("protein_g"),
                food.nutrients.get("fat_g"),
                food.nutrients.get("sat_fat_g"),
                food.nutrients.get("carbs_g"),
                food.nutrients.get("sugar_g"),
                food.nutrients.get("fiber_g"),
                food.nutrients.get("sodium_mg"),
                "usda_bulk",
                now,
            ),
        )
        matched += 1

    conn.commit()
    conn.close()
    pct = 100 * matched / len(canonical_names) if canonical_names else 0
    print(f"matched {matched}/{len(canonical_names)} ingredients ({pct:.1f}%) -> {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--usda-dir", type=Path, default=RAW_USDA_DIR)
    parser.add_argument("--out", type=Path, default=RECIPES_DB_PATH)
    args = parser.parse_args()
    backfill(args.usda_dir, args.out)


if __name__ == "__main__":
    main()
