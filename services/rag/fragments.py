"""Why this exists: WP-V2-2 fragment-level citations (FR-011) — a chunk-level
citation tells the reviewer WHICH document grounded the answer, not WHERE.
What it does: Pure-string (SequenceMatcher, no LLM call) extraction of the
single source sentence that best matches the generated answer, attached to
each citation dict as the optional `fragment_text` field.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

# Sentence-ish splitter: sentences end at . ! ? or a newline (source_text is
# "[sku] name\nprice line\ndescription" — the \n boundaries matter).
_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?")

# Below this best-match ratio the "fragment" is noise, not grounding evidence.
MIN_FRAGMENT_RATIO = 0.35


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.findall(text) if s.strip()]


def extract_fragment(answer: str, source_text: str) -> str | None:
    """Return the source sentence most similar to any answer sentence.

    Deterministic and cheap (SequenceMatcher — same pattern as
    compression._overlap_ratio). Returns None when nothing clears
    MIN_FRAGMENT_RATIO so callers can keep the field absent-by-default.
    """
    if not answer or not source_text:
        return None

    answer_sents = _sentences(answer)
    source_sents = _sentences(source_text)
    if not answer_sents or not source_sents:
        return None

    best_ratio = 0.0
    best_sent: str | None = None
    for src in source_sents:
        for ans in answer_sents:
            ratio = SequenceMatcher(None, src.lower(), ans.lower()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_sent = src

    if best_ratio < MIN_FRAGMENT_RATIO:
        return None
    return best_sent


def annotate_fragments(citations: list[dict[str, Any]], answer: str) -> list[dict[str, Any]]:
    """Return copies of the citation dicts with `fragment_text` filled in.

    Non-mutating (retrieval results may be shared across retry attempts).
    A citation whose source has no matching sentence gets fragment_text=None —
    the key is always present so cached and live payloads have one shape.
    """
    annotated = []
    for citation in citations:
        copy = dict(citation)
        copy["fragment_text"] = extract_fragment(answer, citation.get("source_text") or "")
        annotated.append(copy)
    return annotated
