"""SQLite schema + connection helpers for recipes.db and app.db.

recipes.db holds the ETL output (read-mostly, rebuilt from scratch by etl/build.py).
app.db holds user data (inventory, ratings, photos, overrides) and is written by
both the web app and the Telegram bot, so it's opened in WAL mode.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core.config import APP_DB_PATH, RECIPES_DB_PATH

RECIPES_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingredients (
    canonical_name TEXT PRIMARY KEY,
    pantry INTEGER NOT NULL DEFAULT 0,
    aliases TEXT
);

CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    directions TEXT NOT NULL,
    active_min INTEGER,
    total_min INTEGER,
    time_confidence TEXT,
    equipment TEXT,
    category TEXT
);

CREATE TABLE IF NOT EXISTS recipe_ingredients (
    recipe_id INTEGER NOT NULL REFERENCES recipes(id),
    canonical_ingredient TEXT NOT NULL REFERENCES ingredients(canonical_name),
    quantity_text TEXT,
    quantity REAL,
    unit TEXT,
    PRIMARY KEY (recipe_id, canonical_ingredient)
);

-- Standalone (non-external-content) FTS5 table: we populate it explicitly
-- from etl/build.py with rowid = recipes.id, so it doesn't need its columns
-- to line up positionally with the recipes table.
CREATE VIRTUAL TABLE IF NOT EXISTS recipes_fts USING fts5(title, ingredients_text);

-- Per-100g nutrition for a canonical ingredient. Bulk-populated offline by
-- etl/nutrition_backfill.py from a USDA FoodData Central dump; misses are
-- filled in lazily by core/nutrition.py's live-provider cascade and cached
-- back here so we never re-hit a rate-limited API for the same ingredient.
-- No FK to ingredients(canonical_name): nutrition can be looked up for
-- freeform/bot-added items that aren't in the recipe corpus at all.
CREATE TABLE IF NOT EXISTS ingredient_nutrition (
    canonical_name TEXT PRIMARY KEY,
    cal_per_100g REAL,
    protein_g REAL,
    fat_g REAL,
    sat_fat_g REAL,
    carbs_g REAL,
    sugar_g REAL,
    fiber_g REAL,
    sodium_mg REAL,
    source TEXT,
    updated_at TEXT
);
"""

APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY,
    canonical_ingredient TEXT NOT NULL,
    qty_text TEXT,
    location TEXT NOT NULL CHECK (location IN ('fridge', 'freezer', 'pantry')),
    added_at TEXT NOT NULL,
    expires_at TEXT,
    state TEXT NOT NULL DEFAULT 'fresh'
        CHECK (state IN ('fresh', 'aging', 'use_soon', 'expired')),
    source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual', 'telegram', 'mealprep_migration'))
);

CREATE TABLE IF NOT EXISTS shelf_life (
    canonical_ingredient TEXT NOT NULL,
    location TEXT NOT NULL CHECK (location IN ('fridge', 'freezer', 'pantry')),
    days INTEGER NOT NULL,
    PRIMARY KEY (canonical_ingredient, location)
);

CREATE TABLE IF NOT EXISTS ratings (
    recipe_id INTEGER NOT NULL,
    stars INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
    cooked_at TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY,
    recipe_id INTEGER NOT NULL,
    filepath TEXT NOT NULL,
    taken_at TEXT NOT NULL,
    caption TEXT
);

CREATE TABLE IF NOT EXISTS recipe_overrides (
    recipe_id INTEGER PRIMARY KEY,
    active_min INTEGER,
    total_min INTEGER,
    equipment TEXT
);

CREATE TABLE IF NOT EXISTS search_prefs (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Recipes the user typed in themselves or saved outside the RecipeNLG/
-- open-recipe-data corpus (replaces food/food.db's dead `recipes` table --
-- same shape, now actually wired to a bot command). `liked` is nullable:
-- NULL = no opinion yet, 1 = liked, 0 = disliked.
-- `alternatives_json`: per-ingredient substitute lists discovered at ingest
-- time (e.g. "butter, or margarine"). `promoted_recipe_id`: set once
-- etl/promote_custom_recipes.py (laptop, heavy processing) has folded this
-- into the main recipes.db corpus with real nutrition/embeddings -- NULL
-- means it still only lives here and is live-merged into /cook and /find.
CREATE TABLE IF NOT EXISTS custom_recipes (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    user_input TEXT,
    ingredients_json TEXT,
    alternatives_json TEXT,
    directions TEXT,
    total_nutrition_json TEXT,
    liked INTEGER,
    promoted_recipe_id INTEGER,
    saved_at TEXT NOT NULL
);

-- Ingredient substitution pairs. Grows from a curated seed
-- (etl/substitutions.yaml), alternatives discovered during recipe ingestion,
-- and cached answers from core/substitutions.py's live Claude fallback.
-- Undirected: (a, b) means a and b are interchangeable, check both ways.
CREATE TABLE IF NOT EXISTS substitutions (
    id INTEGER PRIMARY KEY,
    canonical_a TEXT NOT NULL,
    canonical_b TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('seed', 'ingested', 'claude')),
    created_at TEXT NOT NULL,
    UNIQUE (canonical_a, canonical_b)
);

-- One row per "I actually cooked this" event, whether the recipe came from
-- the corpus (recipe_id) or was homemade (custom_recipe_id) -- replaces
-- food/food.db's dead `recipes.total_json` purpose of tracking calories for
-- something actually made. Exactly one of recipe_id/custom_recipe_id is set.
CREATE TABLE IF NOT EXISTS cooked_log (
    id INTEGER PRIMARY KEY,
    recipe_id INTEGER,
    custom_recipe_id INTEGER REFERENCES custom_recipes(id),
    ingredients_json TEXT,
    total_nutrition_json TEXT,
    cooked_at TEXT NOT NULL,
    notes TEXT
);
"""


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """ALTER TABLE ADD COLUMN, ignored if it already exists -- lets schema
    additions land on a DB that already has real data without a full rebuild
    (mirrors food/db.py's migration pattern)."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    except sqlite3.OperationalError:
        pass


def init_recipes_db(path: Path = RECIPES_DB_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(RECIPES_SCHEMA)
        _add_column_if_missing(conn, "recipes", "category", "TEXT")
        conn.commit()
    finally:
        conn.close()


def init_app_db(path: Path = APP_DB_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(APP_SCHEMA)
        _add_column_if_missing(conn, "custom_recipes", "alternatives_json", "TEXT")
        _add_column_if_missing(conn, "custom_recipes", "promoted_recipe_id", "INTEGER")
        conn.commit()
    finally:
        conn.close()


def init_all() -> None:
    init_recipes_db()
    init_app_db()


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2 or sys.argv[1] != "init":
        print("usage: python -m core.db init")
        raise SystemExit(1)
    init_all()
    print(f"recipes.db -> {RECIPES_DB_PATH}")
    print(f"app.db     -> {APP_DB_PATH}")
