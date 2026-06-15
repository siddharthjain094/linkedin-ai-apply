"""Smart external applier helpers and LinkedIn → ATS handoff."""

from agent.browser import external_apply as ext
from agent.browser.job_page import is_linkedin_job_url, posting_closed


def test_is_linkedin_job_url():
    assert is_linkedin_job_url("https://www.linkedin.com/jobs/view/123456/")
    assert is_linkedin_job_url("https://www.linkedin.com/jobs/search/?currentJobId=99")
    assert not is_linkedin_job_url("https://boards.greenhouse.io/acme/jobs/1")
    assert not is_linkedin_job_url("")


def test_looks_like_apply_link():
    assert ext._looks_like_apply_link("https://boards.greenhouse.io/acme/jobs/1")
    assert ext._looks_like_apply_link("https://jobs.lever.co/acme/abc")
    assert not ext._looks_like_apply_link("https://linkedin.com/in/someone")


def test_render_elements_includes_value():
    els = [{"index": 0, "tag": "input", "type": "text", "label": "Email", "value": "a@b.com"}]
    out = ext._render_elements(els)
    assert 'value="a@b.com"' in out
    assert "[0]" in out


def test_posting_closed_detects_phrases():
    class FakePage:
        def get_by_text(self, *a, **k):
            class L:
                def count(self):
                    return 0
            return L()

        def inner_text(self, *a, **k):
            return "Sorry, this position has been filled."

    assert posting_closed(FakePage())
