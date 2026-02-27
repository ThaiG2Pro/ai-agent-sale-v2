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
        data = json.loads(GOLD_DATASET_PATH.read_text())
        required = {"id", "query", "category", "intent", "expected_keywords"}
        for item in data:
            missing = required - set(item.keys())
            assert not missing, f"{item['id']} missing: {missing}"

    def test_all_items_have_language_field(self):
        data = json.loads(GOLD_DATASET_PATH.read_text())
        for item in data:
            assert "language" in item, f"{item['id']} missing language"
            assert item["language"] in ("en", "vi")

    def test_all_items_have_difficulty_field(self):
        data = json.loads(GOLD_DATASET_PATH.read_text())
        for item in data:
            assert "difficulty" in item, f"{item['id']} missing difficulty"
            assert item["difficulty"] in ("easy", "medium", "hard")

    def test_ids_are_unique(self):
        data = json.loads(GOLD_DATASET_PATH.read_text())
        ids = [item["id"] for item in data]
        assert len(ids) == len(set(ids))

    def test_dataset_has_both_languages(self):
        data = json.loads(GOLD_DATASET_PATH.read_text())
        languages = {item["language"] for item in data}
        assert "en" in languages
        assert "vi" in languages


class TestEvalCLIStructure:
    """US5: Eval script must support expected CLI arguments."""

    def test_eval_script_exists(self):
        assert Path("scripts/tier1_eval.py").exists()

    def test_eval_script_has_skip_tier2_flag(self):
        content = Path("scripts/tier1_eval.py").read_text()
        assert "--skip-tier2" in content

    def test_eval_script_has_verbose_flag(self):
        content = Path("scripts/tier1_eval.py").read_text()
        assert "--verbose" in content

    def test_likert_scale_defined(self):
        content = Path("scripts/tier1_eval.py").read_text()
        for score in ["1 =", "2 =", "3 =", "4 =", "5 ="]:
            assert score in content, f"Likert {score} not in eval script"
