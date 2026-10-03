"""Core data models."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Listing:
    platform: str
    job_title: str
    company: str
    location: str
    url: str
    posted_text: str = ""        # raw "posted x hours ago" text from the site
    description: str = ""        # job description snippet when available


@dataclass
class ApplicationRecord:
    date: str                    # YYYY-MM-DD
    platform: str
    job_title: str
    company: str
    location: str
    url: str
    fit_score: int = 0
    fit_reason: str = ""
    resume_variant: str = ""
    status: str = "evaluated"    # applied | skipped_* | blocked_*
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def as_tuple(self) -> tuple:
        return (
            self.date, self.platform, self.job_title, self.company, self.location,
            self.url, self.fit_score, self.fit_reason, self.resume_variant,
            self.status, self.created_at,
        )
