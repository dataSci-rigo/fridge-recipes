from core import db, inventory


def _make_db(tmp_path):
    path = tmp_path / "app.db"
    db.init_app_db(path)
    return path


def test_add_and_list_inventory(tmp_path):
    path = _make_db(tmp_path)
    inventory.add_item("Chicken Thighs", "2 lbs", db_path=path)
    inventory.add_item("Lemon", db_path=path)

    items = inventory.list_inventory(db_path=path)
    names = {i["canonical_ingredient"] for i in items}
    assert names == {"chicken thighs", "lemon"}
    assert all(i["state"] == "fresh" for i in items)
    assert all(i["location"] == "fridge" for i in items)


def test_add_sets_expiry_from_shelf_life(tmp_path):
    path = _make_db(tmp_path)
    conn = db.connect(path)
    conn.execute(
        "INSERT INTO shelf_life (canonical_ingredient, location, days) VALUES (?, ?, ?)",
        ("spinach", "fridge", 5),
    )
    conn.commit()
    conn.close()

    inventory.add_item("spinach", db_path=path)
    items = inventory.find_items("spinach", db_path=path)
    assert len(items) == 1
    assert items[0]["expires_at"] is not None


def test_add_without_shelf_life_leaves_expiry_null(tmp_path):
    path = _make_db(tmp_path)
    inventory.add_item("mystery item", db_path=path)
    items = inventory.find_items("mystery item", db_path=path)
    assert items[0]["expires_at"] is None


def test_eat_item_removes_and_returns_rows(tmp_path):
    path = _make_db(tmp_path)
    inventory.add_item("chicken thighs", "2 lbs", db_path=path)

    removed = inventory.eat_item("Chicken Thighs", db_path=path)
    assert len(removed) == 1
    assert removed[0]["canonical_ingredient"] == "chicken thighs"
    assert inventory.find_items("chicken thighs", db_path=path) == []


def test_eat_item_not_found_returns_empty(tmp_path):
    path = _make_db(tmp_path)
    assert inventory.eat_item("nonexistent", db_path=path) == []


def test_remove_item(tmp_path):
    path = _make_db(tmp_path)
    inventory.add_item("cilantro", db_path=path)
    removed = inventory.remove_item("cilantro", db_path=path)
    assert len(removed) == 1
    assert inventory.list_inventory(db_path=path) == []


def test_list_inventory_filters_by_location(tmp_path):
    path = _make_db(tmp_path)
    inventory.add_item("chicken thighs", location="freezer", db_path=path)
    inventory.add_item("lemon", location="fridge", db_path=path)

    fridge_items = inventory.list_inventory(location="fridge", db_path=path)
    assert [i["canonical_ingredient"] for i in fridge_items] == ["lemon"]
