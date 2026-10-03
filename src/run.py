"""One automation run: discover -> filter -> score -> apply -> log.

Usage:
    python -m src.run                  # headed browser, real applies
    python -m src.run --dry-run        # score + log only, submit nothing
    python -m src.run --headless       # no visible browser
    python -m src.run --max 20         # cap applications this run
    python -m src.run --report         # print today's daily report and exit

Runs outside the configured active window exit quietly (for cron use).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

from . import config as cfgmod
from .config import PROJECT_ROOT
from .models import ApplicationRecord
from .naukri import (
    apply_freshness_filter,
    ensure_login,
    extract_listings,
    posted_within_hours,
    profile_dir,
    search_role,
    try_apply,
)
from .resume_picker import pick_resume
from .report import daily_report
from .scorer import eligibility_block, score_listing
from .store import connect, count_applied_on, log_listing, should_attempt


def within_window(cfg: dict) -> bool:
    tz = ZoneInfo(cfgmod.timezone_name(cfg))
    now = datetime.now(tz).strftime("%H:%M")
    start, end = cfgmod.active_window(cfg)
    return start <= now < end


def run_once(cfg: dict, args) -> dict:
    auto = cfgmod.automation(cfg)
    db = connect(cfgmod.db_path(cfg))
    today = datetime.now(ZoneInfo(cfgmod.timezone_name(cfg))).date().isoformat()

    applied_today = count_applied_on(db, today)
    remaining = auto.get("daily_limit", 200) - applied_today
    if remaining <= 0:
        return {"status": "cap_reached", "applied_today": applied_today}

    min_score = auto.get("min_fit_score", 70)
    freshness_hours = auto.get("freshness_hours", 24)
    per_role_cap = auto.get("max_listings_per_role", 60)
    run_max = args.max or remaining

    stats = {"evaluated": 0, "applied": 0, "skipped": 0, "blocked": 0}
    c = cfgmod.candidate(cfg)
    expected_lpa = float(c.get("expected_ctc_inr", 600000)) / 100000

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir()),
            headless=args.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = browser.pages[0] if browser.pages else browser.new_page()
        page.goto("https://www.naukri.com", wait_until="domcontentloaded")

        if not ensure_login(page):
            log_listing(db, ApplicationRecord(
                date=today, platform="Naukri", job_title="(platform-level)",
                company="", location="", url="https://www.naukri.com",
                fit_score=0, fit_reason="login wall; manual login needed",
                status="blocked_needs_login"))
            browser.close()
            stats["blocked"] += 1
            return {"status": "blocked_needs_login", **stats}

        for role in cfg.get("target_roles", []):
            if stats["applied"] >= run_max:
                break
            search_role(page, role)
            apply_freshness_filter(page)  # best effort; Python filters too
            listings = extract_listings(page, limit=per_role_cap)

            for listing in listings:
                if stats["applied"] >= run_max:
                    break
                if not posted_within_hours(listing.posted_text, freshness_hours):
                    continue
                if not listing.url or not should_attempt(db, listing.url):
                    continue

                stats["evaluated"] += 1
                block = eligibility_block(listing.job_title, listing.description)
                if block:
                    log_listing(db, ApplicationRecord(
                        date=today, platform="Naukri", job_title=listing.job_title,
                        company=listing.company, location=listing.location, url=listing.url,
                        fit_score=0, fit_reason=block, status="skipped_eligibility"))
                    stats["skipped"] += 1
                    continue

                score, reason = score_listing(
                    listing.job_title, listing.company, listing.description,
                    listing.location, expected_ctc_lpa=expected_lpa)
                if score < min_score:
                    log_listing(db, ApplicationRecord(
                        date=today, platform="Naukri", job_title=listing.job_title,
                        company=listing.company, location=listing.location, url=listing.url,
                        fit_score=score, fit_reason=reason, status="skipped_low_fit"))
                    stats["skipped"] += 1
                    continue

                variant_key, resume_path = pick_resume(cfg, listing.job_title, listing.description)
                if not resume_path.exists():
                    log_listing(db, ApplicationRecord(
                        date=today, platform="Naukri", job_title=listing.job_title,
                        company=listing.company, location=listing.location, url=listing.url,
                        fit_score=score, fit_reason=reason + f"; resume missing: {resume_path}",
                        status="blocked_no_resume"))
                    stats["blocked"] += 1
                    continue

                status, detail = try_apply(
                    page, listing, cfg, resume_path, dry_run=args.dry_run)
                log_listing(db, ApplicationRecord(
                    date=today, platform="Naukri", job_title=listing.job_title,
                    company=listing.company, location=listing.location, url=listing.url,
                    fit_score=score, fit_reason=f"{reason} | {detail}",
                    resume_variant=variant_key, status=status))
                if status == "applied":
                    stats["applied"] += 1
                elif status.startswith("blocked"):
                    stats["blocked"] += 1
                else:
                    stats["skipped"] += 1

        browser.close()

    stats["applied_today"] = count_applied_on(db, today)
    stats["status"] = "ok"
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Naukri job application automation run")
    ap.add_argument("--dry-run", action="store_true", help="score and log only; submit nothing")
    ap.add_argument("--headless", action="store_true", help="run browser headless")
    ap.add_argument("--max", type=int, default=0, help="cap applications this run (0 = daily limit)")
    ap.add_argument("--roles", nargs="*", default=None, help="override target roles for this run")
    ap.add_argument("--report", action="store_true", help="print today's daily report and exit")
    ap.add_argument("--config", default=None, help="path to config.yaml")
    args = ap.parse_args()

    cfg = cfgmod.load_config(args.config)

    if args.report:
        print(daily_report(cfg))
        return

    if args.roles:
        cfg = dict(cfg)
        cfg["target_roles"] = args.roles

    if not within_window(cfg):
        print("Outside active window — nothing to do.")
        return

    if args.dry_run:
        print("DRY RUN — nothing will be submitted.")
    stats = run_once(cfg, args)
    print(
        "Evaluated {evaluated}, applied {applied} "
        "(skipped {skipped}, blocked {blocked}).".format(**{k: stats.get(k, 0) for k in
        ("evaluated", "applied", "skipped", "blocked")})
    )


if __name__ == "__main__":
    main()
