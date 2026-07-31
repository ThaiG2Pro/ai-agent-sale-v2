"""Why this exists: Validates eval CLI structure and gold dataset integrity.
What it does: Tests gold_dataset.json schema, Likert scale config, and CLI args.
"""

from __future__ import annotations

import json
from pathlib import Path

GOLD_DATASET_PATH = Path("tests/eval/gold_dataset.json")


class TestGoldDatasetSchema:
    """US5: Gold dataset must have required fields for evaluation."""

    def test_gold_dataset_exists(self):
        assert GOLD_DATASET_PATH.exists()

    def test_gold_dataset_is_valid_json(self):
        data = json.loads(GOLD_DATASET_PATH.read_text())
        assert isinstance(data, list)
        assert len(data) > 0

    def test_all_items_have_required_fields(self):
        # Gold dataset v2 (WP-V2-0): intent/difficulty were dropped in favour of
        # deterministic expectations (expected_skus / must_decline / …) graded
        # by scripts/eval_gate.py.
        data = json.loads(GOLD_DATASET_PATH.read_text())
        required = {"id", "query", "category", "language", "expected_keywords"}
        for item in data:
            missing = required - set(item.keys())
            assert not missing, f"{item['id']} missing: {missing}"

    def test_all_items_have_language_field(self):
        data = json.loads(GOLD_DATASET_PATH.read_text())
        for item in data:
            assert "language" in item, f"{item['id']} missing language"
            assert item["language"] in ("en", "vi")

    def test_all_items_have_deterministic_expectation(self):
        # v2 invariant: every case must be gradable without an LLM judge.
        data = json.loads(GOLD_DATASET_PATH.read_text())
        for item in data:
            graded = bool(
                item.get("expected_skus")
                or item.get("must_decline")
                or item.get("expected_price")
                or item.get("absent_terms")
            )
            assert graded, f"{item['id']} has no deterministic expectation"

    def test_ids_are_unique(self):
        data = json.loads(GOLD_DATASET_PATH.read_text())
        ids = [item["id"] for item in data]
        assert len(ids) == len(set(ids))

    def test_dataset_has_both_languages(self):
        data = json.loads(GOLD_DATASET_PATH.read_text())
        languages = {item["language"] for item in data}
        assert "en" in languages
        assert "vi" in languages
