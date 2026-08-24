"""Open (unauthenticated) endpoints: cached image serving and ingredient
autocomplete. Nothing here exposes user data -- corpus recipe photos,
Wikimedia ingredient photos, and canonical ingredient names only.

Image routes are per-item so pages render immediately and each <img> tag
fetches (and on first view, downloads+caches) its own photo lazily.
"""
from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import FileResponse, HTMLResponse

from core.config import RECIPES_DB_PATH
from core.db import connect
from core.images import get_ingredient_image, get_recipe_image

router = APIRouter()

_PLACEHOLDER_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect width="64" height="64" fill="#80808022"/>'
    '<text x="32" y="38" font-size="28" text-anchor="middle">🍽️</text></svg>'
)


def _placeholder() -> Response:
    return Response(
        content=_PLACEHOLDER_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


_CACHE_HEADERS = {"Cache-Control": "public, max-age=604800"}


@router.get("/img/recipe/{recipe_id}")
def recipe_image(recipe_id: int):
    path = get_recipe_image(recipe_id)
    if path is None:
        return _placeholder()
    return FileResponse(path, headers=_CACHE_HEADERS)


@router.get("/img/ingredient/{name}")
def ingredient_image(name: str):
    path = get_ingredient_image(name)
    if path is None:
        return _placeholder()
    return FileResponse(path, headers=_CACHE_HEADERS)


@router.get("/api/ingredients/autocomplete", response_class=HTMLResponse)
def autocomplete(q: str = "", target: str = "has", limit: int = 8):
    """HTMX fragment: matching canonical ingredient names with thumbnails.
    `target` names the input id the picked value should be written into."""
    q = q.strip().lower()
    if len(q) < 2:
        return HTMLResponse("")
    conn = connect(RECIPES_DB_PATH)
    try:
        rows = conn.execute(
            "SELECT canonical_name FROM ingredients "
            "WHERE canonical_name LIKE ? ORDER BY canonical_name LIMIT ?",
            (f"{q}%", limit),
        ).fetchall()
        if len(rows) < limit:
            extra = conn.execute(
                "SELECT canonical_name FROM ingredients "
                "WHERE canonical_name LIKE ? AND canonical_name NOT LIKE ? "
                "ORDER BY canonical_name LIMIT ?",
                (f"% {q}%", f"{q}%", limit - len(rows)),
            ).fetchall()
            rows += extra
    finally:
        conn.close()

    items = "".join(
        f'<div class="ac-item" data-name="{name}" '
        f"onclick=\"acPick('{target}', this.dataset.name)\">"
        f'<img src="/img/ingredient/{name}" loading="lazy" alt="">'
        f"<span>{name}</span></div>"
        for (name,) in rows
    )
    return HTMLResponse(items)
