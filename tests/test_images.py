"""core/images.py -- cache hit / miss / permanent-miss paths, no real HTTP."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from core import images
from core.db import connect, init_recipes_db


class FakeResponse:
    def __init__(self, content=b"\x89fakeimg", content_type="image/jpeg", status=200):
        self.content = content
        self.headers = {"Content-Type": content_type}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise images.requests.HTTPError(f"{self.status_code}")

    def json(self):
        raise ValueError("not json")


@pytest.fixture
def recipes_db(tmp_path):
    db = tmp_path / "recipes.db"
    init_recipes_db(db)
    conn = connect(db)
    conn.execute(
        "INSERT INTO recipes (id, title, directions, image_url) VALUES "
        "(1, 'Toast', 'toast it', 'http://example.com/toast.jpg'),"
        "(2, 'Ice', 'freeze it', NULL)"
    )
    conn.commit()
    conn.close()
    return db


def test_recipe_image_fetch_and_cache(recipes_db, tmp_path):
    imgdir = tmp_path / "img"
    with patch.object(images.requests, "get", return_value=FakeResponse()) as get:
        p = images.get_recipe_image(1, db_path=recipes_db, images_dir=imgdir)
        assert p is not None and p.exists() and p.suffix == ".jpg"
        # Second call: served from cache, no new HTTP
        p2 = images.get_recipe_image(1, db_path=recipes_db, images_dir=imgdir)
        assert p2 == p
        assert get.call_count == 1


def test_recipe_image_no_url(recipes_db, tmp_path):
    with patch.object(images.requests, "get") as get:
        assert images.get_recipe_image(2, db_path=recipes_db, images_dir=tmp_path) is None
        get.assert_not_called()


def test_recipe_image_dead_link_is_permanent_miss(recipes_db, tmp_path):
    with patch.object(
        images.requests, "get", return_value=FakeResponse(status=404)
    ) as get:
        assert images.get_recipe_image(1, db_path=recipes_db, images_dir=tmp_path) is None
        assert get.call_count == 1
        # Miss is cached: no retry on the next view
        assert images.get_recipe_image(1, db_path=recipes_db, images_dir=tmp_path) is None
        assert get.call_count == 1
    conn = connect(recipes_db)
    assert conn.execute(
        "SELECT image_cached_path FROM recipes WHERE id = 1"
    ).fetchone()[0] == images.UNAVAILABLE
    conn.close()


def test_recipe_image_missing_recipe(recipes_db, tmp_path):
    assert images.get_recipe_image(999, db_path=recipes_db, images_dir=tmp_path) is None


def test_ingredient_image_fetch_and_cache(recipes_db, tmp_path):
    imgdir = tmp_path / "img"
    calls = []

    def fake_search(name):
        calls.append(name)
        return "http://commons.example/onion.jpg"

    with patch.object(images.requests, "get", return_value=FakeResponse()):
        p = images.get_ingredient_image(
            "red onion", db_path=recipes_db, images_dir=imgdir, search_fn=fake_search
        )
        assert p is not None and p.exists()
        assert p.name == "red-onion.jpg"
        p2 = images.get_ingredient_image(
            "red onion", db_path=recipes_db, images_dir=imgdir, search_fn=fake_search
        )
        assert p2 == p
    assert calls == ["red onion"]  # search ran once, second hit came from cache


def test_ingredient_image_no_result_is_permanent_miss(recipes_db, tmp_path):
    calls = []

    def fake_search(name):
        calls.append(name)
        return None

    for _ in range(2):
        assert (
            images.get_ingredient_image(
                "unicorn dust", db_path=recipes_db, images_dir=tmp_path,
                search_fn=fake_search,
            )
            is None
        )
    assert calls == ["unicorn dust"]


def test_ingredient_image_refetches_if_file_deleted(recipes_db, tmp_path):
    imgdir = tmp_path / "img"

    def fake_search(name):
        return "http://commons.example/onion.jpg"

    with patch.object(images.requests, "get", return_value=FakeResponse()):
        p = images.get_ingredient_image(
            "onion", db_path=recipes_db, images_dir=imgdir, search_fn=fake_search
        )
        p.unlink()
        p2 = images.get_ingredient_image(
            "onion", db_path=recipes_db, images_dir=imgdir, search_fn=fake_search
        )
        assert p2 is not None and p2.exists()


def test_commons_search_parses_thumburl():
    payload = {
        "query": {
            "pages": {
                "10": {
                    "index": 2,
                    "imageinfo": [{"mime": "image/jpeg", "url": "http://x/full.jpg",
                                   "thumburl": "http://x/thumb2.jpg"}],
                },
                "11": {
                    "index": 1,
                    "imageinfo": [{"mime": "image/jpeg", "url": "http://x/full1.jpg",
                                   "thumburl": "http://x/thumb1.jpg"}],
                },
                "12": {
                    "index": 0,
                    "imageinfo": [{"mime": "image/svg+xml", "url": "http://x/icon.svg"}],
                },
            }
        }
    }

    class JsonResponse(FakeResponse):
        def json(self):
            return payload

    with patch.object(images.requests, "get", return_value=JsonResponse()):
        # svg (index 0) skipped; lowest-index raster result wins
        assert images._commons_search("onion") == "http://x/thumb1.jpg"


def test_ext_inference():
    assert images._ext_for("http://x/a.png?w=1", None) == ".png"
    assert images._ext_for("http://x/a", "image/webp") == ".webp"
    assert images._ext_for("http://x/a", None) == ".jpg"
