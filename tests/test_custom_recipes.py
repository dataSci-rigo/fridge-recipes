from core import custom_recipes as cr
from core import db
from core.models import InventoryItem


def _make_db(tmp_path):
    path = tmp_path / "app.db"
    db.init_app_db(path)
    return path


def test_save_and_get_recipe(tmp_path):
    path = _make_db(tmp_path)
    rid = cr.save_recipe(
        "Grandma's Chili",
        [{"canonical_name": "ground beef", "quantity_text": "1 lb"},
         {"canonical_name": "kidney bean", "quantity_text": "1 can"}],
        directions="Brown beef, add beans, simmer.",
        alternatives={"ground beef": ["ground turkey"]},
        db_path=path,
    )
    detail = cr.get_custom_recipe(rid, db_path=path)
    assert detail["name"] == "Grandma's Chili"
    assert len(detail["ingredients"]) == 2
    assert detail["alternatives"] == {"ground beef": ["ground turkey"]}
    assert detail["promoted_recipe_id"] is None


def test_save_recipe_upserts_on_same_name(tmp_path):
    path = _make_db(tmp_path)
    rid1 = cr.save_recipe("Soup", [{"canonical_name": "carrot", "quantity_text": None}], db_path=path)
    rid2 = cr.save_recipe("Soup", [{"canonical_name": "celery", "quantity_text": None}], db_path=path)
    assert rid1 == rid2
    detail = cr.get_custom_recipe(rid1, db_path=path)
    assert detail["ingredients"][0]["canonical_name"] == "celery"


def test_list_unpromoted_excludes_promoted(tmp_path):
    path = _make_db(tmp_path)
    rid = cr.save_recipe("Soup", [{"canonical_name": "carrot", "quantity_text": None}], db_path=path)
    assert len(cr.list_unpromoted(db_path=path)) == 1

    cr.mark_promoted(rid, promoted_recipe_id=99999, db_path=path)
    assert cr.list_unpromoted(db_path=path) == []


def test_delete_recipe(tmp_path):
    path = _make_db(tmp_path)
    rid = cr.save_recipe("Soup", [{"canonical_name": "carrot", "quantity_text": None}], db_path=path)
    assert cr.delete_recipe(rid, db_path=path) is True
    assert cr.get_custom_recipe(rid, db_path=path) is None
    assert cr.delete_recipe(rid, db_path=path) is False


def test_find_custom_substring_match(tmp_path):
    path = _make_db(tmp_path)
    cr.save_recipe("Grandma's Chili", [{"canonical_name": "ground beef", "quantity_text": None}], db_path=path)
    cr.save_recipe("Weeknight Tacos", [{"canonical_name": "ground beef", "quantity_text": None}], db_path=path)

    matches = cr.find_custom("chili", db_path=path)
    assert [m["title"] for m in matches] == ["Grandma's Chili"]

    assert cr.find_custom("nonexistent", db_path=path) == []


def test_rank_custom_scores_against_inventory(tmp_path):
    path = _make_db(tmp_path)
    cr.save_recipe(
        "Grandma's Chili",
        [{"canonical_name": "ground beef", "quantity_text": "1 lb"},
         {"canonical_name": "kidney bean", "quantity_text": "1 can"}],
        db_path=path,
    )
    cr.save_recipe(
        "Unrelated Cake",
        [{"canonical_name": "flour", "quantity_text": "2 cups"}],
        db_path=path,
    )

    # core.inventory.add_item always canonicalizes before storing, so real
    # inventory rows for "ground beef" show up as "beef" (canonicalize()
    # strips "ground" as a descriptor) -- match that here.
    inventory = [InventoryItem("beef", state="fresh")]
    results = cr.rank_custom(inventory, db_path=path)

    assert len(results) == 1
    assert results[0].recipe.title == "Grandma's Chili"
    assert "kidney bean" in results[0].missing
