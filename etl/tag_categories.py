"""Standalone backfill: tag recipes.category by title keyword (etl/type_rules.py).

Doesn't touch ingredients/nutrition/embeddings, so this is a fast update-in-
place rather than a full ETL rerun -- run whenever type_rules.py's keyword
list changes.

Usage: python -m etl.tag_categories [--db data/recipes.db]
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from core.config import RECIPES_DB_PATH
from core.db import connect, init_recipes_db
from etl.type_rules import tag_category


def tag_all(db_path: Path) -> Counter:
    init_recipes_db(db_path)  # ensures the category column exists (migration)
    conn = connect(db_path)
    counts: Counter = Counter()
    try:
        rows = conn.execute("SELECT id, title FROM recipes").fetchall()
        for recipe_id, title in rows:
            category = tag_category(title)
            counts[category or "(none)"] += 1
            if category is not None:
                conn.execute(
                    "UPDATE recipes SET category = ? WHERE id = ?", (category, recipe_id)
                )
        conn.commit()
    finally:
        conn.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=RECIPES_DB_PATH)
    args = parser.parse_args()
    counts = tag_all(args.db)
    total = sum(counts.values())
    print(f"tagged {total - counts['(none)']}/{total} recipes with a category")
    for category, n in counts.most_common():
        print(f"  {category:<20} {n}")


if __name__ == "__main__":
    main()
