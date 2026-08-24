"""Environment configuration for the fridge-recipes system."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

load_dotenv(PROJECT_ROOT / ".env")

RECIPES_DB_PATH = Path(os.environ.get("FRIDGE_RECIPES_DB", DATA_DIR / "recipes.db"))
APP_DB_PATH = Path(os.environ.get("FRIDGE_APP_DB", DATA_DIR / "app.db"))
IMAGES_DIR = Path(os.environ.get("FRIDGE_IMAGES_DIR", DATA_DIR / "images"))

TELEGRAM_TOKEN = os.environ.get("FRIDGE_TELEGRAM_TOKEN")
ALLOWED_USER_ID = os.environ.get("FRIDGE_ALLOWED_USER_ID")

# Web app (Phase 5). Personal-mode routes require these via HTTP Basic Auth;
# /demo* stays open. Unset -> personal mode is disabled (401 on everything
# but demo), never silently open.
WEB_USERNAME = os.environ.get("FRIDGE_WEB_USERNAME")
WEB_PASSWORD = os.environ.get("FRIDGE_WEB_PASSWORD")
