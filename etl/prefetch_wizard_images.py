"""One-shot: warm the ingredient-image cache for every setup-wizard item
(etl/demo_items.yaml) so the splash screen renders with photos on first
load instead of trickling in via per-view lazy fetches.

Run on the laptop after an ETL build (needs recipes.db writable):
  python -m etl.prefetch_wizard_images

Idempotent -- already-cached items (including permanent misses) are skipped
by core.images' cache check. Sleeps briefly between Wikimedia queries out of
API politeness.
"""
from __future__ import annotations

import time

from core.images import get_ingredient_image
from web.state import wizard_items


def main() -> None:
    items = wizard_items()
    hits = misses = 0
    for name, _location in items:
        path = get_ingredient_image(name)
        if path is None:
            misses += 1
            print(f"  miss  {name}")
        else:
            hits += 1
            print(f"  ok    {name} -> {path.name}")
        time.sleep(0.3)
    print(f"\n{hits} cached, {misses} without a usable Commons image "
          f"(of {len(items)} wizard items)")


if __name__ == "__main__":
    main()
