"""SQLite ledger: every listing the bot touches is recorded once.

Dedupe semantics (this is what prevents double-applying):
  - status == "applied"        -> never touch again
  - status startswith "skipped_" -> stays skipped
  - status startswith "blocked_" -> may be retried later (blocker can clear,
                                    e.g. you log in and blocked_needs_login goes away)
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from .models import ApplicationRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    platform TEXT NOT NULL,
    job_title TEXT,
    company TEXT,
    location TEXT,
    url TEXT,
    fit_score INTEGER DEFAULT 0,
    fit_reason TEXT DEFAULT '',
    resume_variant TEXT DEFAULT '',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_applications_url ON applications(url);
CREATE INDEX IF NOT EXISTS idx_applications_date ON applications(date);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def log_listing(conn: sqlite3.Connection, rec: ApplicationRecord) -> None:
    conn.execute(
        """INSERT INTO applications
           (date, platform, job_title, company, location, url, fit_score,
            fit_reason, resume_variant, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(url) DO UPDATE SET
             fit_score=excluded.fit_score,
             fit_reason=excluded.fit_reason,
             resume_variant=excluded.resume_variant,
             status=excluded.status,
             created_at=excluded.created_at""",
        rec.as_tuple(),
    )
    conn.commit()


def existing_status(conn: sqlite3.Connection, url: str) -> Optional[str]:
    if not url:
        return None
    row = conn.execute(
        "SELECT status FROM applications WHERE url = ? ORDER BY id DESC LIMIT 1",
        (url,),
    ).fetchone()
    return row["status"] if row else None


def should_attempt(conn: sqlite3.Connection, url: str) -> bool:
    """True if this listing may be processed in this run."""
    status = existing_status(conn, url)
    if status is None:
        return True
    if status == "applied":
        return False
    if status.startswith("blocked_"):
        return True   # blocker may have cleared; retry
    return False      # skipped_* stays skipped


def count_applied_on(conn: sqlite3.Connection, day: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM applications WHERE date = ? AND status = 'applied'",
        (day,),
    ).fetchone()
    return int(row["n"])


def fetch(
    conn: sqlite3.Connection,
    day: Optional[str] = None,
    status: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 500,
) -> List[dict]:
    sql = "SELECT * FROM applications WHERE 1=1"
    params: list = []
    if day:
        sql += " AND date = ?"
        params.append(day)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if query:
        sql += " AND (job_title LIKE ? OR company LIKE ? OR location LIKE ?)"
        like = f"%{query}%"
        params += [like, like, like]
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def daily_stats(conn: sqlite3.Connection, day: str) -> Dict:
    rows = fetch(conn, day=day, limit=100000)
    applied = [r for r in rows if r["status"] == "applied"]
    avg_fit = (
        round(sum(r["fit_score"] for r in applied) / len(applied), 1) if applied else 0.0
    )
    by_status: Dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    return {
        "date": day,
        "evaluated": len(rows),
        "applied": len(applied),
        "avg_fit_applied": avg_fit,
        "by_status": by_status,
    }


def last_n_days(conn: sqlite3.Connection, n: int = 7) -> List[Dict]:
    today = date.today()
    out = []
    for i in range(n - 1, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        s = daily_stats(conn, day)
        out.append({"date": day, "evaluated": s["evaluated"], "applied": s["applied"]})
    return out
