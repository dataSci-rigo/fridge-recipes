"""Ingredient-line parsing and canonicalization.

Used by etl/build.py (to build recipe_ingredients) and by the web/bot search
paths (to canonicalize a free-text ingredient list the user types in) --
never duplicate this logic in either client.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import inflect
import yaml
from ingredient_parser import parse_ingredient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PANTRY_PATH = PROJECT_ROOT / "etl" / "pantry.yaml"
ALIASES_PATH = PROJECT_ROOT / "etl" / "aliases.yaml"

_inflect = inflect.engine()

# Descriptor words the parser sometimes leaves attached to the ingredient
# name (e.g. "Cold Butter"). Stripped from either end of the name.
DESCRIPTORS = {
    "fresh", "chopped", "diced", "minced", "sliced", "grated", "shredded",
    "large", "small", "medium", "extra", "cold", "hot", "warm", "room",
    "temperature", "packed", "melted", "softened", "cooked", "raw", "whole",
    "ripe", "peeled", "seeded", "trimmed", "crushed", "ground", "dried",
    "fine", "finely", "coarse", "coarsely", "thin", "thinly", "thick",
    "boneless", "skinless", "lean", "optional", "more", "taste", "divided",
    "plus", "additional", "good", "quality",
}


@dataclass(frozen=True)
class ParsedLine:
    canonical_name: str | None  # None if the line couldn't be parsed into an ingredient
    quantity_text: str | None
    quantity: float | None  # numeric magnitude of the first parsed amount, if any
    unit: str | None        # unit of that amount as free text (e.g. "cup", "g"), if any
    raw_text: str


@lru_cache(maxsize=1)
def _load_pantry() -> set[str]:
    with open(PANTRY_PATH) as f:
        return set(yaml.safe_load(f) or [])


@lru_cache(maxsize=1)
def _load_aliases() -> dict[str, str]:
    with open(ALIASES_PATH) as f:
        return dict(yaml.safe_load(f) or {})


def is_pantry(canonical_name: str) -> bool:
    return canonical_name in _load_pantry()


def _strip_descriptors(name: str) -> str:
    words = name.split()
    while words and words[0].lower() in DESCRIPTORS:
        words.pop(0)
    while words and words[-1].lower() in DESCRIPTORS:
        words.pop()
    return " ".join(words) if words else name


def _singularize(name: str) -> str:
    try:
        singular = _inflect.singular_noun(name)
    except TypeError:
        # inflect has known edge cases where singular_noun() raises on certain
        # multi-word compounds instead of returning False.
        return name
    return singular if singular else name


def canonicalize(name: str) -> str:
    """Lowercase, strip descriptors, singularize, then apply the alias table."""
    cleaned = re.sub(r"\s+", " ", name).strip().lower()
    cleaned = _strip_descriptors(cleaned)
    cleaned = _singularize(cleaned)
    cleaned = cleaned.strip()
    aliases = _load_aliases()
    return aliases.get(cleaned, cleaned)


# Section headers ("SAUSAGE GRAVY"), blank separators, and instruction-y
# lines occasionally show up inside the free-text ingredients blob in the
# open-recipe-data source. Skip lines that don't look like an ingredient.
_HEADER_RE = re.compile(r"^[A-Z][A-Z0-9 /&'-]{2,}$")


def looks_like_section_header(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.endswith(":"):
        # Labels like "Optional Dressing Ingredients:" or "For each bowl:".
        return True
    return bool(_HEADER_RE.match(stripped)) and not any(
        ch.isdigit() for ch in stripped[:3]
    )


def _amount_fields(parsed) -> tuple[str | None, float | None, str | None]:
    if not parsed.amount:
        return None, None, None
    amount = parsed.amount[0]
    if hasattr(amount, "amounts"):
        # CompositeIngredientAmount (e.g. "1 lb 2 oz") -- use its first part
        # for the numeric quantity/unit, keep the full composite text.
        text = amount.text
        amount = amount.amounts[0] if amount.amounts else None
        if amount is None:
            return text, None, None
    else:
        text = amount.text
    try:
        quantity = float(amount.quantity) if amount.quantity is not None else None
    except (TypeError, ValueError):
        # Occasionally the parser leaves a malformed range string (e.g.
        # "1-3-4") in `quantity` instead of a clean Fraction.
        quantity = None
    unit = str(amount.unit) if amount.unit else None
    return text, quantity, unit


def parse_ingredient_line(line: str) -> ParsedLine:
    line = line.strip()
    if not line or looks_like_section_header(line):
        return ParsedLine(None, None, None, None, line)

    try:
        parsed = parse_ingredient(line)
    except Exception:
        return ParsedLine(_fallback_name(line), None, None, None, line)

    if not parsed.name:
        return ParsedLine(_fallback_name(line), None, None, None, line)

    name = " ".join(part.text for part in parsed.name)
    quantity_text, quantity, unit = _amount_fields(parsed)
    return ParsedLine(canonicalize(name), quantity_text, quantity, unit, line)


_LEADING_QTY_RE = re.compile(
    r"^[\d./\-\s]*\s*(cups?|tablespoons?|tbsp|teaspoons?|tsp|ounces?|oz|"
    r"pounds?|lbs?|grams?|g|kg|ml|liters?|l|cloves?|cans?|packages?|"
    r"sticks?|pinch(?:es)?|dash(?:es)?)?\s*",
    re.IGNORECASE,
)


def _fallback_name(line: str) -> str | None:
    """Best-effort name extraction when the NLP parser raises or finds nothing."""
    remainder = _LEADING_QTY_RE.sub("", line, count=1).strip(" ,.")
    if not remainder:
        return None
    # Drop a trailing parenthetical/comment, e.g. "Butter, Cut Into Pieces".
    remainder = remainder.split(",")[0].strip()
    return canonicalize(remainder) if remainder else None
