"""Image fetching + on-disk caching for recipe dish photos and ingredient
photos, shared by the web app (and any future bot use).

Two sources, both free:
- Recipe photos: `recipes.image_url` came with the raw open-recipe-data dump
  (the original cooking site's photo). Fetched once on first view, cached to
  data/images/recipes/<id>.<ext>.
- Ingredient photos: Wikimedia Commons search API (openly licensed, no key;
  we identify ourselves with a real User-Agent per API etiquette, same as
  food/nutrition.py does for Open Food Facts). Cached to
  data/images/ingredients/<slug>.<ext> with a row in ingredient_images.

Failures are cached as PERMANENT misses ('unavailable' sentinel in the DB) --
this data is years old and dead recipe links are expected; a known-dead URL
must not be re-fetched on every page view.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import requests

from core.config import IMAGES_DIR, RECIPES_DB_PATH
from core.db import connect

USER_AGENT = "fridge-recipes/1.0 (personal recipe app; rlunaorozco@berkeley.edu)"
FETCH_TIMEOUT = 10
UNAVAILABLE = "unavailable"

_EXT_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

COMMONS_API = "https://commons.wikimedia.org/w/api.php"


def _ext_for(url: str, content_type: str | None) -> str:
    if content_type:
        ext = _EXT_BY_CONTENT_TYPE.get(content_type.split(";")[0].strip().lower())
        if ext:
            return ext
    m = re.search(r"\.(jpe?g|png|gif|webp)(?:\?|$)", url, re.IGNORECASE)
    if m:
        ext = m.group(1).lower()
        return ".jpg" if ext in ("jpg", "jpeg") else f".{ext}"
    return ".jpg"


def _download(url: str, dest_stem: Path) -> Path | None:
    """GET url -> dest_stem + inferred extension. None on any failure."""
    try:
        resp = requests.get(
            url, timeout=FETCH_TIMEOUT, headers={"User-Agent": USER_AGENT}
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None
    content_type = resp.headers.get("Content-Type", "")
    if not content_type.startswith("image/") or not resp.content:
        return None
    dest = dest_stem.with_suffix(_ext_for(url, content_type))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return dest


def get_recipe_image(
    recipe_id: int,
    db_path: Path = RECIPES_DB_PATH,
    images_dir: Path = IMAGES_DIR,
) -> Path | None:
    """Cached dish photo for a corpus recipe, fetching from the source site's
    image_url on first request. None = no URL, or a (now permanently cached)
    dead link."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT image_url, image_cached_path FROM recipes WHERE id = ?",
            (recipe_id,),
        ).fetchone()
        if row is None:
            return None
        image_url, cached = row
        if cached == UNAVAILABLE:
            return None
        if cached:
            p = Path(cached)
            if p.exists():
                return p
            # cache file vanished (e.g. images dir cleaned) -- refetch below
        if not image_url:
            return None

        dest = _download(image_url, images_dir / "recipes" / str(recipe_id))
        conn.execute(
            "UPDATE recipes SET image_cached_path = ? WHERE id = ?",
            (str(dest) if dest else UNAVAILABLE, recipe_id),
        )
        conn.commit()
        return dest
    finally:
        conn.close()


def _commons_search(name: str) -> str | None:
    """First usable image URL from a Wikimedia Commons search, or None."""
    try:
        resp = requests.get(
            COMMONS_API,
            params={
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": f"{name} food",
                "gsrnamespace": 6,  # File:
                "gsrlimit": 5,
                "prop": "imageinfo",
                "iiprop": "url|mime",
                # Thumbnail-sized: full Commons originals can be 20MB+ scans.
                "iiurlwidth": 480,
            },
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    pages = (data.get("query") or {}).get("pages") or {}
    # generator=search results carry an "index" ranking key
    for page in sorted(pages.values(), key=lambda p: p.get("index", 999)):
        for info in page.get("imageinfo") or []:
            if (info.get("mime") or "").startswith("image/") and info.get("mime") not in (
                "image/svg+xml",
                "image/tiff",
            ):
                url = info.get("thumburl") or info.get("url")
                if url:
                    return url
    return None


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "item"


def get_ingredient_image(
    canonical_name: str,
    db_path: Path = RECIPES_DB_PATH,
    images_dir: Path = IMAGES_DIR,
    search_fn=_commons_search,
) -> Path | None:
    """Cached photo for a canonical ingredient name, querying Wikimedia
    Commons on first request. None = no usable Commons result (permanently
    cached as a miss)."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT local_path FROM ingredient_images WHERE canonical_name = ?",
            (canonical_name,),
        ).fetchone()
        if row is not None:
            if row[0] == UNAVAILABLE or not row[0]:
                return None
            p = Path(row[0])
            if p.exists():
                return p
            conn.execute(
                "DELETE FROM ingredient_images WHERE canonical_name = ?",
                (canonical_name,),
            )
            conn.commit()

        url = search_fn(canonical_name)
        dest = (
            _download(url, images_dir / "ingredients" / _slug(canonical_name))
            if url
            else None
        )
        conn.execute(
            "INSERT OR REPLACE INTO ingredient_images "
            "(canonical_name, image_url, local_path, source, fetched_at) "
            "VALUES (?, ?, ?, 'wikimedia_commons', ?)",
            (
                canonical_name,
                url,
                str(dest) if dest else UNAVAILABLE,
                time.strftime("%Y-%m-%dT%H:%M:%S"),
            ),
        )
        conn.commit()
        return dest
    finally:
        conn.close()
