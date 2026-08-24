"""One-shot ETL: build data/recipes.db from a raw recipe dataset.

Usage: python -m etl.build --source openrecipes [--limit N] [--workers N]

Currently only the open-recipe-data source is wired up (Phase 1, ~175k
recipes, fast iteration loop). RecipeNLG (2.2M, Phase 7 scale-up) will be
added as a second entry in SOURCES later -- same RawRecipe shape, different
loader.
"""
from __future__ import annotations

import argparse
import gzip
import html
import json
import re
import sys
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path

from core import db
from core.config import RECIPES_DB_PATH
from core.normalize import ParsedLine, is_pantry, parse_ingredient_line
from etl.cuisine_rules import tag_cuisine
from etl.equipment_rules import extract_equipment
from etl.type_rules import tag_category

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

_ISO_DURATION_RE = re.compile(
    r"P(?:(?P<days>\d+)D)?T?(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
)


def parse_iso_duration_minutes(text: str | None) -> int | None:
    """'PT1H30M' -> 90. Returns None for missing/unparseable input."""
    if not text:
        return None
    m = _ISO_DURATION_RE.fullmatch(text.strip())
    if not m or not any(m.groups()):
        return None
    days = int(m.group("days") or 0)
    hours = int(m.group("hours") or 0)
    minutes = int(m.group("minutes") or 0)
    total = days * 24 * 60 + hours * 60 + minutes
    return total or None


@dataclass
class RawRecipe:
    source_id: str
    title: str
    raw_ingredient_lines: list[str]
    description: str
    prep_min: int | None
    cook_min: int | None
    image_url: str | None


def load_openrecipes(path: Path, limit: int | None = None):
    with gzip.open(path, "rt") as f:
        for i, line in enumerate(f):
            if limit is not None and i >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Source data carries HTML entities ("Beets &amp; Greens") that
            # would double-escape in any HTML renderer -- store real text.
            title = html.unescape((doc.get("name") or "").strip())
            ingredients_text = doc.get("ingredients") or ""
            raw_lines = [l.strip() for l in ingredients_text.split("\n") if l.strip()]
            if not title or len(raw_lines) < 2:
                continue
            yield RawRecipe(
                source_id=str(doc.get("_id", {}).get("$oid", i)),
                title=title,
                raw_ingredient_lines=raw_lines,
                description=html.unescape(doc.get("description") or ""),
                prep_min=parse_iso_duration_minutes(doc.get("prepTime")),
                cook_min=parse_iso_duration_minutes(doc.get("cookTime")),
                image_url=(doc.get("image") or "").strip() or None,
            )


SOURCES = {
    "openrecipes": ("recipeitems.json.gz", load_openrecipes),
}


def dedupe(raw_recipes):
    seen = set()
    for r in raw_recipes:
        key = (
            r.title.strip().lower(),
            tuple(sorted(l.lower() for l in r.raw_ingredient_lines)),
        )
        if key in seen:
            continue
        seen.add(key)
        yield r


def _parse_lines_worker(lines: list[str]) -> list[ParsedLine]:
    return [parse_ingredient_line(l) for l in lines]


def build(source: str, limit: int | None, out_path: Path, workers: int) -> None:
    filename, loader = SOURCES[source]
    raw_path = RAW_DIR / filename
    if not raw_path.exists():
        print(f"missing dataset: {raw_path}", file=sys.stderr)
        raise SystemExit(1)

    recipes = list(dedupe(loader(raw_path, limit)))
    print(f"loaded {len(recipes)} deduped recipes")

    db.init_recipes_db(out_path)
    conn = db.connect(out_path)
    conn.executescript(
        "DELETE FROM recipe_ingredients; DELETE FROM recipes; "
        "DELETE FROM ingredients; DELETE FROM recipes_fts;"
    )

    line_lists = [r.raw_ingredient_lines for r in recipes]
    with Pool(workers) as pool:
        parsed_lists = pool.map(_parse_lines_worker, line_lists, chunksize=50)

    known_ingredients: set[str] = set()
    recipe_id = 0
    inserted = 0
    for raw, parsed_lines in zip(recipes, parsed_lists):
        canonical_lines = [
            (p.canonical_name, p.quantity_text, p.quantity, p.unit)
            for p in parsed_lines
            if p.canonical_name
        ]
        if len(canonical_lines) < 2:
            continue
        recipe_id += 1

        directions = raw.description
        equipment = extract_equipment(directions)
        prep, cook = raw.prep_min, raw.cook_min
        active_min = prep
        total_min = (prep or 0) + (cook or 0) if (prep or cook) else None
        time_confidence = "high" if (prep is not None or cook is not None) else "none"

        conn.execute(
            "INSERT INTO recipes (id, title, directions, active_min, total_min, "
            "time_confidence, equipment, category, cuisine, image_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                recipe_id,
                raw.title,
                directions,
                active_min,
                total_min,
                time_confidence,
                ",".join(sorted(equipment)),
                tag_category(raw.title),
                tag_cuisine(raw.title),
                raw.image_url,
            ),
        )
        for name, qty_text, qty, unit in canonical_lines:
            if name not in known_ingredients:
                known_ingredients.add(name)
                conn.execute(
                    "INSERT OR IGNORE INTO ingredients (canonical_name, pantry) VALUES (?, ?)",
                    (name, int(is_pantry(name))),
                )
            conn.execute(
                "INSERT OR IGNORE INTO recipe_ingredients "
                "(recipe_id, canonical_ingredient, quantity_text, quantity, unit) "
                "VALUES (?, ?, ?, ?, ?)",
                (recipe_id, name, qty_text, qty, unit),
            )
        conn.execute(
            "INSERT INTO recipes_fts (rowid, title, ingredients_text) VALUES (?, ?, ?)",
            (recipe_id, raw.title, " ".join(n for n, _, _, _ in canonical_lines)),
        )
        inserted += 1

    conn.commit()
    conn.close()
    print(f"wrote {inserted} recipes, {len(known_ingredients)} distinct ingredients -> {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="openrecipes", choices=SOURCES.keys())
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=Path, default=RECIPES_DB_PATH)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    build(args.source, args.limit, args.out, args.workers)


if __name__ == "__main__":
    main()
