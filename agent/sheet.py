"""Export the SQLite job table to a human-readable spreadsheet (xlsx/csv)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from agent.db import Database

COLUMNS = [
    "job_id",
    "title",
    "company",
    "location",
    "url",
    "source",
    "match_score",
    "match_reasons",
    "apply_type",
    "status",
    "approved",
    "use_master_resume",
    "needs_input",
    "review_resolved",
    "resume_path",
    "cover_letter_path",
    "applied_at",
    "last_seen",
    "notes",
]


def export(db: Database, sheet_file: Path) -> Path:
    rows = [job.as_row() for job in db.all_jobs()]
    df = pd.DataFrame(rows, columns=COLUMNS)
    sheet_file.parent.mkdir(parents=True, exist_ok=True)

    suffix = sheet_file.suffix.lower()
    if suffix == ".csv":
        df.to_csv(sheet_file, index=False)
    else:
        df.to_excel(sheet_file, index=False, engine="openpyxl")
    return sheet_file
