"""CRUD over app.db's inventory table.

Mirrors food/mealprep_db.py's function shape (add_item/remove_item/eat_item/
get_fridge) but against the richer schema (location, expires_at, state)
introduced by the original fridge_recipes plan. Unlike mealprep_db.py,
`qty_text` is free text (not a numeric column), since ingredient_parser-style
quantity math on inventory isn't in scope -- "I ate the chicken" removes the
matching row(s) rather than decrementing a gram count.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.config import APP_DB_PATH
from core.db import connect
from core.normalize import canonicalize


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shelf_life_days(
    canonical_ingredient: str, location: str, db_path: Path
) -> int | None:
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT days FROM shelf_life WHERE canonical_ingredient = ? AND location = ?",
            (canonical_ingredient, location),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def add_item(
    name: str,
    qty_text: str | None = None,
    location: str = "fridge",
    source: str = "manual",
    db_path: Path = APP_DB_PATH,
) -> int:
    """Add an item to inventory, returning the new row id.

    `name` is canonicalized so it matches recipe_ingredients' naming (bot
    free-text like "Chicken Thighs" becomes "chicken thighs").
    """
    canonical = canonicalize(name)
    now = _now()
    days = _shelf_life_days(canonical, location, db_path)
    expires_at = (
        (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        if days is not None
        else None
    )
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO inventory (canonical_ingredient, qty_text, location, "
            "added_at, expires_at, state, source) VALUES (?, ?, ?, ?, ?, 'fresh', ?)",
            (canonical, qty_text, location, now, expires_at, source),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_inventory(
    location: str | None = None, db_path: Path = APP_DB_PATH
) -> list[dict]:
    conn = connect(db_path)
    try:
        if location:
            rows = conn.execute(
                "SELECT id, canonical_ingredient, qty_text, location, added_at, "
                "expires_at, state, source FROM inventory WHERE location = ? "
                "ORDER BY canonical_ingredient",
                (location,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, canonical_ingredient, qty_text, location, added_at, "
                "expires_at, state, source FROM inventory ORDER BY canonical_ingredient"
            ).fetchall()
    finally:
        conn.close()
    cols = [
        "id", "canonical_ingredient", "qty_text", "location", "added_at",
        "expires_at", "state", "source",
    ]
    return [dict(zip(cols, row)) for row in rows]


def find_items(name: str, db_path: Path = APP_DB_PATH) -> list[dict]:
    canonical = canonicalize(name)
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, canonical_ingredient, qty_text, location, added_at, "
            "expires_at, state, source FROM inventory WHERE canonical_ingredient = ?",
            (canonical,),
        ).fetchall()
    finally:
        conn.close()
    cols = [
        "id", "canonical_ingredient", "qty_text", "location", "added_at",
        "expires_at", "state", "source",
    ]
    return [dict(zip(cols, row)) for row in rows]


def _remove_all_matching(name: str, db_path: Path) -> list[dict]:
    items = find_items(name, db_path)
    if not items:
        return []
    conn = connect(db_path)
    try:
        conn.executemany(
            "DELETE FROM inventory WHERE id = ?", [(i["id"],) for i in items]
        )
        conn.commit()
    finally:
        conn.close()
    return items


def remove_item(name: str, db_path: Path = APP_DB_PATH) -> list[dict]:
    """Remove (discard, not eaten) every inventory row matching `name`.
    Returns the removed rows for logging."""
    return _remove_all_matching(name, db_path)


def eat_item(name: str, db_path: Path = APP_DB_PATH) -> list[dict]:
    """Remove every inventory row matching `name` because it was eaten.
    Returns the removed rows so the caller can log nutrition for them."""
    return _remove_all_matching(name, db_path)


def update_state(item_id: int, state: str, db_path: Path = APP_DB_PATH) -> None:
    conn = connect(db_path)
    try:
        conn.execute("UPDATE inventory SET state = ? WHERE id = ?", (state, item_id))
        conn.commit()
    finally:
        conn.close()
