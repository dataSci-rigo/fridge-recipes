"""Pure-Python ranking of recipes against what's in the fridge.

No DB access here — everything operates on Recipe / InventoryItem /
RankFilters from core.models, so it can be exercised with fixtures. The web
app and bot both call rank_recipes() after loading recipes from db.py; keep
it that way rather than duplicating scoring logic in either client. Callers
that want substitution-aware scoring (core.substitutions.get_substitutes)
pass it in as `get_substitutes_fn` rather than this module importing the DB
itself.
"""
from __future__ import annotations

from typing import Callable

from core.models import InventoryItem, Recipe, RankedRecipe, RankFilters

COVERAGE_WEIGHT = 3.0
MISSING_FRESH_PENALTY = 1.0
MISSING_PANTRY_PENALTY = 0.1
SUBSTITUTION_PENALTY = 0.4  # between pantry and fresh: usable, but not a perfect match
USE_SOON_BONUS = 0.5
PREFERENCE_WEIGHT = 0.1
SIMPLICITY_WEIGHT = 0.2

def _no_substitutes(name: str) -> set[str]:
    return set()


def _passes_filters(recipe: Recipe, filters: RankFilters) -> bool:
    if filters.max_active_min is not None:
        if recipe.active_min is None or recipe.active_min > filters.max_active_min:
            return False
    if filters.max_total_min is not None:
        if recipe.total_min is None or recipe.total_min > filters.max_total_min:
            return False
    if filters.allowed_equipment is not None:
        if not recipe.equipment.issubset(filters.allowed_equipment):
            return False
    if filters.exclude_ingredients:
        all_ingredients = {i.canonical_name for i in recipe.ingredients}
        if all_ingredients & filters.exclude_ingredients:
            return False
    if filters.text_query:
        if filters.text_query.lower() not in recipe.title.lower():
            return False
    return True


def _score_recipe(
    recipe: Recipe,
    have_ingredients: set[str],
    use_soon: set[str],
    preferences: dict[str, float],
    get_substitutes_fn: Callable[[str], set[str]],
) -> tuple[float, set[str], set[str], dict[str, str]]:
    needed_nonpantry = recipe.nonpantry_ingredients
    needed_pantry = recipe.pantry_ingredients

    have = (needed_nonpantry & have_ingredients) | (needed_pantry & have_ingredients)
    missing_nonpantry = needed_nonpantry - have_ingredients
    missing_pantry = needed_pantry - have_ingredients

    # For each missing non-pantry ingredient, see if something on hand can
    # stand in for it (e.g. milk for buttermilk). Substituted ingredients
    # count toward coverage but cost more than a direct match.
    substituted: dict[str, str] = {}  # needed ingredient -> the have-ingredient used instead
    for needed in sorted(missing_nonpantry):
        subs = get_substitutes_fn(needed) & have_ingredients
        if subs:
            substituted[needed] = sorted(subs)[0]
    missing_nonpantry = missing_nonpantry - substituted.keys()
    missing = missing_nonpantry | missing_pantry

    covered_nonpantry = len(needed_nonpantry & have_ingredients) + len(substituted)
    coverage = covered_nonpantry / len(needed_nonpantry) if needed_nonpantry else 1.0
    missing_cost = (
        len(missing_nonpantry) * MISSING_FRESH_PENALTY
        + len(missing_pantry) * MISSING_PANTRY_PENALTY
        + len(substituted) * SUBSTITUTION_PENALTY
    )
    use_it_up = len(needed_nonpantry & use_soon) * USE_SOON_BONUS
    preference = sum(preferences.get(name, 0.0) for name in have) * PREFERENCE_WEIGHT
    total_ingredients = len(recipe.ingredients)
    simplicity = SIMPLICITY_WEIGHT / total_ingredients if total_ingredients else 0.0

    score = (
        coverage * COVERAGE_WEIGHT
        - missing_cost
        + use_it_up
        + preference
        + simplicity
    )
    return score, have, missing, substituted


def _explain(
    recipe: Recipe,
    have: set[str],
    missing: set[str],
    use_soon: set[str],
    substituted: dict[str, str],
) -> str:
    have_count = len(have) + len(substituted)
    total = have_count + len(missing)
    parts = [f"uses {have_count} of your {total} ingredients"]
    if substituted:
        subs_str = ", ".join(f"{have_name} instead of {needed}" for needed, have_name in sorted(substituted.items()))
        parts.append(f"using {subs_str}")
    if missing:
        parts.append("missing: " + ", ".join(sorted(missing)))
    using_up = sorted((recipe.nonpantry_ingredients | recipe.pantry_ingredients) & use_soon)
    if using_up:
        parts.append(f"uses up {', '.join(using_up)} before it expires")
    return "; ".join(parts)


def rank_recipes(
    recipes: list[Recipe],
    inventory: list[InventoryItem],
    filters: RankFilters | None = None,
    preferences: dict[str, float] | None = None,
    top_n: int | None = None,
    get_substitutes_fn: Callable[[str], set[str]] | None = None,
) -> list[RankedRecipe]:
    filters = filters or RankFilters()
    preferences = preferences or {}
    get_substitutes_fn = get_substitutes_fn or _no_substitutes

    have_ingredients = {i.canonical_ingredient for i in inventory}
    use_soon = {i.canonical_ingredient for i in inventory if i.state == "use_soon"}

    results: list[RankedRecipe] = []
    for recipe in recipes:
        if not _passes_filters(recipe, filters):
            continue
        if not recipe.nonpantry_ingredients & have_ingredients:
            continue  # must share at least one non-pantry ingredient with inventory
        score, have, missing, substituted = _score_recipe(
            recipe, have_ingredients, use_soon, preferences, get_substitutes_fn
        )
        explanation = _explain(recipe, have, missing, use_soon, substituted)
        results.append(RankedRecipe(recipe, score, have, missing, explanation))

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:top_n] if top_n is not None else results
