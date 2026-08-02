"""Keyword-based meal-category tagging from a recipe title.

The source dataset (open-recipe-data) has no cuisine/meal-type field at
all, so this is best-effort inference from the title only -- same "filter
convenience, not a science" stance as equipment_rules.py. Unlike equipment
(a recipe can use several appliances), a recipe usually has one primary
category, so this returns a single best guess (or None) rather than a set.
Checked in priority order -- e.g. "chocolate cake" should tag dessert, not
get missed because "cake" also loosely reads as a side.
"""
from __future__ import annotations

import re

# Ordered: earlier categories win on a title that could plausibly match more
# than one (e.g. "breakfast cookie" -> breakfast, checked before dessert).
CATEGORY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("breakfast", ["breakfast", "brunch", "pancake", "waffle", "omelet", "omelette",
                   "french toast", "granola", "oatmeal", "hash brown"]),
    ("beverage", ["smoothie", "cocktail", "milkshake", "lemonade", "punch",
                  "iced tea", "hot chocolate", "mocktail"]),
    ("dessert", ["cake", "cookie", "brownie", "cupcake", "pie", "tart", "pudding",
                 "ice cream", "dessert", "fudge", "candy", "mousse", "cheesecake",
                 "sorbet", "custard"]),
    ("bread", ["bread", "muffin", "biscuit", "scone", "roll", "focaccia", "bagel"]),
    ("soup", ["soup", "chowder", "bisque"]),
    ("salad", ["salad", "slaw"]),
    ("appetizer", ["appetizer", "dip", "starter", "finger food", "canape"]),
    ("sauce_or_condiment", ["sauce", "dressing", "marinade", "salsa", "jam",
                             "relish", "chutney", "condiment", "gravy"]),
    ("side_dish", ["side dish"]),
]

_PATTERNS = [
    (
        category,
        # "s?" allows a simple plural (cookie/cookies) without also matching
        # e.g. "stew" inside "stewed" -- \b still requires a real word break
        # right after the (optional) trailing s.
        re.compile(
            "|".join(rf"\b{re.escape(k)}s?\b" for k in keywords), re.IGNORECASE
        ),
    )
    for category, keywords in CATEGORY_KEYWORDS
]


def tag_category(title: str) -> str | None:
    if not title:
        return None
    for category, pattern in _PATTERNS:
        if pattern.search(title):
            return category
    return None
