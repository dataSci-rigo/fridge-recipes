from etl.nutrition_backfill import UsdaFood, _tokenize, build_word_index, match_ingredient


def _food(fdc_id, description, priority=0, penalty=0, has_nutrients=True):
    segments = [s.strip() for s in description.split(",")]
    return UsdaFood(
        fdc_id=fdc_id,
        description=description,
        first_segment_tokens=_tokenize(segments[0]),
        head_tokens=_tokenize(", ".join(segments[:2])),
        all_tokens=_tokenize(description),
        processed_penalty=penalty,
        priority=priority,
        nutrients={"cal_per_100g": 40.0} if has_nutrients else {},
    )


def test_prefers_first_segment_identity_over_tail_mention():
    foods = [
        _food("1", "Noodles, egg, cooked, enriched, with added salt"),
        _food("2", "Egg, whole, raw, fresh"),
    ]
    idx = build_word_index(foods)
    result = match_ingredient("egg", foods, idx)
    assert result.fdc_id == "2"


def test_prefers_plain_over_processed_on_tie():
    foods = [
        _food("1", "Onions, dried", penalty=1),
        _food("2", "Onions, raw", penalty=0),
    ]
    idx = build_word_index(foods)
    result = match_ingredient("onion", foods, idx)
    assert result.fdc_id == "2"


def test_prefers_candidate_with_nutrient_data():
    foods = [
        _food("1", "Chicken, breast, raw", has_nutrients=False),
        _food("2", "Chicken, broilers or fryers, breast, meat only, raw", has_nutrients=True),
    ]
    idx = build_word_index(foods)
    result = match_ingredient("chicken breast", foods, idx)
    assert result.fdc_id == "2"


def test_no_match_returns_none():
    foods = [_food("1", "Bread, whole wheat")]
    idx = build_word_index(foods)
    assert match_ingredient("kumquat", foods, idx) is None
