import pandas as pd

from agent.db import Database
from agent.sheet import COLUMNS, export


def test_sheet_export_roundtrip(tmp_path):
    db = Database(tmp_path / "s.db")
    db.upsert_discovered([
        {"job_id": "1", "title": "Eng", "company": "Acme", "location": "Remote",
         "url": "https://x/1", "description": "d", "source": "search"},
    ])
    db.update("1", match_score=88, status="applied")
    out = export(db, tmp_path / "jobs.csv")
    df = pd.read_csv(out)
    assert list(df.columns) == COLUMNS
    assert df.iloc[0]["job_id"] == 1 or str(df.iloc[0]["job_id"]) == "1"
    assert df.iloc[0]["match_score"] == 88
    assert df.iloc[0]["status"] == "applied"
