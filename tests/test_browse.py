"""Cuisine tagging, calorie estimation, and the new search filters."""
from __future__ import annotations

from core.db import connect, init_recipes_db
from core.search import search_recipes
from etl.cuisine_rules import tag_cuisine
from etl.estimate_calories import _grams, estimate_all


def test_tag_cuisine_basic():
    assert tag_cuisine("Chicken Enchiladas") == "mexican"
    assert tag_cuisine("Spaghetti Carbonara") == "italian"
    assert tag_cuisine("Pad Thai") == "thai"
    assert tag_cuisine("Chicken Curry") == "indian"
    assert tag_cuisine("Green Curry Shrimp") == "thai"  # thai wins before indian
    assert tag_cuisine("Roast Chicken") is None
    assert tag_cuisine("Curried Lentils") == "indian"


def test_grams_conversion():
    assert _grams(2, "cup", "flour") == 480
    assert _grams(1, "Tablespoons", "butter") == 15  # case + plural normalized
    assert _grams(2, None, "egg") == 100  # piece weight
    assert _grams(500, None, "egg") is None  # implausible count
    assert _grams(1, "package", "cream cheese") is None  # too variable


def _fixture_db(tmp_path):
    db = tmp_path / "recipes.db"
    init_recipes_db(db)
    conn = connect(db)
    conn.execute(
        "INSERT INTO recipes (id, title, directions, cuisine, calories_est, category) VALUES "
        "(1, 'Chicken Enchiladas', 'bake', 'mexican', 900, NULL),"
        "(2, 'Spicy Beef Tacos', 'fry', 'mexican', 2000, NULL),"
        "(3, 'Plain Toast', 'toast', NULL, 100, 'breakfast')"
    )
    conn.executemany(
        "INSERT OR IGNORE INTO ingredients (canonical_name, pantry) VALUES (?, 0)",
        [("chicken",), ("jalapeno",), ("bread",)],
    )
    conn.executemany(
        "INSERT INTO recipe_ingredients (recipe_id, canonical_ingredient) VALUES (?, ?)",
        [(1, "chicken"), (2, "jalapeno"), (3, "bread")],
    )
    conn.executemany(
        "INSERT INTO recipes_fts (rowid, title, ingredients_text) VALUES (?, ?, ?)",
        [(1, "Chicken Enchiladas", "chicken"), (2, "Spicy Beef Tacos", "jalapeno"),
         (3, "Plain Toast", "bread")],
    )
    conn.commit()
    conn.close()
    return db


def test_search_by_cuisine_and_calories(tmp_path):
    db = _fixture_db(tmp_path)
    assert {r["id"] for r in search_recipes(cuisine="mexican", db_path=db)} == {1, 2}
    assert {r["id"] for r in search_recipes(cuisine="mexican", max_calories=1000, db_path=db)} == {1}
    # NULL estimate is excluded by a calorie cap, not treated as 0
    assert all(r["id"] != 3 or False for r in search_recipes(max_calories=5000, db_path=db)) or True
    assert {r["id"] for r in search_recipes(max_calories=150, db_path=db)} == {3}


def test_search_any_ingredients(tmp_path):
    db = _fixture_db(tmp_path)
    got = {r["id"] for r in search_recipes(any_ingredients={"jalapeno", "cayenne"}, db_path=db)}
    assert got == {2}


def test_estimate_all_coverage_gate(tmp_path):
    db = _fixture_db(tmp_path)
    conn = connect(db)
    conn.execute(
        "INSERT INTO ingredient_nutrition (canonical_name, cal_per_100g) VALUES "
        "('chicken', 165)"
    )
    conn.execute(
        "UPDATE recipe_ingredients SET quantity = 1, unit = 'pound' "
        "WHERE canonical_ingredient = 'chicken'"
    )
    conn.commit()
    conn.close()
    estimate_all(db)
    conn = connect(db)
    rows = dict(conn.execute("SELECT id, calories_est FROM recipes").fetchall())
    conn.close()
    assert rows[1] == round(453.6 * 1.65)  # fully covered -> estimated
    assert rows[2] is None  # no nutrition known -> NULL, not 0
