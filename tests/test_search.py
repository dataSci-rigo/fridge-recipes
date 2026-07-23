from core import db
from core.search import get_recipe, load_candidate_recipes


def _make_db(tmp_path):
    path = tmp_path / "recipes.db"
    db.init_recipes_db(path)
    conn = db.connect(path)
    conn.executescript(
        """
        INSERT INTO ingredients (canonical_name, pantry) VALUES
            ('chicken breast', 0), ('lemon', 0), ('salt', 1);
        INSERT INTO recipes (id, title, directions, active_min, total_min, equipment)
            VALUES (1, 'Lemon Chicken', 'cook it', 20, 40, 'stovetop');
        INSERT INTO recipe_ingredients (recipe_id, canonical_ingredient, quantity_text)
            VALUES (1, 'chicken breast', '1 lb'), (1, 'lemon', '1'), (1, 'salt', 'to taste');
        """
    )
    conn.commit()
    conn.close()
    return path


def test_load_candidate_recipes_builds_recipe_objects(tmp_path):
    path = _make_db(tmp_path)
    recipes = load_candidate_recipes({"chicken breast"}, db_path=path)
    assert len(recipes) == 1
    recipe = recipes[0]
    assert recipe.title == "Lemon Chicken"
    assert recipe.nonpantry_ingredients == {"chicken breast", "lemon"}
    assert recipe.pantry_ingredients == {"salt"}
    assert recipe.equipment == frozenset({"stovetop"})


def test_load_candidate_recipes_empty_inventory(tmp_path):
    path = _make_db(tmp_path)
    assert load_candidate_recipes(set(), db_path=path) == []


def test_load_candidate_recipes_no_match(tmp_path):
    path = _make_db(tmp_path)
    assert load_candidate_recipes({"nonexistent"}, db_path=path) == []


def test_get_recipe_detail(tmp_path):
    path = _make_db(tmp_path)
    detail = get_recipe(1, db_path=path)
    assert detail["title"] == "Lemon Chicken"
    assert detail["directions"] == "cook it"
    names = {i["name"] for i in detail["ingredients"]}
    assert names == {"chicken breast", "lemon", "salt"}


def test_get_recipe_not_found(tmp_path):
    path = _make_db(tmp_path)
    assert get_recipe(999, db_path=path) is None
