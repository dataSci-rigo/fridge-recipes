import faiss
import numpy as np

from core import db
from core.recommend import recommend


def _make_recipes_db(tmp_path):
    path = tmp_path / "recipes.db"
    db.init_recipes_db(path)
    conn = db.connect(path)
    conn.executescript(
        """
        INSERT INTO recipes (id, title, directions) VALUES
            (1, 'Basil Pesto Pasta', 'cook it'),
            (2, 'Pesto Chicken', 'cook it'),
            (3, 'Chocolate Cake', 'bake it'),
            (4, 'Vanilla Cupcakes', 'bake it');
        """
    )
    conn.commit()
    conn.close()
    return path


def _make_index(tmp_path):
    # 3D toy embeddings: recipes 1+2 ("pesto"-ish) point almost the same
    # direction, 3+4 ("dessert"-ish) point a different, orthogonal direction.
    vectors = {
        1: np.array([1.0, 0.0, 0.0]),
        2: np.array([0.99, 0.14, 0.0]),
        3: np.array([0.0, 0.0, 1.0]),
        4: np.array([0.0, 0.05, 0.999]),
    }
    index = faiss.IndexIDMap2(faiss.IndexFlatIP(3))
    ids = np.array(list(vectors.keys()), dtype="int64")
    mat = np.stack([v / np.linalg.norm(v) for v in vectors.values()]).astype("float32")
    index.add_with_ids(mat, ids)
    path = tmp_path / "test.faiss"
    faiss.write_index(index, str(path))
    return path


def test_recommend_returns_similar_recipes(tmp_path):
    db_path = _make_recipes_db(tmp_path)
    index_path = _make_index(tmp_path)

    results = recommend([1], top_n=2, index_path=index_path, db_path=db_path)

    assert [r.recipe_id for r in results] == [2, 4] or [r.recipe_id for r in results][0] == 2
    assert results[0].recipe_id == 2
    assert results[0].title == "Pesto Chicken"
    assert 1 not in [r.recipe_id for r in results]


def test_recommend_excludes_liked_set_by_default(tmp_path):
    db_path = _make_recipes_db(tmp_path)
    index_path = _make_index(tmp_path)

    results = recommend([1, 2], top_n=5, index_path=index_path, db_path=db_path)
    ids = [r.recipe_id for r in results]
    assert 1 not in ids
    assert 2 not in ids


def test_recommend_empty_liked_list(tmp_path):
    db_path = _make_recipes_db(tmp_path)
    index_path = _make_index(tmp_path)
    assert recommend([], index_path=index_path, db_path=db_path) == []


def test_recommend_ignores_unknown_recipe_ids(tmp_path):
    db_path = _make_recipes_db(tmp_path)
    index_path = _make_index(tmp_path)
    # id 999 isn't in the index -- should be skipped, not raise.
    results = recommend([999], index_path=index_path, db_path=db_path)
    assert results == []
