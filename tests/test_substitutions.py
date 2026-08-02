from core import db
from core.substitutions import add_substitution, get_substitutes, lookup_or_ask


def _make_db(tmp_path):
    path = tmp_path / "app.db"
    db.init_app_db(path)
    return path


def test_get_substitutes_seed_both_directions(tmp_path):
    path = _make_db(tmp_path)
    assert "margarine" in get_substitutes("butter", db_path=path)
    assert "butter" in get_substitutes("margarine", db_path=path)


def test_add_substitution_and_lookup(tmp_path):
    path = _make_db(tmp_path)
    add_substitution("oat milk", "almond milk", source="ingested", db_path=path)
    assert "almond milk" in get_substitutes("oat milk", db_path=path)
    assert "oat milk" in get_substitutes("almond milk", db_path=path)


def test_add_substitution_ignores_self_pair(tmp_path):
    path = _make_db(tmp_path)
    add_substitution("salt", "salt", source="ingested", db_path=path)
    assert get_substitutes("salt", db_path=path) == set()


def test_get_substitutes_unknown_ingredient(tmp_path):
    path = _make_db(tmp_path)
    assert get_substitutes("kumquat extract", db_path=path) == set()


def test_lookup_or_ask_skips_claude_when_known(tmp_path):
    path = _make_db(tmp_path)

    def fail_if_called(name, have):
        raise AssertionError("ask_fn should not be called when seed already has an answer")

    result = lookup_or_ask("butter", db_path=path, ask_fn=fail_if_called)
    assert "margarine" in result


def test_lookup_or_ask_falls_back_and_caches(tmp_path):
    path = _make_db(tmp_path)
    calls = []

    def fake_ask(name, have):
        calls.append(name)
        return ["oat milk", "soy milk"]

    result = lookup_or_ask("almond milk", db_path=path, ask_fn=fake_ask)
    assert result == {"oat milk", "soy milk"}
    assert calls == ["almond milk"]

    # Second call should hit the now-populated cache, not ask_fn again.
    result2 = lookup_or_ask("almond milk", db_path=path, ask_fn=fake_ask)
    assert result2 == {"oat milk", "soy milk"}
    assert calls == ["almond milk"]
