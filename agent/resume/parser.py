"""Extract plain text from a master resume (.docx, .pdf, or .txt)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


def extract_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(
            f"Master resume not found at {path}. Drop your resume there or set "
            f"MASTER_RESUME_PATH."
        )
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _from_docx(path)
    if suffix == ".pdf":
        return _from_pdf(path)
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    raise ValueError(f"Unsupported resume format: {suffix} (use .docx, .pdf, or .txt)")


def _from_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    parts: list[str] = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts).strip()


def _from_pdf(path: Path) -> str:
    from pdfminer.high_level import extract_text as pdf_extract

    return (pdf_extract(str(path)) or "").strip()


@lru_cache(maxsize=4)
def cached_resume_text(path_str: str) -> str:
    return extract_text(Path(path_str))
