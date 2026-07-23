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

TELEGRAM_TOKEN = os.environ.get("FRIDGE_TELEGRAM_TOKEN")
ALLOWED_USER_ID = os.environ.get("FRIDGE_ALLOWED_USER_ID")
