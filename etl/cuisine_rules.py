"""Keyword-based cuisine tagging from a recipe title.

Same best-effort stance and mechanics as type_rules.py (the dataset has no
cuisine field): ordered keyword lists, first match wins, word-boundary
regex with a simple optional plural. Keywords are dish names strongly tied
to one cuisine -- generic words that appear across cuisines (rice, noodle,
chicken) are deliberately absent.
"""
from __future__ import annotations

import re

# Ordered: more specific cuisines first. "pad thai" must hit thai before
# "curry" (further down, in indian) could ever see the title; "miso ramen"
# should tag japanese, not chinese via "noodle"-style loose words (which we
# don't use at all for that reason).
CUISINE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("thai", ["thai", "pad see ew", "tom yum", "satay", "larb",
              "green curry", "red curry", "massaman"]),
    ("japanese", ["japanese", "sushi", "teriyaki", "miso", "ramen", "tempura",
                  "udon", "soba", "yakitori", "katsu", "edamame"]),
    ("chinese", ["chinese", "stir-fry", "stir fry", "stir-fried", "stir fried",
                 "lo mein", "chow mein", "fried rice", "szechuan", "sichuan",
                 "kung pao", "wonton", "dumpling", "potsticker", "hoisin",
                 "general tso", "egg roll", "spring roll"]),
    ("indian", ["indian", "curry", "curried", "masala", "tikka", "tandoori",
                "dal", "naan", "biryani", "korma", "vindaloo", "paneer",
                "samosa", "chana"]),
    ("mexican", ["mexican", "taco", "burrito", "enchilada", "quesadilla",
                 "fajita", "tortilla", "carnitas", "tamale", "guacamole",
                 "tostada", "pico de gallo", "chilaquiles", "pozole"]),
    ("italian", ["italian", "pasta", "spaghetti", "lasagna", "lasagne",
                 "risotto", "pizza", "parmigiana", "alfredo", "pesto",
                 "gnocchi", "bruschetta", "carbonara", "marinara", "ravioli",
                 "linguine", "fettuccine", "penne", "orzo", "caprese",
                 "minestrone", "piccata", "frittata", "polenta"]),
    ("french", ["french", "ratatouille", "quiche", "crepe", "coq au vin",
                "bourguignon", "provencal", "nicoise", "au gratin", "souffle",
                "cassoulet", "beurre blanc"]),
    ("greek", ["greek", "gyro", "tzatziki", "feta", "moussaka", "spanakopita",
               "souvlaki", "orzo salad"]),
    ("mediterranean", ["mediterranean", "hummus", "falafel", "couscous",
                       "tabbouleh", "tabouli", "shawarma", "baba ganoush",
                       "tagine", "za'atar", "harissa"]),
    ("cajun", ["cajun", "creole", "gumbo", "jambalaya", "etouffee",
               "andouille", "po boy", "po' boy", "dirty rice"]),
    ("american", ["burger", "bbq", "barbecue", "barbecued", "meatloaf",
                  "sloppy joe", "mac and cheese", "buffalo wing",
                  "cornbread", "corn dog", "pot pie", "coleslaw"]),
]

_PATTERNS = [
    (
        cuisine,
        re.compile(
            "|".join(rf"\b{re.escape(k)}s?\b" for k in keywords), re.IGNORECASE
        ),
    )
    for cuisine, keywords in CUISINE_KEYWORDS
]

CUISINES = [c for c, _ in CUISINE_KEYWORDS]


def tag_cuisine(title: str) -> str | None:
    if not title:
        return None
    for cuisine, pattern in _PATTERNS:
        if pattern.search(title):
            return cuisine
    return None
