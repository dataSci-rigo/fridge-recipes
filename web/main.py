"""fridge-recipes web app (Phase 5 of the original system plan).

Run locally:  uvicorn web.main:app --reload
Personal-mode routes require HTTP Basic Auth (FRIDGE_WEB_USERNAME /
FRIDGE_WEB_PASSWORD in .env); /demo* and the image/autocomplete API stay
open. Demo mode runs a synthetic per-browser-session fridge and never
touches the real inventory table.
"""
from __future__ import annotations

import secrets

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from core.config import WEB_PASSWORD, WEB_USERNAME
from core.db import init_all
from web.api import router as api_router
from web.state import DemoStore, PersonalStore
from web.views import make_router

_security = HTTPBasic()


def require_auth(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    """401 unless creds match .env. Unset creds = personal mode disabled,
    never silently open."""
    ok = (
        WEB_USERNAME
        and WEB_PASSWORD
        and secrets.compare_digest(credentials.username, WEB_USERNAME)
        and secrets.compare_digest(credentials.password, WEB_PASSWORD)
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bad credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def personal_store() -> PersonalStore:
    return PersonalStore()


DEMO_COOKIE = "fridge_demo_session"


def demo_store(request: Request) -> DemoStore:
    store = DemoStore.open_session(request.cookies.get(DEMO_COOKIE))
    request.state.demo_session_id = store.session_id
    return store


app = FastAPI(title="fridge-recipes")
init_all()

app.include_router(api_router)
app.include_router(
    make_router(demo=False, get_store=personal_store),
    dependencies=[Depends(require_auth)],
)
app.include_router(make_router(demo=True, get_store=demo_store), prefix="/demo")


@app.middleware("http")
async def _persist_demo_cookie(request: Request, call_next):
    """DemoStore.open_session may mint a fresh session id (no/stale cookie);
    stash it on request.state in the dependency and set the cookie here so
    every /demo response carries it."""
    response = await call_next(request)
    sid = getattr(request.state, "demo_session_id", None)
    if sid and request.cookies.get(DEMO_COOKIE) != sid:
        response.set_cookie(
            DEMO_COOKIE, sid, max_age=7 * 24 * 3600, httponly=True, samesite="lax"
        )
    return response


