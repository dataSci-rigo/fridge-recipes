"""CRUD + lightweight search/rank for app.db's custom_recipes -- recipes
ingested via photo/paste (food/recipe_ingest.py) that haven't been through
the laptop's heavy promotion batch (etl/promote_custom_recipes.py) yet.

Deliberately no FTS index or embeddings here: this pool is small (unpromoted
recipes only -- tens to low hundreds of rows), so plain Python filtering is
fast enough and keeps the VM out of the heavy-processing business, per the
"heavy work happens on the laptop periodically" design.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core.config import APP_DB_PATH
from core.db import connect
from core.models import InventoryItem, RankedRecipe, RankFilters, Recipe, RecipeIngredient
from core.normalize import canonicalize, is_pantry
from core.rank import rank_recipes as _rank_recipes


def save_recipe(
    name: str,
    ingredients: list[dict],  # [{"canonical_name": ..., "quantity_text": ...}, ...]
    directions: str | None = None,
    alternatives: dict[str, list[str]] | None = None,  # canonical_name -> [alt names]
    user_input: str | None = None,
    db_path: Path = APP_DB_PATH,
) -> int:
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO custom_recipes "
            "(name, user_input, ingredients_json, alternatives_json, directions, saved_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "ingredients_json=excluded.ingredients_json, "
            "alternatives_json=excluded.alternatives_json, "
            "directions=excluded.directions, saved_at=excluded.saved_at",
            (
                name,
                user_input,
                json.dumps(ingredients),
                json.dumps(alternatives or {}),
                directions,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM custom_recipes WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        return row[0] if row else cur.lastrowid
    finally:
        conn.close()


def _row_to_dict(row: tuple) -> dict:
    (rid, name, user_input, ingredients_json, alternatives_json,
     directions, total_nutrition_json, liked, promoted_recipe_id, saved_at) = row
    return {
        "id": rid,
        "name": name,
        "user_input": user_input,
        "ingredients": json.loads(ingredients_json) if ingredients_json else [],
        "alternatives": json.loads(alternatives_json) if alternatives_json else {},
        "directions": directions,
        "total_nutrition": json.loads(total_nutrition_json) if total_nutrition_json else None,
        "liked": liked,
        "promoted_recipe_id": promoted_recipe_id,
        "saved_at": saved_at,
    }


_SELECT_COLS = (
    "id, name, user_input, ingredients_json, alternatives_json, directions, "
    "total_nutrition_json, liked, promoted_recipe_id, saved_at"
)


def get_custom_recipe(recipe_id: int, db_path: Path = APP_DB_PATH) -> dict | None:
    conn = connect(db_path)
    try:
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM custom_recipes WHERE id = ?", (recipe_id,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row else None


def list_unpromoted(db_path: Path = APP_DB_PATH) -> list[dict]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT {_SELECT_COLS} FROM custom_recipes WHERE promoted_recipe_id IS NULL"
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def delete_recipe(recipe_id: int, db_path: Path = APP_DB_PATH) -> bool:
    conn = connect(db_path)
    try:
        cur = conn.execute("DELETE FROM custom_recipes WHERE id = ?", (recipe_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def mark_promoted(recipe_id: int, promoted_recipe_id: int, db_path: Path = APP_DB_PATH) -> None:
    conn = connect(db_path)
    try:
        conn.execute(
            "UPDATE custom_recipes SET promoted_recipe_id = ? WHERE id = ?",
            (promoted_recipe_id, recipe_id),
        )
        conn.commit()
    finally:
        conn.close()


def _to_recipe_model(row: dict) -> Recipe:
    ingredients = tuple(
        RecipeIngredient(
            canonicalize(i["canonical_name"]), is_pantry(canonicalize(i["canonical_name"]))
        )
        for i in row["ingredients"]
    )
    return Recipe(id=row["id"], title=row["name"], ingredients=ingredients)


def rank_custom(
    inventory: list[InventoryItem],
    filters: RankFilters | None = None,
    top_n: int | None = None,
    get_substitutes_fn=None,
    db_path: Path = APP_DB_PATH,
) -> list[RankedRecipe]:
    rows = list_unpromoted(db_path)
    recipes = [_to_recipe_model(r) for r in rows if r["ingredients"]]
    return _rank_recipes(
        recipes, inventory, filters=filters, top_n=top_n,
        get_substitutes_fn=get_substitutes_fn,
    )


def find_custom(query: str, limit: int = 8, db_path: Path = APP_DB_PATH) -> list[dict]:
    """Simple substring match on name -- no FTS needed for a pool this small."""
    q = query.strip().lower()
    if not q:
        return []
    rows = list_unpromoted(db_path)
    matches = [r for r in rows if q in r["name"].lower()]
    return [{"id": r["id"], "title": r["name"]} for r in matches[:limit]]
