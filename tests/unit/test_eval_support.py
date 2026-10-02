"""Pure grading rules of scripts/eval_support.py."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.eval_support import GOLD_DATASET_PATH, grade_support_f


def test_dataset_is_well_formed():
    cases = json.loads(Path(GOLD_DATASET_PATH).read_text())
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)) and len(cases) >= 20
    for c in cases:
        assert c["query"] and c["category"]
        for sku in c.get("expected_skus", []):
            assert sku.startswith("spacely-")


def test_plain_case_needs_answer_and_citation():
    case = {"expected_skus": ["spacely-quen-mk"]}
    assert grade_support_f(case, "Vào trang đăng nhập…", False, "INFO_QUERY", ["spacely-quen-mk"])[
        "passed"
    ]
    assert not grade_support_f(case, "…", False, "INFO_QUERY", ["spacely-xoa-tk"])["passed"]
    assert not grade_support_f(
        case, "Mình chưa có thông tin", True, "INFO_QUERY", ["spacely-quen-mk"]
    )["passed"]


def test_out_of_scope_must_decline_or_redirect_and_avoid_shop_words():
    case = {"must_decline": True, "forbidden_terms": ["đặt hàng"]}
    assert grade_support_f(case, "Mình chưa có thông tin…", True, "INFO_QUERY", [])["passed"]
    # polite redirect without a decline flag (SMALLTALK path) also passes
    assert grade_support_f(case, "Mình chỉ hỗ trợ về Spacely thôi nhé.", False, "SMALLTALK", [])[
        "passed"
    ]
    assert grade_support_f(
        case, "Spacely không bán iPhone hay sản phẩm khác.", False, "INFO_QUERY", []
    )["passed"]
    assert not grade_support_f(case, "Có, mình hỗ trợ đặt hàng", False, "INFO_QUERY", [])["passed"]
    assert not grade_support_f(case, "iPhone 15 giá 30 triệu.", False, "INFO_QUERY", [])["passed"]


def test_complaint_rules():
    case = {
        "expected_intent": "COMPLAINT",
        "required_any": ["liên hệ hỗ trợ"],
        "forbidden_terms": ["sẽ hoàn tiền"],
    }
    good = "Mình xin lỗi. Bạn bấm 'Liên hệ hỗ trợ' để đội hỗ trợ xử lý nhé."
    bad = "Mình xin lỗi, mình sẽ hoàn tiền cho bạn ngay."
    assert grade_support_f(case, good, False, "COMPLAINT", [])["passed"]
    g = grade_support_f(case, bad, False, "COMPLAINT", [])
    assert not g["passed"] and g["checks"] == {
        "intent": True,
        "required_any": False,
        "no_forbidden": False,
    }
    assert not grade_support_f(case, good, False, "INFO_QUERY", [])["passed"]


def test_no_citation_check_when_other_rules_apply():
    case = {"expected_intent": "SMALLTALK", "required_any": ["spacely"]}
    assert grade_support_f(case, "Mình là trợ lý Spacely", False, "SMALLTALK", [])["passed"]
