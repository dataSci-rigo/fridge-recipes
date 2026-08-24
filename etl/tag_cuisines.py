"""Standalone backfill: tag recipes.cuisine by title keyword
(etl/cuisine_rules.py). Same fast update-in-place shape as tag_categories.py
-- run whenever cuisine_rules.py's keyword list changes.

Usage: python -m etl.tag_cuisines [--db data/recipes.db]
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from core.config import RECIPES_DB_PATH
from core.db import connect, init_recipes_db
from etl.cuisine_rules import tag_cuisine


def tag_all(db_path: Path) -> Counter:
    init_recipes_db(db_path)  # ensures the cuisine column exists (migration)
    conn = connect(db_path)
    counts: Counter = Counter()
    try:
        rows = conn.execute("SELECT id, title FROM recipes").fetchall()
        for recipe_id, title in rows:
            cuisine = tag_cuisine(title)
            counts[cuisine or "(none)"] += 1
            if cuisine is not None:
                conn.execute(
                    "UPDATE recipes SET cuisine = ? WHERE id = ?", (cuisine, recipe_id)
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
    print(f"tagged {total - counts['(none)']}/{total} recipes with a cuisine")
    for cuisine, n in counts.most_common():
        print(f"  {cuisine:<20} {n}")


if __name__ == "__main__":
    main()
