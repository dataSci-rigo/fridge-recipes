"""Shared recipe-detail assembly for the Telegram bot and the web app.

Both surfaces show the same thing for a recipe ref -- title, directions, and
a have/missing checklist against an inventory -- so the computation lives
here once; each surface only renders the returned dict its own way
(Markdown for the bot, a template for the web).

A `ref` is a display id: "42" = corpus recipes.db row, "c42" = unpromoted
custom recipe in app.db (same convention as fridge_handlers.py's /recipe).
"""
from __future__ import annotations

from pathlib import Path

from core.config import APP_DB_PATH, RECIPES_DB_PATH
from core.custom_recipes import get_custom_recipe
from core.normalize import canonicalize
from core.search import get_recipe


def parse_ref(ref: str) -> tuple[str, int] | None:
    ref = ref.strip()
    if ref[:1].lower() == "c" and ref[1:].isdigit():
        return ("custom", int(ref[1:]))
    if ref.isdigit():
        return ("corpus", int(ref))
    return None


def display_id(source: str, recipe_id: int) -> str:
    return f"c{recipe_id}" if source == "custom" else str(recipe_id)


def get_recipe_detail(
    ref: str,
    have_names: set[str],
    recipes_db_path: Path = RECIPES_DB_PATH,
    app_db_path: Path = APP_DB_PATH,
) -> dict | None:
    """Structured detail for either recipe source, with per-ingredient
    `have` computed against `have_names` (a set of canonical inventory
    names). Returns None for an unparseable ref or a missing recipe."""
    parsed = parse_ref(ref)
    if parsed is None:
        return None
    source, recipe_id = parsed

    if source == "corpus":
        detail = get_recipe(recipe_id, db_path=recipes_db_path)
        if detail is None:
            return None
        title, directions = detail["title"], detail["directions"]
        ingredient_rows = [
            (i["name"], i["quantity_text"]) for i in detail["ingredients"]
        ]
        extra = {
            "active_min": detail["active_min"],
            "total_min": detail["total_min"],
            "category": detail["category"],
            "cuisine": detail["cuisine"],
            "calories_est": detail["calories_est"],
        }
    else:
        detail = get_custom_recipe(recipe_id, db_path=app_db_path)
        if detail is None:
            return None
        title, directions = detail["name"], detail["directions"]
        ingredient_rows = [
            (canonicalize(i["canonical_name"]), i.get("quantity_text"))
            for i in detail["ingredients"]
        ]
        extra = {"active_min": None, "total_min": None, "category": None,
                 "cuisine": None, "calories_est": None}

    return {
        "ref": display_id(source, recipe_id),
        "source": source,
        "id": recipe_id,
        "title": title,
        "directions": directions or "",
        "ingredients": [
            {"name": name, "quantity_text": qty, "have": name in have_names}
            for name, qty in ingredient_rows
        ],
        **extra,
    }
