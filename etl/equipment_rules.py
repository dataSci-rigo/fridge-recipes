"""Keyword-based equipment tagging from recipe text.

Best-effort: this is a filter convenience for the ranker, not a science.
Runs over whatever text the source dataset gives us (directions if present,
falling back to the description blurb for sources that lack real directions).
"""
from __future__ import annotations

import re

EQUIPMENT_KEYWORDS: dict[str, list[str]] = {
    "oven": ["preheat", "bake", "baking", "roast", "broil", "oven"],
    "stovetop": ["saute", "sauté", "simmer", "boil", "skillet", "frying pan", "stovetop", "stir-fry", "stir fry"],
    "pressure_cooker": ["instant pot", "pressure cook", "pressure cooker"],
    "slow_cooker": ["slow cooker", "crock pot", "crockpot", "crock-pot"],
    "grill": ["grill", "barbecue", "bbq"],
    "blender": ["blender", "blend until", "puree in a blender"],
    "food_processor": ["food processor", "pulse"],
    "microwave": ["microwave"],
}

_PATTERNS = {
    tag: re.compile("|".join(re.escape(k) for k in keywords), re.IGNORECASE)
    for tag, keywords in EQUIPMENT_KEYWORDS.items()
}


def extract_equipment(text: str) -> set[str]:
    if not text:
        return set()
    found = {tag for tag, pattern in _PATTERNS.items() if pattern.search(text)}
    if not found:
        found.add("no_cook")
    return found
