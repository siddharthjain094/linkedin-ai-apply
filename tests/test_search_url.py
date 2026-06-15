from urllib.parse import parse_qs, urlparse

from agent.browser.search import build_url
from agent.config import SearchConfig


def test_build_url_encodes_filters():
    sc = SearchConfig(
        titles=["Backend Engineer"],
        keywords="python",
        locations=["United States"],
        remote=True,
        on_site=False,
        hybrid=False,
        experience_levels=["mid-senior"],
        date_posted="past-week",
        easy_apply_only=True,
    )
    url = build_url("Backend Engineer", "United States", sc)
    q = parse_qs(urlparse(url).query)
    assert q["keywords"][0] == "Backend Engineer python"
    assert q["location"][0] == "United States"
    assert q["f_E"][0] == "4"
    assert q["f_TPR"][0] == "r604800"
    assert q["f_WT"][0] == "2"
    assert q["f_AL"][0] == "true"


def test_build_url_any_date_omits_tpr():
    sc = SearchConfig(date_posted="any", remote=False, hybrid=False, on_site=True)
    url = build_url("Eng", "Remote", sc)
    q = parse_qs(urlparse(url).query)
    assert "f_TPR" not in q
    assert q["f_WT"][0] == "1"
