"""One-time backfill: embed every recipe's title+ingredients text and build a
FAISS index for core/recommend.py's "recipes like the ones I've saved" search.

Only this script needs sentence-transformers/torch (pip install -e ".[embed]"
locally, or run inside an env that already has it, e.g. sem_search_gpu which
has CUDA + the same bge-small-en-v1.5 model used by semantic_search/
image-search). The live bot only ever loads the *output* FAISS index with
faiss-cpu -- no model inference at query time.

Usage: python -m etl.embed_recipes [--model bge-small-en-v1.5] [--batch-size 256]
                                    [--limit N] [--out data/recipe_embeddings.faiss]
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import faiss
import numpy as np

from core.config import RECIPES_DB_PATH
from core.db import connect

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = PROJECT_ROOT / "data" / "recipe_embeddings.faiss"

# Same model/repo id convention as semantic_search/image-search's text_embed.py.
MODEL_REPOS = {
    "bge-small-en-v1.5": "BAAI/bge-small-en-v1.5",
}
EMBED_DIM = 384


def load_recipe_texts(db_path: Path, limit: int | None) -> tuple[list[int], list[str]]:
    conn = connect(db_path)
    try:
        query = "SELECT rowid, title, ingredients_text FROM recipes_fts"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        rows = conn.execute(query).fetchall()
    finally:
        conn.close()
    ids = [r[0] for r in rows]
    texts = [f"{title}. {ingredients}" for _rowid, title, ingredients in rows]
    return ids, texts


def build_index(
    model_id: str, db_path: Path, out_path: Path, batch_size: int, limit: int | None
) -> None:
    from sentence_transformers import SentenceTransformer

    ids, texts = load_recipe_texts(db_path, limit)
    print(f"embedding {len(texts)} recipes with {model_id}...")

    repo = MODEL_REPOS.get(model_id, model_id)
    model = SentenceTransformer(repo)

    index = faiss.IndexIDMap2(faiss.IndexFlatIP(EMBED_DIM))
    t0 = time.time()
    for start in range(0, len(texts), batch_size):
        batch_ids = ids[start : start + batch_size]
        batch_texts = texts[start : start + batch_size]
        vectors = model.encode(
            batch_texts, normalize_embeddings=True, show_progress_bar=False
        )
        index.add_with_ids(
            np.asarray(vectors, dtype="float32"), np.asarray(batch_ids, dtype="int64")
        )
        if (start // batch_size) % 20 == 0:
            elapsed = time.time() - t0
            print(f"  {start + len(batch_ids)}/{len(texts)} ({elapsed:.0f}s)")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(out_path))
    print(f"wrote {index.ntotal} vectors ({EMBED_DIM}-dim) -> {out_path} "
          f"in {time.time() - t0:.0f}s")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="bge-small-en-v1.5")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--db", type=Path, default=RECIPES_DB_PATH)
    args = parser.parse_args()
    build_index(args.model, args.db, args.out, args.batch_size, args.limit)


if __name__ == "__main__":
    main()
