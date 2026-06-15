from agent.pipeline.match import _parse_score


def test_parse_score_int_and_float():
    assert _parse_score(85) == 85
    assert _parse_score(85.0) == 85
    assert _parse_score("72") == 72


def test_parse_score_percent_and_whitespace():
    assert _parse_score(" 90% ") == 90


def test_parse_score_junk_is_zero():
    assert _parse_score("eighty") == 0
    assert _parse_score(None) == 0
    assert _parse_score({}) == 0


def test_parse_score_clamped():
    assert _parse_score(150) == 100
    assert _parse_score(-5) == 0
