"""Apply button text matching helpers."""

from agent.browser.job_page import (
    _apply_label_looks_valid,
    _looks_like_apply_button,
)


class _FakeLoc:
    def __init__(self, *, text="", aria="", title="", visible=True, count=1):
        self._text = text
        self._aria = aria
        self._title = title
        self._visible = visible
        self._count = count

    def count(self):
        return self._count

    def is_visible(self):
        return self._visible

    def inner_text(self, timeout=0):
        return self._text

    def get_attribute(self, name):
        if name == "aria-label":
            return self._aria
        if name == "title":
            return self._title
        return ""


def test_find_linkedin_apply_button_no_infinite_recursion():
    """Regression: scroll=False must not recurse forever (bug broke all applies)."""
    calls = {"n": 0}

    class FakePage:
        def locator(self, _sel):
            calls["n"] += 1
            return FakeLocator()

    class FakeLocator:
        def count(self):
            return 0

        def nth(self, _i):
            return self

        def is_visible(self):
            return False

    page = FakePage()
    # Patch helpers so we only exercise recursion guard in find_linkedin_apply_button.
    import agent.browser.job_page as jp

    orig_role = jp._role_apply_button
    orig_text = jp._text_search_apply_button
    orig_scroll = jp._scroll_apply_into_view
    orig_top = jp._find_in_apply_top_card
    jp._role_apply_button = lambda _p: None
    jp._text_search_apply_button = lambda _p: None
    jp._scroll_apply_into_view = lambda _p: None
    jp._find_in_apply_top_card = lambda _p: None
    try:
        assert jp.find_linkedin_apply_button(page, scroll=True) is None
        assert jp.find_linkedin_apply_button(page, scroll=False) is None
        assert calls["n"] < 500
    finally:
        jp._role_apply_button = orig_role
        jp._text_search_apply_button = orig_text
        jp._scroll_apply_into_view = orig_scroll
        jp._find_in_apply_top_card = orig_top

    assert _apply_label_looks_valid("Easy Apply")
    assert _apply_label_looks_valid("Apply now")
    assert _apply_label_looks_valid("Apply to Senior Engineer")
    assert not _apply_label_looks_valid("Already applied")
    assert not _apply_label_looks_valid("500 applicants")
    assert not _apply_label_looks_valid("How you match")


def test_rejects_job_card_link_false_positive():
    """Regression: job-card <a> inner_text must not pass as an apply button."""
    card = _FakeLoc(
        text=(
            "Senior Software Engineer\n\n"
            "Meds.com\n\n"
            "United States (Remote)\n\n"
            "401(k), +1 benefit\n\n"
            "Actively reviewing applicants"
        ),
        aria="",
    )
    assert not _looks_like_apply_button(card)


def test_accepts_real_easy_apply_aria():
    btn = _FakeLoc(text="", aria="Easy Apply to Senior Software Engineer at Meds.com")
    assert _looks_like_apply_button(btn)


def test_accepts_external_apply_link():
    """External apply ('Responses managed off LinkedIn') is usually <a>Apply</a>."""
    link = _FakeLoc(text="Apply", aria="")
    assert _looks_like_apply_button(link)
    assert _apply_label_looks_valid("Apply")


def test_text_search_does_not_return_unvalidated_job_card():
    """Regression: _text_search must not return a job-card ancestor without validation."""
    import agent.browser.job_page as jp

    card = _FakeLoc(
        text="Senior Software Engineer\nMeds.com\nUnited States (Remote)",
        aria="",
    )

    class FakeTextItem:
        def is_visible(self):
            return True

        def evaluate(self, _expr):
            return "A"

        def get_attribute(self, _name):
            return ""

        def locator(self, _xpath):
            return card

    class FakePage:
        def get_by_text(self, _pat):
            return FakeGetByText()

        def locator(self, _sel):
            return _FakeLoc(count=0)

    class FakeGetByText:
        def count(self):
            return 1

        def nth(self, _i):
            return FakeTextItem()

    assert jp._text_search_apply_button(FakePage()) is None
