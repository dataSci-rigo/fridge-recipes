from core.models import InventoryItem, Recipe, RecipeIngredient, RankFilters
from core.rank import rank_recipes

CHICKEN = RecipeIngredient("chicken thighs")
LEMON = RecipeIngredient("lemon")
RICE = RecipeIngredient("rice")
CUMIN = RecipeIngredient("cumin")
SALT = RecipeIngredient("salt", pantry=True)
OIL = RecipeIngredient("oil", pantry=True)
HEAVY_CREAM = RecipeIngredient("heavy cream")
THYME = RecipeIngredient("thyme")
SPINACH = RecipeIngredient("spinach")
FLOUR = RecipeIngredient("flour", pantry=True)


def make_inventory():
    return [
        InventoryItem("chicken thighs", state="fresh"),
        InventoryItem("lemon", state="fresh"),
        InventoryItem("rice", state="fresh"),
        InventoryItem("cumin", state="fresh"),
        InventoryItem("spinach", state="use_soon"),
    ]


def test_top_result_is_the_obviously_best_recipe():
    perfect_match = Recipe(
        id=1,
        title="Lemon Chicken Rice",
        ingredients=(CHICKEN, LEMON, RICE, CUMIN, SALT, OIL),
        active_min=20,
        total_min=40,
        equipment=frozenset({"stovetop"}),
    )
    mostly_missing = Recipe(
        id=2,
        title="Cream Sauce Chicken",
        ingredients=(CHICKEN, HEAVY_CREAM, THYME),
        active_min=25,
        total_min=45,
        equipment=frozenset({"stovetop"}),
    )
    unrelated = Recipe(
        id=3,
        title="Flour Tortillas",
        ingredients=(FLOUR, SALT, OIL),
        active_min=15,
        total_min=15,
        equipment=frozenset({"stovetop"}),
    )

    results = rank_recipes(
        [perfect_match, mostly_missing, unrelated], make_inventory()
    )

    assert [r.recipe.id for r in results] == [1, 2]  # unrelated shares nothing, filtered out
    assert results[0].recipe.id == 1
    # salt/oil aren't in the inventory (nobody tracks pantry staples in the
    # fridge), so they still show up as missing -- just cheaply.
    assert results[0].missing == {"salt", "oil"}


def test_missing_pantry_items_barely_affect_score_vs_missing_fresh():
    missing_pantry_only = Recipe(
        id=1,
        title="Chicken with pantry gaps",
        ingredients=(CHICKEN, LEMON, SALT, OIL),
        active_min=20,
        total_min=30,
    )
    nothing_missing = Recipe(
        id=2,
        title="Plain Chicken",
        ingredients=(CHICKEN, LEMON),
        active_min=20,
        total_min=30,
    )
    missing_fresh_item = Recipe(
        id=3,
        title="Chicken with a missing fresh item",
        ingredients=(CHICKEN, LEMON, HEAVY_CREAM),
        active_min=20,
        total_min=30,
    )

    results = rank_recipes(
        [missing_pantry_only, nothing_missing, missing_fresh_item], make_inventory()
    )
    scores = {r.recipe.id: r.score for r in results}

    # missing_pantry_only has full non-pantry coverage (chicken+lemon), same
    # as nothing_missing -- the two missing pantry staples should cost little.
    assert scores[2] > scores[1]
    assert scores[2] - scores[1] < 0.3

    # missing_fresh_item is missing a *fresh* ingredient (heavy cream), which
    # costs both coverage and missing_cost -- much more than pantry gaps.
    assert scores[1] > scores[3]
    assert (scores[1] - scores[3]) > (scores[2] - scores[1])


def test_use_soon_ingredient_boosts_score():
    uses_spinach = Recipe(
        id=1,
        title="Chicken and Spinach",
        ingredients=(CHICKEN, SPINACH),
        active_min=15,
        total_min=25,
    )
    no_spinach = Recipe(
        id=2,
        title="Plain Chicken",
        ingredients=(CHICKEN, LEMON),
        active_min=15,
        total_min=25,
    )

    results = rank_recipes([uses_spinach, no_spinach], make_inventory())
    assert results[0].recipe.id == 1
    assert "spinach" in results[0].explanation


def test_time_and_equipment_filters_exclude_correctly():
    slow_oven_recipe = Recipe(
        id=1,
        title="Slow Roast Chicken",
        ingredients=(CHICKEN, LEMON),
        active_min=15,
        total_min=120,
        equipment=frozenset({"oven"}),
    )
    quick_stovetop_recipe = Recipe(
        id=2,
        title="Quick Chicken Stir Fry",
        ingredients=(CHICKEN, LEMON, RICE),
        active_min=15,
        total_min=20,
        equipment=frozenset({"stovetop"}),
    )

    results = rank_recipes(
        [slow_oven_recipe, quick_stovetop_recipe],
        make_inventory(),
        filters=RankFilters(
            max_total_min=30, allowed_equipment=frozenset({"stovetop"})
        ),
    )

    assert [r.recipe.id for r in results] == [2]


def test_exclude_ingredients_filter():
    with_cream = Recipe(
        id=1,
        title="Creamy Chicken",
        ingredients=(CHICKEN, HEAVY_CREAM),
        active_min=15,
        total_min=25,
    )
    results = rank_recipes(
        [with_cream],
        make_inventory(),
        filters=RankFilters(exclude_ingredients=frozenset({"heavy cream"})),
    )
    assert results == []
