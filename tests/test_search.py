from core import db
from core.search import find_by_title, get_recipe, load_candidate_recipes, search_recipes


def _make_db(tmp_path):
    path = tmp_path / "recipes.db"
    db.init_recipes_db(path)
    conn = db.connect(path)
    conn.executescript(
        """
        INSERT INTO ingredients (canonical_name, pantry) VALUES
            ('chicken breast', 0), ('lemon', 0), ('salt', 1),
            ('chickpea', 0), ('tahini', 0), ('flour', 0), ('sugar', 0);
        INSERT INTO recipes (id, title, directions, active_min, total_min, equipment, category)
            VALUES (1, 'Lemon Chicken', 'cook it', 20, 40, 'stovetop', NULL),
                   (2, 'Classic Hummus', 'blend it', 10, 10, 'no_cook', 'appetizer'),
                   (3, 'Smoky Hummus', 'blend it smoky', 15, 60, 'no_cook', 'appetizer'),
                   (4, 'Sugar Cookies', 'bake it', 20, 35, 'oven', 'dessert');
        INSERT INTO recipe_ingredients (recipe_id, canonical_ingredient, quantity_text)
            VALUES (1, 'chicken breast', '1 lb'), (1, 'lemon', '1'), (1, 'salt', 'to taste'),
                   (2, 'chickpea', '1 can'), (2, 'tahini', '2 tbsp'),
                   (3, 'chickpea', '1 can'), (3, 'tahini', '3 tbsp'),
                   (4, 'flour', '2 cups'), (4, 'sugar', '1 cup');
        INSERT INTO recipes_fts (rowid, title, ingredients_text) VALUES
            (1, 'Lemon Chicken', 'chicken breast lemon salt'),
            (2, 'Classic Hummus', 'chickpea tahini'),
            (3, 'Smoky Hummus', 'chickpea tahini'),
            (4, 'Sugar Cookies', 'flour sugar');
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


def test_find_by_title_multiple_matches(tmp_path):
    path = _make_db(tmp_path)
    matches = find_by_title("hummus", db_path=path)
    assert {m["title"] for m in matches} == {"Classic Hummus", "Smoky Hummus"}


def test_find_by_title_single_match(tmp_path):
    path = _make_db(tmp_path)
    matches = find_by_title("lemon chicken", db_path=path)
    assert len(matches) == 1
    assert matches[0]["title"] == "Lemon Chicken"


def test_find_by_title_no_match(tmp_path):
    path = _make_db(tmp_path)
    assert find_by_title("kumquat souffle", db_path=path) == []


def test_find_by_title_sanitizes_punctuation(tmp_path):
    path = _make_db(tmp_path)
    # FTS5 syntax chars (quotes, colons) shouldn't raise -- just get stripped.
    matches = find_by_title('hummus" OR title:*', db_path=path)
    assert {m["title"] for m in matches} == {"Classic Hummus", "Smoky Hummus"}


def test_find_by_title_empty_query(tmp_path):
    path = _make_db(tmp_path)
    assert find_by_title("???", db_path=path) == []


def test_search_recipes_by_category(tmp_path):
    path = _make_db(tmp_path)
    matches = search_recipes(category="appetizer", db_path=path)
    assert {m["title"] for m in matches} == {"Classic Hummus", "Smoky Hummus"}


def test_search_recipes_by_max_total_min(tmp_path):
    path = _make_db(tmp_path)
    matches = search_recipes(category="appetizer", max_total_min=30, db_path=path)
    assert {m["title"] for m in matches} == {"Classic Hummus"}


def test_search_recipes_by_max_active_min(tmp_path):
    path = _make_db(tmp_path)
    matches = search_recipes(max_active_min=15, db_path=path)
    assert {m["title"] for m in matches} == {"Classic Hummus", "Smoky Hummus"}


def test_search_recipes_by_ingredients_requires_all(tmp_path):
    path = _make_db(tmp_path)
    matches = search_recipes(ingredients={"chickpea", "tahini"}, db_path=path)
    assert {m["title"] for m in matches} == {"Classic Hummus", "Smoky Hummus"}

    matches_missing_one = search_recipes(
        ingredients={"chickpea", "tahini", "lemon"}, db_path=path
    )
    assert matches_missing_one == []


def test_search_recipes_combined_filters(tmp_path):
    path = _make_db(tmp_path)
    matches = search_recipes(
        query="hummus",
        category="appetizer",
        max_total_min=30,
        ingredients={"chickpea"},
        db_path=path,
    )
    assert {m["title"] for m in matches} == {"Classic Hummus"}


def test_search_recipes_no_filters_returns_all_up_to_limit(tmp_path):
    path = _make_db(tmp_path)
    matches = search_recipes(db_path=path, limit=2)
    assert len(matches) == 2
