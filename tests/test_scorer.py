"""Unit tests for the fit scorer. Run with:  python -m pytest  (or plain python)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scorer import eligibility_block, score_listing  # noqa


def test_prompt_engineer_scores_high():
    score, reason = score_listing(
        "Prompt Engineer",
        "Acme AI",
        "We need a prompt engineer skilled in LLM evaluation, LangChain, RAG, "
        "prompt design for GPT-4. 0-2 years experience. Rs 5-8 LPA. Remote.",
        "Remote",
    )
    assert score >= 70, f"expected >=70, got {score} ({reason})"


def test_senior_role_scores_lower():
    junior, _ = score_listing("Prompt Engineer", "Acme",
                             "Prompt engineer, LLM, RAG. 1-3 years. Rs 5-8 LPA.", "")
    senior, _ = score_listing("Senior Staff Prompt Architect", "Acme",
                             "Prompt engineer, LLM, RAG. 8-10 years experience.", "")
    assert junior > senior, f"junior {junior} should beat senior {senior}"


def test_equity_only_blocked():
    assert eligibility_block("Founding Engineer", "Equity only, no cash salary") is not None


def test_gender_restricted_blocked():
    assert eligibility_block("AI Engineer", "Female candidates only") is not None


def test_irrelevant_role_scores_low():
    score, _ = score_listing("Truck Driver", "Logistics Co",
                            "Drive trucks across state lines. 5 years driving.", "")
    assert score < 70, f"expected <70, got {score}"


if __name__ == "__main__":
    for name, fn in sorted({k: v for k, v in globals().items()
                            if k.startswith("test_")}.items()):
        fn()
        print(f"PASS {name}")
    print("All scorer tests passed.")
