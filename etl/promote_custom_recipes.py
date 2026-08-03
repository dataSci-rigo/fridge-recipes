"""Laptop-run batch: fold unpromoted custom_recipes (ingested via photo/paste
on the VM) into the main recipes.db corpus with full accuracy -- re-parses
every ingredient with the trained NLP parser (core.normalize.
parse_ingredient_line, more accurate than the live bot's Claude-only guess),
backfills nutrition the same way etl/nutrition_backfill.py does, and extends
the FAISS embeddings index so promoted recipes show up in /recommend too.
Never runs on the VM -- see fridge_handlers.py's module docstring.

Usage (weekly/monthly, from the laptop):
  1. python env_sync.py pull_laptop fridge-recipes     # VM's app.db -> local mirror
  2. python -m etl.promote_custom_recipes               # this script
  3. python env_sync.py push_laptop fridge-recipes      # recipes.db + faiss -> VM
     (new ids only, so push_laptop's INSERT-OR-IGNORE merge is safe here)
  4. python -m etl.promote_custom_recipes --marks-only   # separately marks
     promoted_recipe_id back on the VM's *live* app.db (a targeted UPDATE,
     not a generic file merge -- see the module note on _merge_sqlite below)

Requires ingredient-parser-nlp (same heavy dependency etl/build.py needs)
-- not installed on the VM, this only ever runs locally.
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from core.config import RECIPES_DB_PATH
from core.custom_recipes import list_unpromoted, mark_promoted
from core.db import connect
from core.normalize import canonicalize, is_pantry, parse_ingredient_line
from etl.embed_recipes import DEFAULT_OUT as DEFAULT_FAISS_PATH
from etl.nutrition_backfill import backfill as backfill_nutrition
from etl.type_rules import tag_category

DATA_BACKUPS = Path.home() / "Documents" / "data_backups" / "fridge-recipes"
DEFAULT_APP_DB_MIRROR = DATA_BACKUPS / "app.db"


def _next_recipe_id(conn) -> int:
    row = conn.execute("SELECT MAX(id) FROM recipes").fetchone()
    return (row[0] or 0) + 1


_ALTERNATIVE_CLAUSE_RE = re.compile(r",?\s+(?:or|substitute)\s+.+$", re.IGNORECASE)


def _primary_clause(raw_text: str) -> str:
    """parse_ingredient_line's NLP model is trained on single-ingredient
    lines -- fed "1 lb ground beef, or ground turkey" whole, it garbles the
    two names together instead of picking one. The alternative is already
    captured separately (custom["alternatives"], from the ingest-time Claude
    extraction), so strip that clause before re-parsing the primary."""
    return _ALTERNATIVE_CLAUSE_RE.sub("", raw_text).strip()


def _promote_one(conn, custom: dict, recipe_id: int) -> None:
    parsed_lines = []
    for ing in custom["ingredients"]:
        raw = ing.get("raw_text")
        if raw:
            parsed_lines.append(parse_ingredient_line(_primary_clause(raw)))
        else:
            # No raw text captured (older ingest) -- fall back to the
            # already-canonicalized guess with no quantity/unit structure.
            parsed_lines.append(None)

    directions = custom["directions"] or ""
    category = tag_category(custom["name"])
    conn.execute(
        "INSERT INTO recipes (id, title, directions, active_min, total_min, "
        "time_confidence, equipment, category) VALUES (?, ?, ?, NULL, NULL, 'none', NULL, ?)",
        (recipe_id, custom["name"], directions, category),
    )

    ingredients_text_parts = []
    for ing, parsed in zip(custom["ingredients"], parsed_lines):
        if parsed is not None and parsed.canonical_name:
            name, qty_text, qty, unit = parsed.canonical_name, parsed.quantity_text, parsed.quantity, parsed.unit
        else:
            name = canonicalize(ing["canonical_name"])
            qty_text, qty, unit = ing.get("quantity_text"), None, None
        if not name:
            continue
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
        ingredients_text_parts.append(name)

    conn.execute(
        "INSERT INTO recipes_fts (rowid, title, ingredients_text) VALUES (?, ?, ?)",
        (recipe_id, custom["name"], " ".join(ingredients_text_parts)),
    )


def promote_all(app_db_path: Path, recipes_db_path: Path) -> list[tuple[int, int]]:
    """Returns [(custom_recipe_id, new_recipe_id), ...] for what got promoted."""
    unpromoted = list_unpromoted(db_path=app_db_path)
    if not unpromoted:
        print("nothing to promote")
        return []

    conn = connect(recipes_db_path)
    promoted: list[tuple[int, int]] = []
    try:
        next_id = _next_recipe_id(conn)
        for custom in unpromoted:
            if not custom["ingredients"]:
                print(f"  skipping c{custom['id']} ({custom['name']!r}) -- no ingredients")
                continue
            _promote_one(conn, custom, next_id)
            promoted.append((custom["id"], next_id))
            print(f"  c{custom['id']} -> #{next_id}  {custom['name']}")
            next_id += 1
        conn.commit()
    finally:
        conn.close()

    for custom_id, new_id in promoted:
        mark_promoted(custom_id, new_id, db_path=app_db_path)

    return promoted


def extend_embeddings(
    promoted: list[tuple[int, int]], recipes_db_path: Path, faiss_path: Path
) -> None:
    if not promoted:
        return
    import faiss
    import numpy as np
    from sentence_transformers import SentenceTransformer

    from etl.embed_recipes import EMBED_DIM, MODEL_REPOS

    conn = connect(recipes_db_path)
    try:
        ids = [new_id for _, new_id in promoted]
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT rowid, title, ingredients_text FROM recipes_fts WHERE rowid IN ({placeholders})",
            ids,
        ).fetchall()
    finally:
        conn.close()

    texts = [f"{title}. {ingredients}" for _rowid, title, ingredients in rows]
    row_ids = [rowid for rowid, _t, _i in rows]

    model = SentenceTransformer(MODEL_REPOS["bge-small-en-v1.5"])
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

    index = faiss.read_index(str(faiss_path))
    index.add_with_ids(
        np.asarray(vectors, dtype="float32"), np.asarray(row_ids, dtype="int64")
    )
    faiss.write_index(index, str(faiss_path))
    print(f"extended embeddings index with {len(row_ids)} vector(s) -> {faiss_path}")


def already_promoted_locally(app_db_path: Path) -> list[tuple[int, int]]:
    """(custom_recipe_id, promoted_recipe_id) pairs already marked in the
    local mirror -- used by the second, separate --marks-only invocation
    (after push_laptop has actually shipped recipes.db to the VM; pushing
    marks any earlier would point the VM at recipe ids it doesn't have
    yet)."""
    conn = connect(app_db_path)
    try:
        rows = conn.execute(
            "SELECT id, promoted_recipe_id FROM custom_recipes WHERE promoted_recipe_id IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    return [(r[0], r[1]) for r in rows]


_REMOTE_MARK_SCRIPT = """\
import json, sqlite3, sys
pairs = json.loads(sys.argv[1])
conn = sqlite3.connect("data/app.db")
conn.executemany(
    "UPDATE custom_recipes SET promoted_recipe_id = ? WHERE id = ?",
    [(new_id, custom_id) for custom_id, new_id in pairs],
)
conn.commit()
print(f"updated {conn.total_changes} row(s)")
"""


def push_marks_to_vm(promoted: list[tuple[int, int]], vm: str, zone: str) -> None:
    """Targeted UPDATE of promoted_recipe_id on the VM's *live* app.db.
    Not routed through env_sync.py's push_laptop/_merge_sqlite: that helper
    is INSERT-OR-IGNORE-only and can't update an existing row, and app.db
    holds live inventory/ratings we must not touch otherwise. Ships a small
    script + runs it remotely rather than an inline shell one-liner, so
    there's no fragile nested-quoting to get wrong."""
    import json
    import tempfile

    if not promoted:
        return

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(_REMOTE_MARK_SCRIPT)
        local_script = f.name

    remote_script = "~/apps/fridge-recipes/_mark_promoted.py"
    scp = subprocess.run(
        ["gcloud", "compute", "scp", f"--zone={zone}", local_script, f"{vm}:{remote_script}"],
        capture_output=True, text=True,
    )
    if scp.returncode != 0:
        print(f"push-marks upload failed: {scp.stderr.strip()}")
        return

    payload = json.dumps(promoted)
    result = subprocess.run(
        ["gcloud", "compute", "ssh", vm, f"--zone={zone}", "--",
         "cd ~/apps/fridge-recipes && python3 _mark_promoted.py", f"'{payload}'"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"push-marks failed: {result.stderr.strip()}")
    else:
        print(f"marked {len(promoted)} recipe(s) promoted on the VM: {result.stdout.strip()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-db", type=Path, default=DEFAULT_APP_DB_MIRROR)
    parser.add_argument("--recipes-db", type=Path, default=RECIPES_DB_PATH)
    parser.add_argument("--faiss-index", type=Path, default=DEFAULT_FAISS_PATH)
    parser.add_argument("--usda-dir", type=Path, default=None)
    parser.add_argument("--skip-embeddings", action="store_true")
    parser.add_argument("--skip-nutrition", action="store_true")
    parser.add_argument(
        "--marks-only", action="store_true",
        help="skip promotion entirely; just push already-promoted ids from a "
             "prior run to the VM. Run this AFTER `push_laptop` has actually "
             "shipped recipes.db, so the VM has the ids being referenced.",
    )
    parser.add_argument("--vm", default="a-bot")
    parser.add_argument("--zone", default="us-central1-a")
    args = parser.parse_args()

    if not args.app_db.exists():
        print(f"no app.db at {args.app_db} -- run `python env_sync.py pull_laptop fridge-recipes` first")
        raise SystemExit(1)

    if args.marks_only:
        pairs = already_promoted_locally(args.app_db)
        push_marks_to_vm(pairs, args.vm, args.zone)
        return

    promoted = promote_all(args.app_db, args.recipes_db)
    print(f"promoted {len(promoted)} recipe(s)")

    if promoted and not args.skip_nutrition:
        from etl.nutrition_backfill import RAW_USDA_DIR
        backfill_nutrition(args.usda_dir or RAW_USDA_DIR, args.recipes_db)

    if promoted and not args.skip_embeddings:
        extend_embeddings(promoted, args.recipes_db, args.faiss_index)

    if promoted:
        print(
            "\nNext: `python env_sync.py push_laptop fridge-recipes` to ship "
            "recipes.db + the embeddings index, THEN "
            "`python -m etl.promote_custom_recipes --marks-only` to mark "
            "these promoted on the live VM."
        )


if __name__ == "__main__":
    main()
