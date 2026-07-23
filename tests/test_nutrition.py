from unittest.mock import patch

from core import db
from core.nutrition import NutritionPer100g, lookup


def _make_db(tmp_path):
    path = tmp_path / "recipes.db"
    db.init_recipes_db(path)
    return path


def test_lookup_returns_cached_value_without_network(tmp_path):
    path = _make_db(tmp_path)
    conn = db.connect(path)
    conn.execute(
        "INSERT INTO ingredient_nutrition (canonical_name, cal_per_100g, source, updated_at) "
        "VALUES (?, ?, ?, ?)",
        ("chicken breast", 165.0, "usda", "2026-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()

    with patch("core.nutrition.requests.get") as mock_get:
        result = lookup("chicken breast", db_path=path)
        mock_get.assert_not_called()

    assert result is not None
    assert result.cal_per_100g == 165.0
    assert result.source == "usda"


def test_lookup_falls_back_to_live_and_caches(tmp_path):
    path = _make_db(tmp_path)

    fake_usda_response = {
        "foods": [
            {
                "description": "Egg, whole, raw",
                "foodNutrients": [
                    {"nutrientNumber": "1008", "value": 143},
                    {"nutrientNumber": "1003", "value": 12.6},
                ],
            }
        ]
    }

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return fake_usda_response

    with patch("core.nutrition.requests.get", return_value=FakeResponse()) as mock_get:
        result = lookup("egg", db_path=path)
        assert mock_get.called

    assert result is not None
    assert result.cal_per_100g == 143.0
    assert result.source == "usda"

    # Second call should hit the cache, not the network again.
    with patch("core.nutrition.requests.get") as mock_get:
        cached = lookup("egg", db_path=path)
        mock_get.assert_not_called()
    assert cached.cal_per_100g == 143.0


def test_lookup_returns_none_when_nothing_found(tmp_path):
    path = _make_db(tmp_path)

    class EmptyResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"foods": []}

    with patch("core.nutrition.requests.get", return_value=EmptyResponse()):
        result = lookup("nonexistent unobtainium root", db_path=path)

    assert result is None
