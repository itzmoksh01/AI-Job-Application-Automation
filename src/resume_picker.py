"""Pick the best-matching resume variant for a listing.

Variants re-emphasize real experience only — they never invent claims.
Falls back to the master resume when nothing clearly fits or a file is missing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from .config import PROJECT_ROOT, master_resume, project_path, resume_variants

# keyword -> variant key (first match wins, ordered by specificity)
RULES: list[tuple[str, list[str]]] = [
    ("prompt_engineering", ["prompt engineer", "prompt design", "prompting", "prompt specialist"]),
    ("ai_agent", ["ai agent", "agentic", "agent developer", "autonomous"]),
    ("ai_automation", ["automation", "workflow", "n8n", "zapier", "rpa", "chatbot"]),
    ("ai_video", ["video", "veo", "runway", "heygen", "pika", "kling", "sora", "filmora", "premiere"]),
    ("ai_content", ["content", "creative", "copywriting", "social media", "canva"]),
]


def pick_resume(cfg: Dict, title: str, description: str = "") -> tuple[str, Path]:
    """Return (variant_key, resolved Path)."""
    text = f"{title} {description}".lower()
    variants = resume_variants(cfg)
    for key, keywords in RULES:
        if any(k in text for k in keywords):
            rel = variants.get(key)
            if rel:
                p = project_path(rel)
                if p.exists():
                    return key, p
    # fallback: master resume
    master = project_path(master_resume(cfg))
    return "master", master
