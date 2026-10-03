"""Gmail interview radar: detect recruiter/interview mail and save reply drafts.

Setup (one time):
    1. Create a Google Cloud project, enable the Gmail API.
    2. Create OAuth "Desktop app" credentials, download as credentials.json
       into the project root (see .env.example).
    3. Run:  python -m src.gmail_radar --auth
       (opens a browser once; stores token.json — never commit it)

Then schedule:  python -m src.gmail_radar --scan
Drafts are NEVER sent automatically — you approve every reply.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from .config import PROJECT_ROOT

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

SEEN_FILE = PROJECT_ROOT / "data" / "gmail_radar_seen.json"

SIGNAL_QUERIES = [
    "subject:interview newer_than:2h",
    "subject:shortlisted newer_than:2h",
    'subject:"next steps" newer_than:2h',
    "subject:offer newer_than:2h",
    'subject:"screening call" newer_than:2h',
    "from:naukri.com newer_than:2h",
]

VERIFY_KEYWORDS = [
    "interview", "shortlist", "screening", "next steps", "offer letter",
    "assessment", "technical round", "hr round",
]
NOISE_SIGNALS = ["unsubscribe", "newsletter", "promotion", "discount", "sale "]


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    token = PROJECT_ROOT / "token.json"
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(PROJECT_ROOT / "credentials.json"), SCOPES)
            creds = flow.run_local_server(port=0)
        token.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text() or "[]"))
    return set()


def save_seen(seen: set) -> None:
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(sorted(seen)))


def _get(service, msg_id: str) -> dict:
    return service.users().messages().get(
        userId="me", id=msg_id, format="full").execute()


def _headers(msg: dict) -> dict:
    return {h["name"].lower(): h["value"]
            for h in msg["payload"].get("headers", [])}


def _body_text(msg: dict) -> str:
    def walk(part) -> str:
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        if mime == "text/plain" and body.get("data"):
            return base64.urlsafe_b64decode(body["data"]).decode("utf-8", "ignore")
        for sub in part.get("parts", []) or []:
            t = walk(sub)
            if t:
                return t
        return ""
    return walk(msg["payload"])


def verify(msg: dict) -> bool:
    """Heuristic check: genuinely about the user's job hunt, not noise."""
    heads = _headers(msg)
    subject = heads.get("subject", "")
    frm = heads.get("from", "")
    text = f"{subject} {_body_text(msg)[:2000]}".lower()
    if not any(k in text for k in VERIFY_KEYWORDS):
        return False
    if any(n in text for n in NOISE_SIGNALS):
        return False
    if "list-unsubscribe" in heads:
        return False  # bulk mail
    return True


def draft_reply(service, to_addr: str, subject: str, body: str,
               thread_id: str | None = None) -> dict:
    msg = EmailMessage()
    msg["To"] = to_addr
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    payload: dict = {"message": {"raw": raw}}
    if thread_id:
        payload["message"]["threadId"] = thread_id
    return service.users().drafts().create(userId="me", body=payload).execute()


DRAFT_TEMPLATE = """Hi {name},

Thank you for reaching out regarding the {role} opportunity. I am very interested and would love to discuss the next steps.

A quick note on my background: I am a Generative AI Specialist with hands-on experience in prompt engineering, AI automation, AI agents, and AI-driven content and video generation.

Please let me know a convenient time for a screening call, or share the process and timelines involved.

Looking forward to your response.

Best regards,
{sender}
"""


def scan(service, sender_name: str, dry_run: bool = False) -> list[dict]:
    seen = load_seen()
    hits: list[dict] = []
    for q in SIGNAL_QUERIES:
        try:
            res = service.users().messages().list(
                userId="me", q=q, maxResults=10).execute()
        except Exception as e:
            print(f"query failed ({q}): {e}")
            continue
        for m in res.get("messages", []) or []:
            mid = m["id"]
            if mid in seen:
                continue
            full = _get(service, mid)
            if not verify(full):
                seen.add(mid)
                continue
            heads = _headers(full)
            frm = heads.get("from", "")
            subject = heads.get("subject", "(no subject)")
            # naive name/role extraction for the draft
            name = frm.split("<")[0].strip().strip('"') or "there"
            reply_body = DRAFT_TEMPLATE.format(
                name=name, role=subject, sender=sender_name)
            if not dry_run:
                draft_reply(service, frm, subject, reply_body,
                            thread_id=full.get("threadId"))
            seen.add(mid)
            hits.append({"from": frm, "subject": subject, "id": mid})
    save_seen(seen)
    return hits


def main() -> None:
    ap = argparse.ArgumentParser(description="Gmail interview radar")
    ap.add_argument("--auth", action="store_true", help="one-time OAuth login")
    ap.add_argument("--scan", action="store_true", help="scan recent mail, save drafts")
    ap.add_argument("--dry-run", action="store_true", help="scan without creating drafts")
    ap.add_argument("--sender", default=os.environ.get("RADAR_SENDER", "Abhishek Goswami"))
    args = ap.parse_args()

    service = _service()
    if args.auth:
        print("Gmail connected. token.json saved (keep it private).")
        return
    if args.scan or args.dry_run:
        hits = scan(service, args.sender, dry_run=args.dry_run)
        if hits:
            print(f"ALERT: {len(hits)} interview-related email(s):")
            for h in hits:
                print(f" - {h['subject']}  <{h['from']}>")
            if not args.dry_run:
                print("Reply drafts saved (not sent). Review them in Gmail Drafts.")
        else:
            print("No interview activity.")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
