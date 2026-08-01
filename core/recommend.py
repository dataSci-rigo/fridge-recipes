""""Recipes like the ones I've saved" -- FAISS nearest-neighbor search over
precomputed recipe embeddings (etl/embed_recipes.py builds the index).

No embedding model runs here: liked recipes already have vectors stored in
the index, so a recommendation is just averaging a handful of vectors and
doing a FAISS search -- faiss-cpu only, safe to run in the live bot process.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import faiss
import numpy as np

from core.config import RECIPES_DB_PATH
from core.search import get_recipe

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INDEX_PATH = PROJECT_ROOT / "data" / "recipe_embeddings.faiss"


@dataclass
class Recommendation:
    recipe_id: int
    title: str
    similarity: float


@lru_cache(maxsize=1)
def _load_index(index_path: Path) -> faiss.Index:
    return faiss.read_index(str(index_path))


def recommend(
    liked_recipe_ids: list[int],
    top_n: int = 5,
    exclude: set[int] | None = None,
    index_path: Path = DEFAULT_INDEX_PATH,
    db_path: Path = RECIPES_DB_PATH,
) -> list[Recommendation]:
    """Nearest neighbors to the average embedding of `liked_recipe_ids`,
    excluding `exclude` (defaults to the liked set itself, so you don't get
    recommended the same recipes you just saved)."""
    if not liked_recipe_ids:
        return []

    exclude = set(exclude) if exclude is not None else set(liked_recipe_ids)

    index = _load_index(index_path)
    vectors = []
    for recipe_id in liked_recipe_ids:
        try:
            vectors.append(index.reconstruct(recipe_id))
        except RuntimeError:
            continue  # recipe_id not in the index (e.g. embedded after a stale build)
    if not vectors:
        return []

    query = np.mean(vectors, axis=0, dtype="float32")
    query /= np.linalg.norm(query) or 1.0

    k = top_n + len(exclude)
    similarities, ids = index.search(query.reshape(1, -1), k)

    results: list[Recommendation] = []
    for recipe_id, similarity in zip(ids[0], similarities[0]):
        if recipe_id < 0 or recipe_id in exclude:
            continue
        detail = get_recipe(int(recipe_id), db_path=db_path)
        if detail is None:
            continue
        results.append(Recommendation(int(recipe_id), detail["title"], float(similarity)))
        if len(results) >= top_n:
            break
    return results
