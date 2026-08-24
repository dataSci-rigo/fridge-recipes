"""core/recipe_ingest.py -- JSON parsing + save_extracted, no API calls."""
from __future__ import annotations

import json

import pytest

from core.db import init_app_db
from core.recipe_ingest import _parse_response, save_extracted


def test_parse_response_plain_json():
    data = _parse_response('{"title": "Toast", "ingredients": [], "directions": ""}')
    assert data["title"] == "Toast"


def test_parse_response_strips_markdown_fences():
    raw = '```json\n{"title": "Toast", "ingredients": []}\n```'
    assert _parse_response(raw)["title"] == "Toast"


def test_parse_response_error_raises():
    with pytest.raises(ValueError):
        _parse_response('{"error": "no recipe found"}')


def test_save_extracted_canonicalizes_and_records_alternatives(tmp_path):
    db = tmp_path / "app.db"
    init_app_db(db)
    extracted = {
        "title": "Meatballs",
        "directions": "roll and bake",
        "ingredients": [
            {"raw_text": "1 lb ground beef, or ground turkey",
             "quantity_text": "1 lb", "canonical_guess": "ground beef",
             "alternatives": ["ground turkey"]},
            {"raw_text": "2 Fresh Eggs", "quantity_text": "2",
             "canonical_guess": "fresh eggs", "alternatives": []},
        ],
    }
    recipe_id, saved = save_extracted(extracted, user_input="pasted", db_path=db)
    assert recipe_id > 0
    names = [i["canonical_name"] for i in saved["ingredients"]]
    assert "beef" in names and "egg" in names  # descriptors stripped, singularized
    # raw_text preserved for the laptop promotion re-parse
    assert saved["ingredients"][0]["raw_text"].startswith("1 lb ground beef")

    import sqlite3
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT ingredients_json, alternatives_json FROM custom_recipes WHERE id = ?",
        (recipe_id,),
    ).fetchone()
    subs = conn.execute(
        "SELECT canonical_a, canonical_b, source FROM substitutions"
    ).fetchall()
    conn.close()
    assert json.loads(row[1]) == {"beef": ["turkey"]}
    assert ("beef", "turkey", "ingested") in subs
