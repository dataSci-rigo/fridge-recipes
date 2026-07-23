"""Plain data structures shared by rank.py, inventory.py, the web app, and the bot.

Kept dependency-free (no DB imports) so rank.py can be tested against fixtures
without touching SQLite.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RecipeIngredient:
    canonical_name: str
    pantry: bool = False


@dataclass(frozen=True)
class Recipe:
    id: int
    title: str
    ingredients: tuple[RecipeIngredient, ...]
    active_min: int | None = None
    total_min: int | None = None
    equipment: frozenset[str] = field(default_factory=frozenset)

    @property
    def nonpantry_ingredients(self) -> set[str]:
        return {i.canonical_name for i in self.ingredients if not i.pantry}

    @property
    def pantry_ingredients(self) -> set[str]:
        return {i.canonical_name for i in self.ingredients if i.pantry}


@dataclass(frozen=True)
class InventoryItem:
    canonical_ingredient: str
    state: str = "fresh"  # fresh | aging | use_soon | expired


@dataclass
class RankFilters:
    max_active_min: int | None = None
    max_total_min: int | None = None
    allowed_equipment: frozenset[str] | None = None  # None = no restriction
    text_query: str | None = None
    exclude_ingredients: frozenset[str] = field(default_factory=frozenset)


@dataclass
class RankedRecipe:
    recipe: Recipe
    score: float
    have: set[str]
    missing: set[str]
    explanation: str
