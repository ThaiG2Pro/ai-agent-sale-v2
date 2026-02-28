"""Context compression: deduplication, low-confidence filtering,
near-duplicate removal."""

from difflib import SequenceMatcher
from typing import Any

from services.rag.constants import COMPRESSION_SCORE_THRESHOLD, NEAR_DUP_THRESHOLD


def _overlap_ratio(a: str, b: str) -> float:
    """Longest-common-subsequence string similarity ratio."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def compress_context(
    chunks: list[dict[str, Any]],
    score_threshold: float = COMPRESSION_SCORE_THRESHOLD,
    overlap_threshold: float = NEAR_DUP_THRESHOLD,
) -> list[dict[str, Any]]:
    """
    Why this exists: Reduce token count by 20-40% (FR-012).
    Algorithm:
      Step 1: Deduplicate by description text.
      Step 2: Filter low-signal chunks (vector_score < 0.5).
      Step 3: Remove >80% near-duplicates (preserve highest rrf_score).
    """
    # Step 1: dedup by description text
    seen = set()
    step1 = []
    for chunk in chunks:
        txt = (chunk.get("description") or "").strip()
        if txt not in seen:
            seen.add(txt)
            step1.append(chunk)

    # Step 2: low-confidence filter (vector cosine similarity)
    step2 = [c for c in step1 if c.get("vector_score", 0.0) >= score_threshold]

    # Step 3: near-duplicate removal (preserve highest rrf_score)
    step2_sorted = sorted(step2, key=lambda c: c.get("rrf_score", 0.0), reverse=True)
    step3: list[dict[str, Any]] = []
    for candidate in step2_sorted:
        ctext = (candidate.get("description") or "").strip()
        is_dup = any(
            _overlap_ratio(ctext, (kept.get("description") or "").strip())
            > overlap_threshold
            for kept in step3
        )
        if not is_dup:
            step3.append(candidate)

    return step3
