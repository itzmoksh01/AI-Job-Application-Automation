"""Naukri.com browser automation (Playwright).

Design notes
------------
* Login is NEVER handled with a password in code. We use a persistent
  browser profile: you log in once manually (``python -m src.naukri --login``),
  the session cookies persist, and every later run reuses them.
* Selectors are defensive on purpose — job boards change their DOM often.
  If extraction suddenly returns zero cards, update the SELECTORS dict below
  (see README troubleshooting).
* ``--dry-run`` (via run.py) scores and logs listings without submitting
  anything — always do a dry run first.
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from typing import List, Optional, Tuple

from playwright.sync_api import Page, sync_playwright

from .config import PROJECT_ROOT, candidate
from .models import Listing

BASE_URL = "https://www.naukri.com"

# Candidate CSS selectors, tried in order. Update when the site changes.
SELECTORS = {
    # logged-in indicators (any one matching => logged in)
    "logged_in": [
        "text=My Naukri",
        "[class*='user-name']",
        "[class*='nI-gNb-drawer']",
        "img[class*='avatar']",
        "text=Complete your profile",
    ],
    # homepage keyword search box
    "search_box": [
        "input[placeholder*='skills' i]",
        "input[placeholder*='Search jobs' i]",
        ".suggestor-input",
        "input[name='qp']",
    ],
    # job cards on the search results page
    "job_card": [
        "article.jobTuple",
        ".srp-jobtuple-wrapper",
        "div[data-job-id]",
        ".jobTuple",
    ],
    "card_title": ["a.title", ".title", "a[class*='title']"],
    "card_company": ["a.subTitle", ".subTitle", "a[class*='company']"],
    "card_location": [".locWdth", ".location", "span[class*='location']"],
    "card_posted": [".job-post-day", ".posted", "span[class*='post']"],
    # freshness / date-posted filter
    "filter_toggle": ["text=Freshness", "text=Date Posted"],
    "freshness_option": ["text=Last 24 hours", "text=Last 1 day", "text=Today"],
    # apply flow
    "apply_button": ["button:has-text('Apply')", "a:has-text('Apply')"],
    "already_applied": ["text=Applied", "text=You have already applied"],
    "success": ["text=successfully applied", "text=Application submitted"],
    "file_input": ["input[type='file']"],
}

FRESHNESS_LABELS = ["last 24 hours", "last 1 day", "today", "past 24 hours"]


# ------------------------------------------------------------------ login
def profile_dir() -> Path:
    d = Path.home() / ".job-automation" / "naukri-profile"
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_logged_in(page: Page) -> bool:
    for sel in SELECTORS["logged_in"]:
        try:
            if page.locator(sel).first.count() > 0:
                return True
        except Exception:
            continue
    return False


def ensure_login(page: Page, timeout_min: int = 10) -> bool:
    """Return True once logged in. Otherwise print instructions and wait for
    the user to complete login manually in the opened browser window."""
    if is_logged_in(page):
        return True
    print("\n" + "=" * 60)
    print("  Naukri login required.")
    print("  1. Log in to Naukri in the browser window that just opened")
    print("     (use any method you like — Google, email, OTP).")
    print("  2. Waiting up to %d minutes for the login to complete..." % timeout_min)
    print("=" * 60 + "\n")
    deadline = time.time() + timeout_min * 60
    while time.time() < deadline:
        time.sleep(5)
        try:
            if is_logged_in(page):
                print("Login detected. Continuing.")
                return True
        except Exception:
            pass
    return False


# ------------------------------------------------------------------ search
def _first_matching(page: Page, selectors: List[str]):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return None


def search_role(page: Page, role: str) -> bool:
    """Search Naukri for a role via the homepage search box (most robust)."""
    page.goto(BASE_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    box = _first_matching(page, SELECTORS["search_box"])
    if box is None:
        # fallback: direct search URL pattern
        slug = re.sub(r"[^a-z0-9]+", "-", role.lower()).strip("-")
        page.goto(f"{BASE_URL}/{slug}-jobs", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        return True
    try:
        box.click()
        box.fill(role)
        page.wait_for_timeout(800)
        box.press("Enter")
    except Exception:
        slug = re.sub(r"[^a-z0-9]+", "-", role.lower()).strip("-")
        page.goto(f"{BASE_URL}/{slug}-jobs", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    return True


def apply_freshness_filter(page: Page) -> bool:
    """Try to enable the site's own past-24-hours filter. Returns True if the
    filter was (probably) applied; listings are ALSO filtered in Python."""
    toggle = _first_matching(page, SELECTORS["filter_toggle"])
    if toggle:
        try:
            toggle.click()
            page.wait_for_timeout(1200)
            for sel in SELECTORS["freshness_option"]:
                opt = page.locator(sel).first
                if opt.count() > 0:
                    opt.click()
                    page.wait_for_timeout(2500)
                    return True
        except Exception:
            pass
    return False


def posted_within_hours(posted_text: str, hours: int = 24) -> bool:
    """Parse 'x hours ago' / 'x days ago' style labels. Unknown -> True
    (let the site filter decide) — but the caller logs the raw text."""
    t = (posted_text or "").lower()
    if not t:
        return True
    m = re.search(r"(\d+)\s*hour", t)
    if m:
        return int(m.group(1)) <= hours
    if any(w in t for w in ("just now", "few hours", "today")):
        return True
    m = re.search(r"(\d+)\s*day", t)
    if m:
        return int(m.group(1)) * 24 <= hours
    if "yesterday" in t:
        return hours >= 24
    if "week" in t or "month" in t:
        return False
    return True  # unknown label: keep, don't silently drop


def _text_of(card, selectors: List[str]) -> str:
    for sel in selectors:
        try:
            el = card.locator(sel).first
            if el.count() > 0:
                return el.inner_text().strip()
        except Exception:
            continue
    return ""


def extract_listings(page: Page, limit: int = 60) -> List[Listing]:
    cards = None
    for sel in SELECTORS["job_card"]:
        try:
            found = page.locator(sel)
            if found.count() > 0:
                cards = found
                break
        except Exception:
            continue
    if cards is None:
        return []
    listings: List[Listing] = []
    for i in range(min(cards.count(), limit)):
        try:
            card = cards.nth(i)
            title = _text_of(card, SELECTORS["card_title"])
            company = _text_of(card, SELECTORS["card_company"])
            location = _text_of(card, SELECTORS["card_location"])
            posted = _text_of(card, SELECTORS["card_posted"])
            href = ""
            try:
                link = card.locator(SELECTORS["card_title"][0]).first
                if link.count() > 0:
                    href = link.get_attribute("href") or ""
            except Exception:
                pass
            if href and href.startswith("/"):
                href = BASE_URL + href
            if title and href:
                listings.append(
                    Listing(
                        platform="Naukri",
                        job_title=title,
                        company=company,
                        location=location,
                        url=href,
                        posted_text=posted,
                    )
                )
        except Exception:
            continue
    return listings


# ------------------------------------------------------------------ apply
# Questions the bot is allowed to answer, mapped from profile fields.
# Anything else -> skipped_question (truthfulness guard).
def build_qa(cfg: dict) -> List[Tuple[List[str], str]]:
    c = candidate(cfg)
    qa: List[Tuple[List[str], str]] = [
        (["current ctc", "current salary"], str(c.get("current_ctc_inr", ""))),
        (["expected ctc", "expected salary"], str(c.get("expected_ctc_inr", ""))),
        (["notice period"], str(c.get("notice_period", ""))),
        (["total experience", "overall experience", "years of experience"],
         str(c.get("total_experience_years", ""))),
        (["email"], str(c.get("email", ""))),
        (["phone", "mobile", "contact number"], str(c.get("phone", ""))),
        (["location", "current location", "city"], str(c.get("current_location", ""))),
        (["portfolio", "portfolio link"], str(c.get("portfolio_url", ""))),
        (["relocat"], str(c.get("willing_to_relocate", ""))),
        (["in-person interview", "in person interview", "interview in person"],
         str(c.get("willing_in_person_interview", ""))),
        (["video editing experience", "video editing"],
         str(c.get("video_editing_years", ""))),
    ]
    # drop unanswered entries — the bot must never guess
    return [(keys, val) for keys, val in qa if str(val).strip()]


def _answer_for(question: str, qa: List[Tuple[List[str], str]]) -> Optional[str]:
    q = question.lower()
    for keys, val in qa:
        if any(k in q for k in keys):
            return val
    return None


def try_apply(
    page: Page,
    listing: Listing,
    cfg: dict,
    resume_path: Path,
    dry_run: bool = False,
) -> Tuple[str, str]:
    """Attempt one application. Returns (status, detail).

    Statuses: applied | already_applied | skipped_question |
              skipped_external | blocked_* | failed
    """
    if dry_run:
        return "dry_run", "scored only, nothing submitted"

    c = candidate(cfg)
    qa = build_qa(cfg)
    page.goto(listing.url, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)

    # external application? (redirected off naukri.com)
    try:
        if "naukri.com" not in (page.url or ""):
            return "skipped_external", f"redirected to {page.url}"
    except Exception:
        pass

    if _first_matching(page, SELECTORS["already_applied"]):
        return "already_applied", "site shows already applied"

    btn = _first_matching(page, SELECTORS["apply_button"])
    if btn is None:
        # maybe an external "Apply on company site" link
        try:
            if "naukri.com" not in (page.url or ""):
                return "skipped_external", "external apply flow"
        except Exception:
            pass
        return "failed", "no apply button found"
    try:
        btn.click()
    except Exception as e:
        return "failed", f"apply click failed: {e}"
    page.wait_for_timeout(2500)

    # external redirect after clicking apply
    try:
        if "naukri.com" not in (page.url or ""):
            return "skipped_external", f"apply redirected to {page.url}"
    except Exception:
        pass

    # upload resume if the form asks
    file_input = _first_matching(page, SELECTORS["file_input"])
    if file_input and resume_path.exists():
        try:
            file_input.set_input_files(str(resume_path))
            page.wait_for_timeout(1500)
        except Exception:
            pass

    # fill name / email / phone fields by label
    for label, value in (("name", c.get("name", "")), ("email", c.get("email", "")),
                        ("phone", c.get("phone", "")), ("mobile", c.get("phone", ""))):
        if not value:
            continue
        try:
            field = page.get_by_label(re.compile(label, re.I)).first
            if field.count() > 0 and not (field.input_value() or "").strip():
                field.fill(str(value))
        except Exception:
            pass

    # answer visible questions; unknown REQUIRED question -> skip (truthfulness)
    try:
        questions = page.locator("label, div[class*='question']").all()
    except Exception:
        questions = []
    for q in questions[:25]:
        try:
            qtext = (q.inner_text() or "").strip()
            if not qtext or len(qtext) > 300:
                continue
            answer = _answer_for(qtext, qa)
            required = "*" in qtext or "required" in qtext.lower()
            if answer is None:
                if required:
                    return "skipped_question", f"unanswerable required question: {qtext[:120]}"
                continue
            # try to fill an associated input
            try:
                inp = q.locator("xpath=following::input[1]").first
                if inp.count() > 0 and not (inp.input_value() or "").strip():
                    inp.fill(answer)
                    continue
            except Exception:
                pass
            # yes/no style radio
            if answer.lower() in ("yes", "no"):
                try:
                    radio = page.get_by_label(re.compile(f"^{answer}$", re.I)).first
                    if radio.count() > 0:
                        radio.check()
                except Exception:
                    pass
        except Exception:
            continue

    # submit
    submitted = False
    for name in ("Submit", "Apply", "Continue"):
        try:
            s = page.get_by_role("button", name=re.compile(name, re.I)).first
            if s.count() > 0 and s.is_visible():
                s.click()
                submitted = True
                break
        except Exception:
            continue
    page.wait_for_timeout(3000)

    if _first_matching(page, SELECTORS["success"]):
        return "applied", "application submitted"
    if _first_matching(page, SELECTORS["already_applied"]):
        return "already_applied", "site shows already applied"
    if not submitted:
        return "failed", "no submit button / unclear outcome"
    return "applied", "submitted (no explicit confirmation text found)"


# ------------------------------------------------------------------ CLI
def main() -> None:
    ap = argparse.ArgumentParser(description="Naukri one-time manual login")
    ap.add_argument("--headless", action="store_true", help="run headless (not for login)")
    args = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir()),
            headless=args.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = browser.pages[0] if browser.pages else browser.new_page()
        page.goto(BASE_URL, wait_until="domcontentloaded")
        ok = ensure_login(page, timeout_min=15)
        print("LOGIN OK — session saved." if ok else "LOGIN NOT DETECTED — try again.")
        browser.close()


if __name__ == "__main__":
    main()
