"""Standalone backfill: rough whole-recipe calorie estimates
(recipes.calories_est) from recipe_ingredients quantities x
ingredient_nutrition per-100g values.

Explicitly an ESTIMATE, for coarse "light vs hearty" filtering only: unit ->
gram conversions are kitchen approximations (a cup is 240g regardless of
density), unitless counts use typical piece weights, and the whole recipe is
skipped (NULL) unless enough of its ingredient lines could actually be
scored -- a NULL means "unknown", never "0 calories". No servings data
exists in the source, so this is per whole recipe, not per serving.

Run after nutrition_backfill (needs ingredient_nutrition populated) and
after any full ETL rebuild (which resets the recipes table):
  python -m etl.estimate_calories [--db data/recipes.db]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from core.config import RECIPES_DB_PATH
from core.db import connect, init_recipes_db

# Fraction of a recipe's ingredient lines that must be scorable (known
# grams AND known cal/100g) for the estimate to be trusted at all.
MIN_COVERAGE = 0.6

# A whole-recipe total above this is a parse artifact ("500 cups flour"),
# not food -- discard rather than store garbage.
MAX_SANE_CALORIES = 30_000

GRAMS_PER_UNIT = {
    "gram": 1, "kilogram": 1000, "milligram": 0.001,
    "milliliter": 1, "liter": 1000, "fluid_ounce": 29.6,
    "cup": 240, "tablespoon": 15, "teaspoon": 5,
    "pint": 473, "quart": 946, "gallon": 3785,
    "pound": 453.6, "ounce": 28.35,
    "clove": 5, "stick": 113, "pinch": 0.3, "dash": 0.6,
    "slice": 30, "can": 400, "bunch": 100, "handful": 30, "sprig": 2,
}

# Typical whole-piece weights for common unitless lines ("2 eggs").
PIECE_GRAMS = {
    "egg": 50, "egg white": 33, "egg yolk": 17,
    "onion": 110, "tomato": 120, "potato": 170, "sweet potato": 130,
    "carrot": 60, "celery": 40, "bell pepper": 120, "jalapeno": 15,
    "apple": 180, "banana": 120, "lemon": 60, "lime": 45, "orange": 130,
    "avocado": 150, "cucumber": 200, "zucchini": 195, "garlic": 5,
    "shallot": 30, "tortilla": 45, "chicken breast": 170, "chicken thigh": 120,
    "bay leaf": 0.2, "green onion": 15, "scallion": 15, "mushroom": 20,
    "peach": 150, "pear": 180, "shrimp": 12, "date": 8,
}


def _grams(quantity: float, unit: str | None, name: str) -> float | None:
    if unit:
        u = unit.strip().lower().rstrip("s")
        if u in GRAMS_PER_UNIT:
            return quantity * GRAMS_PER_UNIT[u]
        return None  # package/jar/box etc.: too variable, don't guess
    piece = PIECE_GRAMS.get(name)
    if piece is not None and quantity <= 24:  # "2 eggs" yes, "500 egg" no
        return quantity * piece
    return None


def estimate_all(db_path: Path) -> tuple[int, int]:
    init_recipes_db(db_path)  # ensures the calories_est column exists
    conn = connect(db_path)
    try:
        cal_per_100g = dict(
            conn.execute(
                "SELECT canonical_name, cal_per_100g FROM ingredient_nutrition "
                "WHERE cal_per_100g IS NOT NULL"
            ).fetchall()
        )
        rows = conn.execute(
            "SELECT recipe_id, canonical_ingredient, quantity, unit "
            "FROM recipe_ingredients"
        ).fetchall()

        totals: dict[int, float] = {}
        scored: dict[int, int] = {}
        lines: dict[int, int] = {}
        for recipe_id, name, quantity, unit in rows:
            lines[recipe_id] = lines.get(recipe_id, 0) + 1
            if quantity is None:
                continue
            grams = _grams(quantity, unit, name)
            cal100 = cal_per_100g.get(name)
            if grams is None or cal100 is None:
                continue
            totals[recipe_id] = totals.get(recipe_id, 0.0) + grams * cal100 / 100
            scored[recipe_id] = scored.get(recipe_id, 0) + 1

        estimates = [
            (int(round(totals[rid])), rid)
            for rid in totals
            if scored[rid] / lines[rid] >= MIN_COVERAGE
            and totals[rid] <= MAX_SANE_CALORIES
        ]
        conn.execute("UPDATE recipes SET calories_est = NULL")
        conn.executemany(
            "UPDATE recipes SET calories_est = ? WHERE id = ?", estimates
        )
        conn.commit()
        return len(estimates), len(lines)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=RECIPES_DB_PATH)
    args = parser.parse_args()
    estimated, total = estimate_all(args.db)
    print(f"estimated calories for {estimated}/{total} recipes "
          f"(>= {MIN_COVERAGE:.0%} of lines scorable)")


if __name__ == "__main__":
    main()
