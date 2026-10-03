"""Configuration loading for the job application automation.

Reads config.yaml, resolves resume paths relative to the project root,
and exposes a small typed view over the raw dict.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | Path | None = None) -> Dict[str, Any]:
    cfg_path = Path(path) if path else PROJECT_ROOT / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    return cfg


def project_path(relative: str | Path) -> Path:
    """Resolve a config-relative path against the project root."""
    p = Path(relative)
    return p if p.is_absolute() else PROJECT_ROOT / p


def candidate(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return cfg.get("candidate", {})


def automation(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return cfg.get("automation", {})


def resume_variants(cfg: Dict[str, Any]) -> Dict[str, str]:
    return (cfg.get("resumes", {}) or {}).get("variants", {}) or {}


def master_resume(cfg: Dict[str, Any]) -> str:
    return (cfg.get("resumes", {}) or {}).get("master", "resumes/master_resume.pdf")


def active_window(cfg: Dict[str, Any]) -> tuple[str, str]:
    w = automation(cfg).get("active_window", {}) or {}
    return w.get("start", "10:00"), w.get("end", "18:00")


def timezone_name(cfg: Dict[str, Any]) -> str:
    return automation(cfg).get("timezone", os.environ.get("TIMEZONE", "Asia/Kolkata"))


def db_path(cfg: Dict[str, Any] | None = None) -> Path:
    return Path(os.environ.get("APP_DB", PROJECT_ROOT / "data" / "applications.db"))
