from pathlib import Path

from core import db


def test_init_all_creates_both_databases(tmp_path: Path):
    recipes_path = tmp_path / "recipes.db"
    app_path = tmp_path / "app.db"

    db.init_recipes_db(recipes_path)
    db.init_app_db(app_path)

    assert recipes_path.exists()
    assert app_path.exists()

    conn = db.connect(recipes_path)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert {"recipes", "recipe_ingredients", "ingredients", "ingredient_nutrition"} <= tables

    conn = db.connect(app_path)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert {
        "inventory", "shelf_life", "ratings", "photos", "custom_recipes", "cooked_log",
    } <= tables
