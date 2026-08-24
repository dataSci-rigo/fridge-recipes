"""Per-mode inventory/selection stores for the web app.

Two modes, one interface:
- PersonalStore -- the real inventory (core.inventory / app.db), wizard
  selection state parked in search_prefs as JSON.
- DemoStore -- a synthetic fridge per browser session in the demo_sessions /
  demo_inventory tables, seeded from etl/demo_items.yaml. Demo interactions
  never touch the real inventory table.

The setup wizard offers the same etl/demo_items.yaml item set in both modes;
"committing" routes to the mode's own storage.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import yaml

from core.config import APP_DB_PATH
from core.db import connect
from core import inventory as core_inventory

DEMO_ITEMS_PATH = Path(__file__).resolve().parent.parent / "etl" / "demo_items.yaml"
DEMO_SESSION_MAX_AGE_DAYS = 7
PAGE_SIZE = 20

_SELECTION_PREF_KEY = "web_setup_selection"


@lru_cache(maxsize=1)
def wizard_items() -> tuple[tuple[str, str], ...]:
    """[(canonical_name, location), ...] in stable YAML order."""
    data = yaml.safe_load(DEMO_ITEMS_PATH.read_text())
    return tuple(
        (name, location)
        for location in ("fridge", "pantry")
        for name in data.get(location, [])
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PersonalStore:
    mode = "personal"
    prefix = ""

    def __init__(self, db_path: Path = APP_DB_PATH):
        self.db_path = db_path

    # -- wizard selection state (search_prefs JSON: {name: bool}) --
    def _read_selection(self) -> dict[str, bool]:
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT value FROM search_prefs WHERE key = ?", (_SELECTION_PREF_KEY,)
            ).fetchone()
        finally:
            conn.close()
        return json.loads(row[0]) if row and row[0] else {}

    def _write_selection(self, sel: dict[str, bool]) -> None:
        conn = connect(self.db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO search_prefs (key, value) VALUES (?, ?)",
                (_SELECTION_PREF_KEY, json.dumps(sel)),
            )
            conn.commit()
        finally:
            conn.close()

    def selection(self) -> dict[str, bool]:
        sel = self._read_selection()
        return {name: bool(sel.get(name, False)) for name, _loc in wizard_items()}

    def update_selection(self, page_names: list[str], checked: set[str]) -> None:
        sel = self._read_selection()
        for name in page_names:
            sel[name] = name in checked
        self._write_selection(sel)

    def set_all(self, value: bool) -> None:
        self._write_selection({name: value for name, _loc in wizard_items()})

    def commit_selected(self) -> int:
        have = self.inventory_names()
        selection = self.selection()
        count = 0
        for name, location in wizard_items():
            if selection.get(name) and name not in have:
                core_inventory.add_item(
                    name, location=location, source="manual", db_path=self.db_path
                )
                count += 1
        self._write_selection({})
        return count

    # -- inventory --
    def inventory(self) -> list[dict]:
        return core_inventory.list_inventory(db_path=self.db_path)

    def inventory_names(self) -> set[str]:
        return {i["canonical_ingredient"] for i in self.inventory()}

    def remove(self, name: str) -> None:
        core_inventory.remove_item(name, db_path=self.db_path)

    def add(self, name: str, location: str = "fridge") -> None:
        core_inventory.add_item(name, location=location, db_path=self.db_path)


class DemoStore:
    mode = "demo"
    prefix = "/demo"

    def __init__(self, session_id: str, db_path: Path = APP_DB_PATH):
        self.session_id = session_id
        self.db_path = db_path

    # -- session lifecycle --
    @classmethod
    def open_session(cls, session_id: str | None, db_path: Path = APP_DB_PATH) -> "DemoStore":
        """Returns a store for an existing session, or seeds a fresh one
        (new uuid) when the cookie is absent/stale. Sweeps old sessions."""
        conn = connect(db_path)
        try:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=DEMO_SESSION_MAX_AGE_DAYS)
            ).isoformat()
            conn.execute("DELETE FROM demo_sessions WHERE last_seen_at < ?", (cutoff,))

            row = None
            if session_id:
                row = conn.execute(
                    "SELECT session_id FROM demo_sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            if row is None:
                session_id = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO demo_sessions (session_id, created_at, last_seen_at) "
                    "VALUES (?, ?, ?)",
                    (session_id, _now(), _now()),
                )
                conn.executemany(
                    "INSERT INTO demo_inventory "
                    "(session_id, canonical_ingredient, location, selected, committed) "
                    "VALUES (?, ?, ?, 0, 0)",
                    [(session_id, name, loc) for name, loc in wizard_items()],
                )
            else:
                conn.execute(
                    "UPDATE demo_sessions SET last_seen_at = ? WHERE session_id = ?",
                    (_now(), session_id),
                )
            conn.commit()
        finally:
            conn.close()
        return cls(session_id, db_path)

    def setup_done(self) -> bool:
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT setup_done FROM demo_sessions WHERE session_id = ?",
                (self.session_id,),
            ).fetchone()
        finally:
            conn.close()
        return bool(row and row[0])

    # -- wizard selection state --
    def selection(self) -> dict[str, bool]:
        conn = connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT canonical_ingredient, selected FROM demo_inventory "
                "WHERE session_id = ?",
                (self.session_id,),
            ).fetchall()
        finally:
            conn.close()
        by_name = {name: bool(sel) for name, sel in rows}
        return {name: by_name.get(name, False) for name, _loc in wizard_items()}

    def update_selection(self, page_names: list[str], checked: set[str]) -> None:
        conn = connect(self.db_path)
        try:
            conn.executemany(
                "UPDATE demo_inventory SET selected = ? "
                "WHERE session_id = ? AND canonical_ingredient = ?",
                [(int(n in checked), self.session_id, n) for n in page_names],
            )
            conn.commit()
        finally:
            conn.close()

    def set_all(self, value: bool) -> None:
        conn = connect(self.db_path)
        try:
            conn.execute(
                "UPDATE demo_inventory SET selected = ? WHERE session_id = ?",
                (int(value), self.session_id),
            )
            conn.commit()
        finally:
            conn.close()

    def commit_selected(self) -> int:
        conn = connect(self.db_path)
        try:
            conn.execute(
                "UPDATE demo_inventory SET committed = selected WHERE session_id = ?",
                (self.session_id,),
            )
            conn.execute(
                "UPDATE demo_sessions SET setup_done = 1 WHERE session_id = ?",
                (self.session_id,),
            )
            conn.commit()
            return conn.execute(
                "SELECT COUNT(*) FROM demo_inventory "
                "WHERE session_id = ? AND committed = 1",
                (self.session_id,),
            ).fetchone()[0]
        finally:
            conn.close()

    # -- inventory (committed demo items) --
    def inventory(self) -> list[dict]:
        conn = connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT canonical_ingredient, location FROM demo_inventory "
                "WHERE session_id = ? AND committed = 1 ORDER BY location, canonical_ingredient",
                (self.session_id,),
            ).fetchall()
        finally:
            conn.close()
        return [
            {"canonical_ingredient": n, "location": loc, "qty_text": None, "state": "fresh"}
            for n, loc in rows
        ]

    def inventory_names(self) -> set[str]:
        return {i["canonical_ingredient"] for i in self.inventory()}

    def remove(self, name: str) -> None:
        conn = connect(self.db_path)
        try:
            conn.execute(
                "UPDATE demo_inventory SET committed = 0, selected = 0 "
                "WHERE session_id = ? AND canonical_ingredient = ?",
                (self.session_id, name),
            )
            conn.commit()
        finally:
            conn.close()

    def add(self, name: str, location: str = "fridge") -> None:
        conn = connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO demo_inventory "
                "(session_id, canonical_ingredient, location, selected, committed) "
                "VALUES (?, ?, ?, 1, 1) "
                "ON CONFLICT(session_id, canonical_ingredient) "
                "DO UPDATE SET committed = 1, selected = 1",
                (self.session_id, name, location),
            )
            conn.commit()
        finally:
            conn.close()
