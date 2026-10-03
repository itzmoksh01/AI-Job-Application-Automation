"""Daily report generation from the application ledger."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from . import config as cfgmod
from .store import connect, daily_stats, fetch


def daily_report(cfg: dict, day: str | None = None) -> str:
    tz = ZoneInfo(cfgmod.timezone_name(cfg))
    day = day or datetime.now(tz).date().isoformat()
    db = connect(cfgmod.db_path(cfg))
    s = daily_stats(db, day)
    rows = fetch(db, day=day, limit=100000)

    applied = [r for r in rows if r["status"] == "applied"]
    limit = cfgmod.automation(cfg).get("daily_limit", 200)

    lines = [
        f"# Daily report — {day}",
        "",
        f"**Applied:** {s['applied']} / {limit}",
        f"**Evaluated:** {s['evaluated']} listings",
        f"**Avg fit (applied):** {s['avg_fit_applied']}",
        "",
    ]

    if applied:
        lines.append("## Applied")
        for r in applied[:20]:
            lines.append(f"- {r['job_title']} @ {r['company']} (fit {r['fit_score']}, {r['resume_variant']})")
        lines.append("")

    blockers = [r for r in rows if r["status"].startswith("blocked_") or r["status"] == "skipped_question"]
    if blockers:
        lines.append("## Blockers / needs your input")
        seen = set()
        for r in blockers[:15]:
            key = (r["status"], r["fit_reason"][:80])
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- [{r['status']}] {r['job_title']} @ {r['company']}: {r['fit_reason'][:140]}")
        lines.append("")

    by_status = s["by_status"]
    if by_status:
        lines.append("## By status")
        for st, n in sorted(by_status.items(), key=lambda x: -x[1]):
            lines.append(f"- {st}: {n}")
        lines.append("")

    top = sorted([r for r in rows if r["fit_score"] >= 85 and r["status"] != "applied"],
                 key=lambda r: -r["fit_score"])[:8]
    if top:
        lines.append("## Notable high-fit roles (not applied)")
        for r in top:
            lines.append(f"- {r['job_title']} @ {r['company']} — fit {r['fit_score']} [{r['status']}]")
        lines.append("")

    return "\n".join(lines).strip()
