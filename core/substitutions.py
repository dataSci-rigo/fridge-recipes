"""Ingredient substitution lookup: what else can I use instead of X?

Three sources, cheapest first (same cascade shape as core/nutrition.py and
food/gi.py): a curated YAML seed, a DB table that grows from recipe
ingestion ("butter, or margarine") and from caching live Claude answers, and
the live Claude fallback itself for anything not seen before.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import yaml

from core.config import APP_DB_PATH
from core.db import connect
from core.normalize import canonicalize

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = PROJECT_ROOT / "etl" / "substitutions.yaml"


@lru_cache(maxsize=1)
def _load_seed() -> frozenset[tuple[str, str]]:
    with open(SEED_PATH) as f:
        pairs = yaml.safe_load(f) or []
    return frozenset(
        (canonicalize(a), canonicalize(b)) for a, b in pairs
    )


def _db_pairs(name: str, db_path: Path) -> set[str]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT canonical_a, canonical_b FROM substitutions "
            "WHERE canonical_a = ? OR canonical_b = ?",
            (name, name),
        ).fetchall()
    finally:
        conn.close()
    out = set()
    for a, b in rows:
        out.add(b if a == name else a)
    return out


def get_substitutes(name: str, db_path: Path = APP_DB_PATH) -> set[str]:
    """Known substitutes for a canonical ingredient name, from the seed
    table and anything discovered via ingestion or a prior Claude lookup.
    Does not hit the network -- see lookup_or_ask() for the live fallback."""
    canonical = canonicalize(name)
    out = set()
    for a, b in _load_seed():
        if a == canonical:
            out.add(b)
        elif b == canonical:
            out.add(a)
    out |= _db_pairs(canonical, db_path)
    out.discard(canonical)
    return out


def add_substitution(
    a: str, b: str, source: str, db_path: Path = APP_DB_PATH
) -> None:
    """Record a discovered substitution pair (ingestion or a cached Claude
    answer). source in {'ingested', 'claude'} -- 'seed' is YAML-only."""
    ca, cb = canonicalize(a), canonicalize(b)
    if ca == cb:
        return
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO substitutions "
            "(canonical_a, canonical_b, source, created_at) VALUES (?, ?, ?, ?)",
            (ca, cb, source, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def _claude_suggest(name: str, have_ingredients: set[str]) -> list[str]:
    """Live fallback: ask Claude for substitutes, nudged toward what's
    already in `have_ingredients` if any of those would work."""
    import json
    import re

    import anthropic

    have_str = ", ".join(sorted(have_ingredients)) if have_ingredients else "(nothing specific)"
    system = (
        "You suggest cooking ingredient substitutes. Given an ingredient and "
        "a list of what the user currently has on hand, return ONLY a JSON "
        'array of up to 3 short lowercase substitute ingredient names, e.g. '
        '["margarine", "vegetable oil"]. Prefer substitutes from what the '
        "user has on hand when reasonable. No prose, no markdown."
    )
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        system=system,
        messages=[{"role": "user", "content": f"Substitutes for: {name}\nHave on hand: {have_str}"}],
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


def lookup_or_ask(
    name: str,
    have_ingredients: set[str] | None = None,
    db_path: Path = APP_DB_PATH,
    ask_fn=_claude_suggest,
) -> set[str]:
    """get_substitutes() first; only calls Claude (and caches the result)
    when nothing is already known. `ask_fn` is injectable for testing."""
    known = get_substitutes(name, db_path)
    if known:
        return known

    suggestions = ask_fn(name, have_ingredients or set())
    for sub in suggestions:
        add_substitution(name, sub, source="claude", db_path=db_path)
    return {canonicalize(s) for s in suggestions}
