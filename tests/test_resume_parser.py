from pathlib import Path

from agent.resume.parser import (
    MIN_RESUME_CHARS,
    clear_resume_cache,
    extract_text,
    resume_text_looks_valid,
)


def test_txt_resume_extracts(tmp_path):
    p = tmp_path / "resume.txt"
    p.write_text("Jane Doe\nSenior Engineer\nPython, Go, AWS\n" * 5, encoding="utf-8")
    text = extract_text(p)
    assert resume_text_looks_valid(text)


def test_cached_resume_skips_poisoning_on_short_read(tmp_path):
    clear_resume_cache()
    p = tmp_path / "resume.txt"
    p.write_text("x" * (MIN_RESUME_CHARS + 10), encoding="utf-8")
    from agent.resume import parser as mod

    first = mod.cached_resume_text(str(p))
    assert len(first) >= MIN_RESUME_CHARS
    p.write_text("short", encoding="utf-8")
    # Cache should still return the good read until cleared.
    assert len(mod.cached_resume_text(str(p))) >= MIN_RESUME_CHARS
    clear_resume_cache()


def test_pdf_fallback_uses_longer_extractor(tmp_path, monkeypatch):
    p = tmp_path / "resume.pdf"
    p.write_bytes(b"%PDF-fake")

    from agent.resume import parser as mod

    monkeypatch.setattr(mod, "_from_pdf_pypdf", lambda _p: "short")
    monkeypatch.setattr(
        mod,
        "_from_pdf_pdfminer",
        lambda _p: "A" * (MIN_RESUME_CHARS + 5),
    )
    text = mod._from_pdf(p)
    assert resume_text_looks_valid(text)
