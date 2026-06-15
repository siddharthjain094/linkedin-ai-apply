from agent.browser.forms import _coerce_to_option, answer_from_intake, resolve_answer

INTAKE = {
    "screening_answers": {
        "years of experience": "6",
        "authorized to work": "Yes",
        "require sponsorship": "No",
    },
    "eeo": {"gender": "Decline to self identify"},
}


def test_coerce_exact_and_substring():
    assert _coerce_to_option("Yes", ["Yes", "No"]) == "Yes"
    assert _coerce_to_option("authorized", ["I am authorized", "No"]) == "I am authorized"


def test_coerce_yes_no_normalisation():
    assert _coerce_to_option("true", ["Yes, I am", "No"]) == "Yes, I am"
    assert _coerce_to_option("false", ["Yes", "No, I am not"]) == "No, I am not"


def test_coerce_unmappable_returns_none():
    # Previously this silently returned the first option (a wrong answer).
    assert _coerce_to_option("Maybe in six months", ["1-2 years", "3-5 years"]) is None


def test_coerce_freetext_passthrough_when_no_options():
    assert _coerce_to_option("anything", None) == "anything"


def test_answer_from_intake_substring_and_eeo():
    assert answer_from_intake("How many years of experience?", INTAKE) == "6"
    assert answer_from_intake("Gender", INTAKE) == "Decline to self identify"
    assert answer_from_intake("Totally unrelated question", INTAKE) is None


def test_resolve_answer_parks_when_intake_unmappable_and_no_llm():
    # intake says "No" sponsorship but options don't include a No-ish value -> None
    out = resolve_answer("require sponsorship", ["A", "B"], INTAKE, llm=None)
    assert out is None
