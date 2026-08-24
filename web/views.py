"""Page routes, built once per mode by make_router(demo=...).

Personal and demo modes render the same templates against their own store
(web/state.py); the only behavioral differences are the URL prefix, auth
(applied by web/main.py), and add-your-own being read-only in demo mode
(extraction runs, nothing persists -- demo must never write real app data).
"""
from __future__ import annotations

import math
from pathlib import Path

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from core.config import APP_DB_PATH, RECIPES_DB_PATH
from core.custom_recipes import find_custom
from core.models import InventoryItem, RankFilters
from core.normalize import canonicalize
from core.rank import rank_recipes
from core.recipe_detail import get_recipe_detail
from core.recipe_ingest import extract_from_image, extract_from_text, save_extracted
from core.search import load_candidate_recipes, search_recipes
from core.substitutions import get_substitutes
from core import custom_recipes as custom_recipes_db
from web.state import PAGE_SIZE, wizard_items

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

MAX_INGREDIENT_THUMBS = 12  # busier recipes get a plain text list

# --- browse picture-buttons -------------------------------------------------
# Cuisine/category/flavor tiles get real photos through the same Wikimedia
# cache as ingredients (the key is just a Commons search seed -- "<key> food");
# time/calorie tiles are emoji since no photo says "under 30 minutes".

CATEGORY_TILES = [
    ("breakfast", "breakfast"), ("dessert", "dessert"), ("soup", "soup"),
    ("salad", "salad"), ("bread", "bread"), ("appetizer", "appetizer"),
    ("beverage", "beverage"), ("sauce_or_condiment", "sauce"),
]

CUISINE_TILES = [
    ("italian", "italian"), ("mexican", "mexican"), ("chinese", "chinese"),
    ("indian", "indian"), ("thai", "thai"), ("japanese", "japanese"),
    ("french", "french"), ("greek", "greek"), ("mediterranean", "mediterranean"),
    ("cajun", "cajun"), ("american", "american"),
]

# flavor -> ingredient names, matched with ANY-of semantics
FLAVORS: dict[str, set[str]] = {
    "spicy": {"chili powder", "jalapeno", "cayenne", "cayenne pepper",
              "sriracha", "red pepper flake", "chipotle"},
    "sweet": {"sugar", "brown sugar", "honey", "maple syrup", "chocolate",
              "chocolate chip"},
    "citrusy": {"lemon", "lime", "orange", "lemon juice", "lime juice"},
    "garlicky": {"garlic"},
    "herby": {"basil", "cilantro", "parsley", "rosemary", "thyme", "oregano",
              "dill", "mint"},
    "cheesy": {"cheese", "cheddar", "parmesan", "mozzarella", "cream cheese",
               "feta"},
}
# the flavor word itself seeds the Commons image search ("spicy food")
FLAVOR_TILES = [(name, name) for name in FLAVORS]

TIME_TILES = [("15 min", 15, "⚡"), ("30 min", 30, "⏱️"), ("1 hour", 60, "🕐")]
CALORIE_TILES = [("light · <800", 800, "🥬"), ("medium · <1500", 1500, "🍽️"),
                 ("hearty · <3000", 3000, "🍖")]


def _render(request: Request, store, name: str, **ctx) -> HTMLResponse:
    ctx.update({"request": request, "prefix": store.prefix, "mode": store.mode})
    return templates.TemplateResponse(request, name, ctx)


def _subs_fn():
    return lambda name: get_substitutes(name, db_path=APP_DB_PATH)


def _cook_results(store, top_n: int = 12) -> list[dict]:
    items = store.inventory()
    if not items:
        return []
    inv = [InventoryItem(i["canonical_ingredient"], state=i["state"]) for i in items]
    names = {i.canonical_ingredient for i in inv}
    subs_fn = _subs_fn()

    ranked = [
        ("corpus", r)
        for r in rank_recipes(
            load_candidate_recipes(names, db_path=RECIPES_DB_PATH),
            inv, filters=RankFilters(), top_n=top_n, get_substitutes_fn=subs_fn,
        )
    ]
    if store.mode == "personal":
        ranked += [
            ("custom", r)
            for r in custom_recipes_db.rank_custom(
                inv, top_n=top_n, get_substitutes_fn=subs_fn, db_path=APP_DB_PATH
            )
        ]
    ranked.sort(key=lambda pair: pair[1].score, reverse=True)
    return [
        {
            "ref": f"c{r.recipe.id}" if source == "custom" else str(r.recipe.id),
            "title": r.recipe.title,
            "active_min": r.recipe.active_min,
            "total_min": r.recipe.total_min,
            "explanation": r.explanation,
            "has_image": source == "corpus",
        }
        for source, r in ranked[:top_n]
    ]


def _search_results(store, query: str | None, filters: dict, limit: int = 24) -> list[dict]:
    corpus = search_recipes(
        query=query or None,
        ingredients=filters.get("ingredients"),
        max_active_min=filters.get("max_active_min"),
        max_total_min=filters.get("max_total_min"),
        category=filters.get("category"),
        cuisine=filters.get("cuisine"),
        max_calories=filters.get("max_calories"),
        any_ingredients=filters.get("any_ingredients"),
        limit=limit,
        db_path=RECIPES_DB_PATH,
    )
    results = [
        {"ref": str(m["id"]), "title": m["title"], "has_image": True} for m in corpus
    ]
    if store.mode == "personal" and query and len(results) < limit:
        custom = find_custom(query, limit=limit - len(results), db_path=APP_DB_PATH)
        results += [
            {"ref": f"c{m['id']}", "title": m["title"], "has_image": False}
            for m in custom
        ]
    return results


def make_router(demo: bool, get_store) -> APIRouter:
    from fastapi import Depends

    router = APIRouter()
    Store = Depends(get_store)

    # -- landing -----------------------------------------------------------
    def landing(request: Request, store=Store):
        done = store.setup_done() if demo else bool(store.inventory_names())
        target = "search" if done else "setup"
        return RedirectResponse(f"{store.prefix}/{target}")

    if demo:  # bare GET /demo (no trailing slash) must land too
        router.get("", response_class=HTMLResponse)(landing)
    router.get("/", response_class=HTMLResponse)(landing)

    # -- setup wizard ------------------------------------------------------
    @router.get("/setup", response_class=HTMLResponse)
    def setup(request: Request, store=Store, page: int = 1):
        items = wizard_items()
        selection = store.selection()
        pages = max(1, math.ceil(len(items) / PAGE_SIZE))
        page = min(max(page, 1), pages)
        page_items = items[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
        return _render(
            request, store, "setup.html",
            items=[
                {"name": n, "location": loc, "selected": selection.get(n, False)}
                for n, loc in page_items
            ],
            page=page, pages=pages,
            selected_count=sum(selection.values()),
            total_count=len(items),
        )

    @router.post("/setup", response_class=HTMLResponse)
    async def setup_post(request: Request, store=Store):
        form = await request.form()
        page = int(form.get("page", 1))
        action = form.get("action", "save")
        checked = set(form.getlist("item"))
        page_names = form.getlist("page_item")

        # Persist this page's checkboxes first so no click loses state
        # (select/unselect-all overwrite it globally right after).
        store.update_selection(page_names, checked)

        if action == "select_all":
            store.set_all(True)
        elif action == "unselect_all":
            store.set_all(False)
        elif action == "skip":  # skip = select all, then straight to commit
            store.set_all(True)
            store.commit_selected()
            return RedirectResponse(f"{store.prefix}/review", status_code=303)
        elif action == "done":
            store.commit_selected()
            return RedirectResponse(f"{store.prefix}/review", status_code=303)
        elif action == "next":
            page += 1
        elif action == "prev":
            page -= 1
        return RedirectResponse(f"{store.prefix}/setup?page={page}", status_code=303)

    # -- review (post-commit: remove what you don't actually have) ---------
    @router.get("/review", response_class=HTMLResponse)
    def review(request: Request, store=Store):
        return _render(request, store, "review.html", inventory=store.inventory())

    @router.post("/review/remove", response_class=HTMLResponse)
    def review_remove(request: Request, store=Store, name: str = Form(...)):
        store.remove(name)
        return RedirectResponse(f"{store.prefix}/review", status_code=303)

    # -- inventory add (autocomplete form on search/review pages) ----------
    @router.post("/inventory/add", response_class=HTMLResponse)
    def inventory_add(
        request: Request, store=Store,
        name: str = Form(...), location: str = Form("fridge"),
        back: str = Form("search"),
    ):
        canonical = canonicalize(name)
        if canonical:
            store.add(canonical, location=location if location in ("fridge", "pantry") else "fridge")
        return RedirectResponse(f"{store.prefix}/{back}", status_code=303)

    # -- search ------------------------------------------------------------
    @router.get("/search", response_class=HTMLResponse)
    def search(
        request: Request, store=Store,
        q: str = "", category: str = "", cuisine: str = "", flavor: str = "",
        has: str = "",
        max_active: int | None = None, max_total: int | None = None,
        max_cal: int | None = None,
        cook: int = 0,
    ):
        results = None
        if cook:
            results = _cook_results(store)
        elif q or category or cuisine or flavor or has or max_active or max_total or max_cal:
            filters: dict = {}
            if category:
                filters["category"] = category
            if cuisine:
                filters["cuisine"] = cuisine
            if flavor in FLAVORS:
                filters["any_ingredients"] = FLAVORS[flavor]
            if max_active:
                filters["max_active_min"] = max_active
            if max_total:
                filters["max_total_min"] = max_total
            if max_cal:
                filters["max_calories"] = max_cal
            names = {canonicalize(n) for n in has.split(",") if n.strip()}
            names.discard("")
            if names:
                filters["ingredients"] = names
            results = _search_results(store, q.strip(), filters)
        return _render(
            request, store, "search.html",
            results=results, cook=bool(cook),
            q=q, category=category, cuisine=cuisine, flavor=flavor, has=has,
            max_active=max_active or "", max_total=max_total or "",
            max_cal=max_cal or "",
            category_tiles=CATEGORY_TILES, cuisine_tiles=CUISINE_TILES,
            flavor_tiles=FLAVOR_TILES, time_tiles=TIME_TILES,
            calorie_tiles=CALORIE_TILES,
            inventory_count=len(store.inventory_names()),
        )

    # -- recipe detail -----------------------------------------------------
    @router.get("/recipe/{ref}", response_class=HTMLResponse)
    def recipe(request: Request, ref: str, store=Store):
        detail = get_recipe_detail(
            ref, store.inventory_names(),
            recipes_db_path=RECIPES_DB_PATH, app_db_path=APP_DB_PATH,
        )
        if detail is None or (demo and detail["source"] == "custom"):
            return _render(request, store, "notfound.html", ref=ref)
        return _render(
            request, store, "recipe.html",
            r=detail,
            show_thumbs=len(detail["ingredients"]) <= MAX_INGREDIENT_THUMBS,
            missing=[i for i in detail["ingredients"] if not i["have"]],
        )

    # -- add your own ------------------------------------------------------
    @router.get("/add", response_class=HTMLResponse)
    def add(request: Request, store=Store):
        return _render(request, store, "add.html", extracted=None, saved_ref=None, error=None)

    @router.post("/add", response_class=HTMLResponse)
    async def add_post(
        request: Request, store=Store,
        text: str = Form(""), photo: UploadFile | None = None,
    ):
        error = extracted = saved_ref = None
        try:
            if photo is not None and photo.filename:
                content = await photo.read()
                extracted = await extract_from_image(
                    content, mime=photo.content_type or "image/jpeg"
                )
            elif text.strip():
                extracted = await extract_from_text(text.strip())
            else:
                error = "Paste a recipe or choose a photo first."
        except ValueError:
            error = "Couldn't find a recipe in that input."
        except Exception:
            error = "Extraction failed — try again in a moment."

        if extracted is not None and not demo:
            recipe_id, _saved = save_extracted(
                extracted, user_input=text.strip() or "[photo]", db_path=APP_DB_PATH
            )
            saved_ref = f"c{recipe_id}"

        return _render(
            request, store, "add.html",
            extracted=extracted, saved_ref=saved_ref, error=error,
        )

    return router
