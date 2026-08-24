"""
Extract a recipe (title, ingredients incl. substitution alternatives,
directions) from a photo/screenshot or pasted text, using Claude -- same
shape as food/ocr.py's nutrition-label reader (strict JSON-extraction
prompt, markdown-fence stripping, raises ValueError on a miss).

Moved here from food/recipe_ingest.py so both the Telegram bot and the web
app can call it (food/ depends on fridge_recipes, not the reverse). Reads
ANTHROPIC_API_KEY straight from the environment, same as
core/substitutions.py's _claude_suggest -- no food/config.py import.

Deliberately does NOT invoke the local ingredient-parser-nlp model: Claude
supplies its own best-guess canonical ingredient name per line, which then
only needs the cheap part of core.normalize (canonicalize(): descriptor
stripping + inflect + alias lookup, no model load) to line up with the rest
of the vocabulary. Keeps the e2-micro VM out of the heavy-parsing business;
the trained parser only runs later, in etl/promote_custom_recipes.py on the
laptop.
"""

from __future__ import annotations

import base64
import json
import os
import re

import anthropic

_SYSTEM = """\
You extract recipes from text or images. Return ONLY valid JSON:
{
  "title": "Recipe Name",
  "ingredients": [
    {"raw_text": "1 cup butter, or margarine", "quantity_text": "1 cup",
     "canonical_guess": "butter", "alternatives": ["margarine"]}
  ],
  "directions": "Step-by-step instructions as one text block."
}
- raw_text: the ingredient line as written/read, verbatim.
- quantity_text: just the amount+unit portion, e.g. "1 cup", "2 tbsp", null if none.
- canonical_guess: your best simple/generic name for the ingredient -- lowercase,
  singular, with descriptors like "fresh"/"chopped"/"large" removed, e.g.
  "2 cups all-purpose flour, sifted" -> "all purpose flour".
- alternatives: any "or X" / "you can substitute Y" alternatives mentioned on that
  same line, as canonical-style names (same rules as canonical_guess). Empty list
  if the line names only one ingredient.
- directions: cooking instructions as one text block. Empty string if genuinely
  not present in the input.
If the input does not contain a recipe at all, return: {"error": "no recipe found"}
No prose, no markdown -- raw JSON only."""


def _client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _parse_response(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    data = json.loads(raw)
    if data.get("error"):
        raise ValueError("No recipe found in input")
    return data


async def extract_from_image(image_bytes: bytes, mime: str = "image/jpeg") -> dict:
    b64 = base64.standard_b64encode(image_bytes).decode()
    msg = await _client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime, "data": b64},
                    },
                    {"type": "text", "text": "Extract the recipe from this image."},
                ],
            }
        ],
    )
    return _parse_response(msg.content[0].text)


def save_extracted(
    extracted: dict, user_input: str | None, db_path=None
) -> tuple[int, dict]:
    """Persist an extract_from_*() result as a custom recipe: canonicalize
    Claude's per-line guesses with the cheap canonicalize() (keeping raw_text
    for etl/promote_custom_recipes.py's later heavy re-parse) and record any
    per-line alternatives as 'ingested' substitution pairs. Shared by the
    bot's photo/paste handlers and the web app's add-your-own page."""
    from core.config import APP_DB_PATH
    from core.custom_recipes import save_recipe
    from core.normalize import canonicalize
    from core.substitutions import add_substitution

    db_path = db_path or APP_DB_PATH
    ingredients = []
    alternatives: dict[str, list[str]] = {}
    for line in extracted.get("ingredients", []):
        canonical = canonicalize(line.get("canonical_guess") or line.get("raw_text", ""))
        if not canonical:
            continue
        ingredients.append({
            "canonical_name": canonical,
            "quantity_text": line.get("quantity_text"),
            "raw_text": line.get("raw_text"),  # kept for promote's re-parse
        })
        alts = [canonicalize(a) for a in line.get("alternatives") or [] if a]
        if alts:
            alternatives[canonical] = alts
            for alt in alts:
                add_substitution(canonical, alt, source="ingested", db_path=db_path)

    title = extracted.get("title") or "Untitled recipe"
    recipe_id = save_recipe(
        title, ingredients, directions=extracted.get("directions"),
        alternatives=alternatives, user_input=user_input, db_path=db_path,
    )
    return recipe_id, {"title": title, "ingredients": ingredients, "alternatives": alternatives}


async def extract_from_text(text: str) -> dict:
    msg = await _client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=_SYSTEM,
        messages=[{"role": "user", "content": text}],
    )
    return _parse_response(msg.content[0].text)
