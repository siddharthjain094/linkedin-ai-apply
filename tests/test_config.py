import yaml

import agent.config as config
from agent.config import Settings, SubmitMode, load_settings


def test_override_only_applies_non_none():
    s = Settings()
    s2 = s.override(match_threshold=85, dry_run=None, submit_mode=SubmitMode.review)
    assert s2.match_threshold == 85
    assert s2.submit_mode == SubmitMode.review
    # original unchanged
    assert s.match_threshold == 70
    assert s.submit_mode == SubmitMode.auto


def test_paths_resolve_under_project_root():
    s = Settings(db_path="data/state.db")
    assert s.db_file.is_absolute()
    assert s.db_file.name == "state.db"


def test_effective_user_data_dir_prefers_real_chrome_profile():
    # Default: app's own isolated profile under the project root.
    assert Settings().effective_user_data_dir.name == "browser_profile"
    # When pointed at a real Chrome dir, use it (with ~ expanded, outside project).
    s = Settings(chrome_user_data_dir="~/Library/Application Support/Google/Chrome")
    p = s.effective_user_data_dir
    assert "~" not in str(p)
    assert str(p).endswith("Google/Chrome")


def test_learned_answers_merge_into_screening(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "intake.yaml").write_text(
        yaml.safe_dump({"screening_answers": {"years of experience": "3",
                                              "authorized to work": "Yes"}}),
        encoding="utf-8",
    )
    # Learned answer overrides intake; new key is added.
    (profile / "learned_answers.yaml").write_text(
        yaml.safe_dump({"years of experience": "5",
                        "willing to travel": "Yes, up to 25%"}),
        encoding="utf-8",
    )

    s = load_settings(env_file=str(tmp_path / ".env"))
    answers = s.intake["screening_answers"]
    assert answers["years of experience"] == "5"          # learned wins
    assert answers["authorized to work"] == "Yes"          # intake preserved
    assert answers["willing to travel"] == "Yes, up to 25%"  # learned added


def test_learned_answers_path_env_is_consistent(tmp_path, monkeypatch):
    # The path used to SAVE (settings.learned_answers_file, via the review CLI)
    # must match the path used to LOAD, even when overridden by env.
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("LEARNED_ANSWERS_PATH", "custom/answers.yaml")
    s = load_settings(env_file=str(tmp_path / ".env"))
    assert s.learned_answers_path == "custom/answers.yaml"
    assert s.learned_answers_file == tmp_path / "custom/answers.yaml"
