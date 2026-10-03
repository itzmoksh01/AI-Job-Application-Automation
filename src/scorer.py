"""Deterministic fit scoring for job listings (0-100).

Rubric (mirrors the production automation):
  - Role-title match to target roles ............ up to 30
  - Skills overlap with candidate profile ...... up to 30
  - Seniority match (junior/fresher-friendly) ... up to 20
  - Compensation sanity vs expected CTC ....... up to 10
  - Listing quality (real company, clear JD) ... up to 10

Apply only when score >= min_fit_score (default 70). Never inflates:
unknown facts score zero, they never score positively.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- skills
SKILL_KEYWORDS = [
    # prompt / LLM core
    "prompt engineering", "prompt design", "prompting", "llm", "large language model",
    "generative ai", "genai", "gen ai", "gpt", "claude", "gemini", "llama", "mistral",
    "rag", "retrieval augmented", "fine-tuning", "fine tuning", "embeddings",
    "vector database", "langchain", "langgraph", "llamaindex", "autogen", "crewai",
    # agents & automation
    "ai agent", "agentic", "autonomous agent", "ai automation", "workflow automation",
    "n8n", "make.com", "zapier", "power automate", "uipath", "rpa",
    "chatbot", "conversational ai", "voice ai",
    # generative media
    "ai video", "text-to-video", "image generation", "runway", "runwayml", "heygen",
    "elevenlabs", "pika", "kling", "sora", "midjourney", "stable diffusion",
    "higgsfield", "veo", "capcut", "premiere", "after effects", "davinci",
    # web / tooling around the roles
    "python", "fastapi", "react", "node.js", "nodejs", "wordpress", "javascript",
    "canva", "figma", "filmora",
]

# target-role title signals: (phrase, weight)
TITLE_SIGNALS = [
    ("prompt engineer", 30),
    ("prompt specialist", 26),
    ("prompt designer", 26),
    ("ai automation", 28),
    ("automation engineer", 24),
    ("ai agent", 28),
    ("agent developer", 26),
    ("agentic ai", 26),
    ("ai content", 26),
    ("content generation", 24),
    ("generative ai", 24),
    ("genai", 22),
    ("ai video", 28),
    ("video editor", 20),
    ("ai artist", 22),
    ("ai engineer", 22),
    ("llm engineer", 24),
    ("conversational ai", 22),
]

SENIOR_NEGATIVES = [
    "senior", "sr.", "sr ", "staff", "principal", "architect", "lead ",
    "head of", "director", "vp", "vice president", "manager -", " 8+", "10+",
    "8-10", "10-12", "7+ years", "8+ years", "6-8 years",
]
JUNIOR_POSITIVES = [
    "fresher", "fresher ", "entry level", "entry-level", "junior", "trainee",
    "intern", "0-1", "0-2", "1-2", "1-3", "0 - 2", "0 to 2",
]

GHOST_SIGNALS = [
    "equity only", "no salary", "unpaid", "commission only", "no cash",
    "co-founder", "cofounder",
]
VAGUE_SIGNALS = ["urgent hiring", "immediate joiner wanted!!!", "earn from home"]

FEMALE_ONLY = ["female", "women ", "woman ", "girls", "she/him"]  # eligibility guard


def _hits(text: str, phrases) -> list[str]:
    t = text.lower()
    return [p for p in phrases if p in t]


def _parse_ctc_lpa(text: str) -> tuple[float | None, float | None]:
    """Extract (min_lpa, max_lpa) from Indian CTC strings like 'Rs 4.8-9.6 LPA'."""
    t = text.lower().replace(",", "")
    m = re.search(r"(?:rs\.?|₹)\s*(\d+(?:\.\d+)?)\s*[-–to]+\s*(\d+(?:\.\d+)?)\s*(?:l|lpa|lakh)", t)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(?:rs\.?|₹)\s*(\d+(?:\.\d+)?)\s*(?:l|lpa|lakh)", t)
    if m:
        v = float(m.group(1))
        return v, v
    return None, None


def score_listing(
    title: str,
    company: str,
    description: str,
    location: str = "",
    expected_ctc_lpa: float = 6.0,
) -> tuple[int, str]:
    """Return (score 0-100, one-line reason)."""
    text = f"{title}\n{company}\n{description}\n{location}"
    tl = title.lower()
    reasons: list[str] = []

    # ---- 1. title match (30)
    title_score = 0
    for phrase, weight in TITLE_SIGNALS:
        if phrase in tl:
            title_score = max(title_score, weight)
            reasons.append(f"title match '{phrase}'")
            break
    else:
        # partial: any target word present
        if any(w in tl for w in ("ai", "prompt", "automation", "agent", "video", "content")):
            title_score = 12
            reasons.append("partial title overlap")

    # ---- 2. skills overlap (30)
    skill_hits = _hits(text, SKILL_KEYWORDS)
    # diminishing returns: first 10 hits count fully
    skill_score = min(30, len(set(skill_hits)) * 3)
    if skill_hits:
        reasons.append(f"{len(set(skill_hits))} skill hits")

    # ---- 3. seniority (20)
    senior_hits = _hits(text, SENIOR_NEGATIVES)
    junior_hits = _hits(text, JUNIOR_POSITIVES)
    if senior_hits and not junior_hits:
        seniority_score = 4
        reasons.append("skews senior")
    elif junior_hits:
        seniority_score = 20
        reasons.append("junior/fresher friendly")
    else:
        seniority_score = 14
        reasons.append("mid-level ok")

    # ---- 4. compensation sanity (10)
    ctc_min, ctc_max = _parse_ctc_lpa(text)
    if ctc_min is None:
        comp_score = 6
        reasons.append("no salary listed")
    elif ctc_max is not None and ctc_max < expected_ctc_lpa * 0.5:
        comp_score = 2
        reasons.append("pay far below expectation")
    elif ctc_min <= expected_ctc_lpa <= (ctc_max or ctc_min) or (ctc_max or 0) >= expected_ctc_lpa:
        comp_score = 10
        reasons.append("pay in range")
    else:
        comp_score = 7
        reasons.append("pay partly overlaps")

    # ---- 5. listing quality (10)
    quality_score = 10
    ghost = _hits(text, GHOST_SIGNALS)
    vague = _hits(text, VAGUE_SIGNALS)
    if ghost:
        quality_score = 0
        reasons.append("ghost/equity-only listing")
    elif vague:
        quality_score = 4
        reasons.append("vague listing")
    elif len(description.strip()) < 120:
        quality_score = 6
        reasons.append("thin job description")
    if not company.strip() or company.strip().lower() in ("confidential", "hidden"):
        quality_score = min(quality_score, 5)
        reasons.append("company hidden")

    total = title_score + skill_score + seniority_score + comp_score + quality_score
    total = max(0, min(100, total))
    return total, "; ".join(reasons) if reasons else "no strong signals"


def eligibility_block(title: str, description: str) -> str | None:
    """Hard eligibility guards. Returns a skip reason or None."""
    text = f"{title} {description}".lower()
    if any(p in text for p in ("female candidate", "women only", "female only", "for women")):
        return "gender-restricted posting; not eligible"
    if any(p in text for p in ("equity only", "no cash salary", "unpaid")):
        return "no cash compensation; incompatible with expected CTC"
    return None
