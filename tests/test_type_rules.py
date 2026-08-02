from etl.type_rules import tag_category


def test_tags_dessert():
    assert tag_category("Chocolate Chip Cookies") == "dessert"


def test_tags_soup():
    assert tag_category("Tuscan Bean Soup with Shrimp") == "soup"


def test_word_boundary_avoids_false_positive_in_dressing_name():
    # "chili" is not a soup keyword, but this guards the general principle:
    # a category word embedded in another word shouldn't match.
    assert tag_category("Spinach Salad with Chili-Orange Dressing") == "salad"


def test_word_boundary_avoids_stew_substring_match():
    assert tag_category("Spicy Stewed Beef with Creamy Cheddar Grits") is None


def test_no_match_returns_none():
    assert tag_category("Lemon Chicken") is None


def test_empty_title_returns_none():
    assert tag_category("") is None
