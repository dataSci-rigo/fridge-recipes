"""Web app: auth gating, demo wizard flow, isolation of demo writes, search
and recipe detail against a small fixture corpus."""
from __future__ import annotations

import importlib
import sqlite3

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    app_db = tmp_path / "app.db"
    recipes_db = tmp_path / "recipes.db"
    monkeypatch.setenv("FRIDGE_APP_DB", str(app_db))
    monkeypatch.setenv("FRIDGE_RECIPES_DB", str(recipes_db))
    monkeypatch.setenv("FRIDGE_IMAGES_DIR", str(tmp_path / "images"))
    monkeypatch.setenv("FRIDGE_WEB_USERNAME", "u")
    monkeypatch.setenv("FRIDGE_WEB_PASSWORD", "p")

    # config reads env at import time -- reload the chain so the temp paths stick
    import core.config
    importlib.reload(core.config)
    for mod in (
        "core.db", "core.inventory", "core.search", "core.custom_recipes",
        "core.substitutions", "core.recipe_detail", "core.images",
        "web.state", "web.api", "web.views", "web.main",
    ):
        importlib.reload(importlib.import_module(mod))
    import web.main

    from core.db import connect
    conn = connect(recipes_db)
    conn.execute(
        "INSERT INTO recipes (id, title, directions, active_min, total_min, "
        "time_confidence, category) VALUES "
        "(1, 'Classic Hummus', 'blend it', 10, 10, 'high', 'appetizer'),"
        "(2, 'Spicy Hummus', 'blend it hot', 10, 10, 'high', 'appetizer'),"
        "(3, 'Egg Salad', 'mix', 5, 5, 'high', 'salad')"
    )
    conn.executemany(
        "INSERT OR IGNORE INTO ingredients (canonical_name, pantry) VALUES (?, 0)",
        [("chickpea",), ("tahini",), ("egg",), ("mayonnaise",)],
    )
    conn.executemany(
        "INSERT INTO recipe_ingredients (recipe_id, canonical_ingredient) VALUES (?, ?)",
        [(1, "chickpea"), (1, "tahini"), (2, "chickpea"), (2, "tahini"),
         (3, "egg"), (3, "mayonnaise")],
    )
    conn.executemany(
        "INSERT INTO recipes_fts (rowid, title, ingredients_text) VALUES (?, ?, ?)",
        [(1, "Classic Hummus", "chickpea tahini"),
         (2, "Spicy Hummus", "chickpea tahini"),
         (3, "Egg Salad", "egg mayonnaise")],
    )
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient
    return TestClient(web.main.app, follow_redirects=False), app_db


AUTH = ("u", "p")


def test_personal_routes_require_auth(client):
    c, _db = client
    for path in ("/", "/setup", "/search", "/review", "/add", "/recipe/1"):
        assert c.get(path).status_code == 401, path


def test_demo_and_api_routes_open(client):
    c, _db = client
    assert c.get("/demo").status_code in (303, 307)
    assert c.get("/demo/setup").status_code == 200
    assert c.get("/api/ingredients/autocomplete?q=chi").status_code == 200


def test_demo_wizard_skip_commits_all_and_never_touches_real_inventory(client):
    c, app_db = client
    c.get("/demo")
    r = c.post("/demo/setup", data={"page": "1", "action": "skip"})
    assert r.headers["location"] == "/demo/review"
    review = c.get("/demo/review").text
    assert "egg" in review

    conn = sqlite3.connect(app_db)
    assert conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM demo_inventory WHERE committed = 1"
    ).fetchone()[0] > 50
    conn.close()


def test_demo_select_all_pages_and_done(client):
    c, app_db = client
    c.get("/demo")
    # select all acts across all pages, not just the visible one
    c.post("/demo/setup", data={"page": "1", "action": "select_all"})
    c.post("/demo/setup", data={"page": "1", "action": "done"})
    conn = sqlite3.connect(app_db)
    total = conn.execute("SELECT COUNT(*) FROM demo_inventory").fetchone()[0]
    committed = conn.execute(
        "SELECT COUNT(*) FROM demo_inventory WHERE committed = 1"
    ).fetchone()[0]
    conn.close()
    assert committed == total > 50


def test_demo_page_checkbox_persistence(client):
    c, app_db = client
    c.get("/demo")
    # check only "egg" on page 1, then leave the page
    c.post("/demo/setup", data={"page": "1", "action": "next",
                                "page_item": ["egg", "milk"], "item": ["egg"]})
    conn = sqlite3.connect(app_db)
    sel = dict(conn.execute(
        "SELECT canonical_ingredient, selected FROM demo_inventory "
        "WHERE canonical_ingredient IN ('egg', 'milk')"
    ).fetchall())
    conn.close()
    assert sel == {"egg": 1, "milk": 0}


def test_personal_wizard_commits_to_real_inventory(client):
    c, app_db = client
    c.post("/setup", auth=AUTH,
           data={"page": "1", "action": "done", "page_item": ["egg"], "item": ["egg"]})
    conn = sqlite3.connect(app_db)
    rows = conn.execute("SELECT canonical_ingredient FROM inventory").fetchall()
    conn.close()
    assert rows == [("egg",)]


def test_search_and_recipe_detail(client):
    c, _db = client
    r = c.get("/search?q=hummus", auth=AUTH)
    assert "Classic Hummus" in r.text and "Spicy Hummus" in r.text
    r = c.get("/search?q=hummus&has=tahini", auth=AUTH)
    assert "Hummus" in r.text
    r = c.get("/recipe/3", auth=AUTH)
    assert "Egg Salad" in r.text and "mayonnaise" in r.text
    assert c.get("/recipe/999", auth=AUTH).status_code == 200  # notfound page


def test_cook_uses_inventory(client):
    c, _db = client
    c.post("/setup", auth=AUTH,
           data={"page": "1", "action": "done",
                 "page_item": ["egg", "milk"], "item": ["egg", "milk"]})
    from core.inventory import add_item
    add_item("mayonnaise", location="fridge")
    r = c.get("/search?cook=1", auth=AUTH)
    assert "Egg Salad" in r.text


def test_autocomplete_fragment(client):
    c, _db = client
    r = c.get("/api/ingredients/autocomplete?q=chi&target=has-input")
    assert "chickpea" in r.text and "/img/ingredient/chickpea" in r.text
    assert c.get("/api/ingredients/autocomplete?q=c").text == ""  # <2 chars
