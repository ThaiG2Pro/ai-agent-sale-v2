"""Query understanding: classification and normalization."""

from typing import Literal

from services.rag.constants import ACTION_VERBS


def classify_query(query: str) -> Literal["short", "long", "ambiguous"]:
    """
    Why this exists: Drives adaptive TopK for cost efficiency (FR-015).
    Rules (deterministic, word-count + keyword heuristic):
    - short:    word_count ≤ 8  (raised from 5 — Vietnamese price queries are 6-8 words)
    - long:     9 ≤ word_count ≤ 15
    - ambiguous: word_count > 15 AND (no action verb AND no capitalised proper noun)
    - >15 words with action verb or proper noun → "long" (safe fallback, TopK 15)
    """
    words = query.split()
    wc = len(words)
    if wc <= 8:
        return "short"
    if wc <= 15:
        return "long"
    has_action_verb = any(
        word.lower().strip("?,.:;!") in ACTION_VERBS for word in words
    )
    has_proper_noun = any(word[0].isupper() for word in words if len(word) > 1)
    if has_action_verb or has_proper_noun:
        return "long"
    return "ambiguous"


def compute_adaptive_topk(query: str, intent: str | None = None) -> int:
    """
    Why this exists: Cost efficiency (FR-015).
    Intent-first override (reduces tokens for focused queries):
      PRICING / INFO_QUERY → 5  (single product, specific fact)
      COMPARISON           → 10 (two products, cross-match needed)
    Fallback by word-count: short → 5, long → 15, ambiguous → 20.
    """
    if intent in ("PRICING", "INFO_QUERY"):
        return 5
    if intent == "COMPARISON":
        return 10
    return {"short": 5, "long": 15, "ambiguous": 20}[classify_query(query)]
