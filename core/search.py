"""Loads candidate Recipe objects from recipes.db for core.rank.rank_recipes.

core.rank operates on in-memory core.models.Recipe objects rather than the
DB directly (so it stays pure/testable with fixtures) -- this module is the
bridge that pulls a manageable candidate set out of the 163k-recipe corpus
for a given inventory, before handing off to rank_recipes for scoring.
"""
from __future__ import annotations

from pathlib import Path

from core.config import RECIPES_DB_PATH
from core.db import connect
from core.models import Recipe, RecipeIngredient

# Recipes sharing >=1 ingredient with a common pantry-adjacent item (salt,
# egg, butter, ...) can number in the tens of thousands; cap the candidate
# set pulled from SQL before scoring in Python. rank_recipes' own "share
# >=1 non-pantry ingredient" filter still applies on top of this.
DEFAULT_CANDIDATE_LIMIT = 500


def load_candidate_recipes(
    inventory_names: set[str],
    db_path: Path = RECIPES_DB_PATH,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> list[Recipe]:
    if not inventory_names:
        return []

    conn = connect(db_path)
    try:
        placeholders = ",".join("?" * len(inventory_names))
        recipe_ids = [
            row[0]
            for row in conn.execute(
                f"SELECT DISTINCT recipe_id FROM recipe_ingredients "
                f"WHERE canonical_ingredient IN ({placeholders}) LIMIT ?",
                (*inventory_names, limit),
            )
        ]
        if not recipe_ids:
            return []

        id_placeholders = ",".join("?" * len(recipe_ids))
        recipe_rows = conn.execute(
            f"SELECT id, title, directions, active_min, total_min, equipment "
            f"FROM recipes WHERE id IN ({id_placeholders})",
            recipe_ids,
        ).fetchall()
        ing_rows = conn.execute(
            f"SELECT ri.recipe_id, ri.canonical_ingredient, i.pantry "
            f"FROM recipe_ingredients ri "
            f"JOIN ingredients i ON ri.canonical_ingredient = i.canonical_name "
            f"WHERE ri.recipe_id IN ({id_placeholders})",
            recipe_ids,
        ).fetchall()
    finally:
        conn.close()

    ingredients_by_recipe: dict[int, list[RecipeIngredient]] = {}
    for recipe_id, name, pantry in ing_rows:
        ingredients_by_recipe.setdefault(recipe_id, []).append(
            RecipeIngredient(name, bool(pantry))
        )

    recipes = []
    for rid, title, _directions, active_min, total_min, equipment in recipe_rows:
        recipes.append(
            Recipe(
                id=rid,
                title=title,
                ingredients=tuple(ingredients_by_recipe.get(rid, [])),
                active_min=active_min,
                total_min=total_min,
                equipment=frozenset(equipment.split(",")) if equipment else frozenset(),
            )
        )
    return recipes


def get_recipe(recipe_id: int, db_path: Path = RECIPES_DB_PATH) -> dict | None:
    """Full recipe detail (title, directions, ingredients) for a single recipe_id."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, title, directions, active_min, total_min, equipment "
            "FROM recipes WHERE id = ?",
            (recipe_id,),
        ).fetchone()
        if row is None:
            return None
        ing_rows = conn.execute(
            "SELECT canonical_ingredient, quantity_text FROM recipe_ingredients "
            "WHERE recipe_id = ?",
            (recipe_id,),
        ).fetchall()
    finally:
        conn.close()

    rid, title, directions, active_min, total_min, equipment = row
    return {
        "id": rid,
        "title": title,
        "directions": directions,
        "active_min": active_min,
        "total_min": total_min,
        "equipment": equipment.split(",") if equipment else [],
        "ingredients": [{"name": n, "quantity_text": q} for n, q in ing_rows],
    }
