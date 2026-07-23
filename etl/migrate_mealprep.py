"""One-off migration: seed shelf_life defaults, then copy food/data/mealprep.db's
`fridge` rows into fridge_recipes' app.db.inventory (canonicalized), as part
of retiring food/mealprep_db.py + food/mealprep_handlers.py in favor of
fridge_recipes owning inventory.

Safe to re-run: shelf_life seeding is an upsert, and mealprep rows are only
copied if app.db.inventory doesn't already have a source='mealprep_migration'
row for that item.

Usage: python -m etl.migrate_mealprep [--mealprep-db PATH] [--app-db PATH]
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from core.config import APP_DB_PATH
from core.db import connect, init_app_db
from core.normalize import canonicalize

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SHELF_LIFE_PATH = PROJECT_ROOT / "etl" / "shelf_life.yaml"
DEFAULT_MEALPREP_DB = Path.home() / "Documents" / "food" / "data" / "mealprep.db"

_MIGRATION_SOURCE = "mealprep_migration"


def seed_shelf_life(app_db: Path) -> int:
    with open(SHELF_LIFE_PATH) as f:
        entries = yaml.safe_load(f) or []
    conn = connect(app_db)
    try:
        for name, location, days in entries:
            conn.execute(
                "INSERT INTO shelf_life (canonical_ingredient, location, days) "
                "VALUES (?, ?, ?) ON CONFLICT(canonical_ingredient, location) "
                "DO UPDATE SET days = excluded.days",
                (canonicalize(name), location, days),
            )
        conn.commit()
    finally:
        conn.close()
    return len(entries)


def _shelf_life_days(conn: sqlite3.Connection, name: str, location: str) -> int | None:
    row = conn.execute(
        "SELECT days FROM shelf_life WHERE canonical_ingredient = ? AND location = ?",
        (name, location),
    ).fetchone()
    return row[0] if row else None


def migrate_fridge_rows(mealprep_db: Path, app_db: Path) -> int:
    if not mealprep_db.exists():
        print(f"no mealprep.db found at {mealprep_db}, nothing to migrate")
        return 0

    src = sqlite3.connect(mealprep_db)
    rows = src.execute(
        "SELECT item_name, quantity, unit, added_at, updated_at FROM fridge"
    ).fetchall()
    src.close()

    conn = connect(app_db)
    already = {
        r[0]
        for r in conn.execute(
            "SELECT canonical_ingredient FROM inventory WHERE source = ?",
            (_MIGRATION_SOURCE,),
        )
    }

    migrated = 0
    for item_name, quantity, unit, added_at, _updated_at in rows:
        canonical = canonicalize(item_name)
        if canonical in already:
            continue
        qty_text = f"{quantity:.0f} {unit}" if quantity is not None else None
        days = _shelf_life_days(conn, canonical, "fridge")
        expires_at = (
            (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
            if days is not None
            else None
        )
        conn.execute(
            "INSERT INTO inventory (canonical_ingredient, qty_text, location, "
            "added_at, expires_at, state, source) VALUES (?, ?, 'fridge', ?, ?, 'fresh', ?)",
            (canonical, qty_text, added_at or datetime.now(timezone.utc).isoformat(),
             expires_at, _MIGRATION_SOURCE),
        )
        migrated += 1

    conn.commit()
    conn.close()
    return migrated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mealprep-db", type=Path, default=DEFAULT_MEALPREP_DB)
    parser.add_argument("--app-db", type=Path, default=APP_DB_PATH)
    args = parser.parse_args()

    init_app_db(args.app_db)
    n_seeded = seed_shelf_life(args.app_db)
    print(f"seeded {n_seeded} shelf_life defaults")

    n_migrated = migrate_fridge_rows(args.mealprep_db, args.app_db)
    print(f"migrated {n_migrated} fridge item(s) from {args.mealprep_db} -> {args.app_db}")


if __name__ == "__main__":
    main()
