from __future__ import annotations

import ast
import asyncio
from pathlib import Path

from app.review.ppt.evidence import (
    dedupe_findings,
    validate_cross_candidate,
    validate_local_candidate,
)
from app.review.ppt.models import (
    PptCrossCandidate,
    PptElement,
    PptFinding,
    PptLocalCandidate,
    PptReviewDocument,
    PptSlide,
)
from app.review.ppt.reviewer import parse_model_payload, review_ppt_document


def _document() -> PptReviewDocument:
    return PptReviewDocument(
        filename="经营汇报.pptx",
        page_count=3,
        slides=(
            PptSlide(
                slide_number=1,
                elements=(
                    PptElement(
                        element_id="slide:1/shape:1",
                        slide_number=1,
                        kind="text",
                        text="持续不断提升服务能力。客户100万户。",
                    ),
                ),
            ),
            PptSlide(
                slide_number=2,
                elements=(
                    PptElement(
                        element_id="slide:2/shape:1",
                        slide_number=2,
                        kind="table",
                        text="2024年客户\t120万户",
                    ),
                ),
            ),
            PptSlide(
                slide_number=3,
                elements=(
                    PptElement(
                        element_id="slide:3/shape:2",
                        slide_number=3,
                        kind="chart",
                        text="客户120万户",
                    ),
                ),
            ),
        ),
    )


def test_local_candidate_requires_exact_page_element_and_source_text():
    document = _document()
    valid = PptLocalCandidate(
        category="grammar",
        slide_number=1,
        element_id="slide:1/shape:1",
        target_text="持续不断提升",
        description="‘持续’与‘不断’语义重复",
    )
    fabricated = PptLocalCandidate(
        category="typo",
        slide_number=1,
        element_id="slide:1/shape:1",
        target_text="虚构原文",
        description="虚构问题",
    )

    assert validate_local_candidate(document, valid) == PptFinding(
        rule_id="ppt-grammar",
        category="grammar",
        slide_number=1,
        element_id="slide:1/shape:1",
        target_text="持续不断提升",
        description="‘持续’与‘不断’语义重复",
    )
    assert validate_local_candidate(document, fabricated) is None


def test_cross_candidate_requires_two_exact_sources_and_same_scope():
    document = _document()
    valid = PptCrossCandidate(
        category="data_inconsistency",
        slide_number=1,
        element_id="slide:1/shape:1",
        target_text="客户100万户",
        related_slide_number=3,
        related_element_id="slide:3/shape:2",
        related_text="客户120万户",
        description="同一统计口径的客户数前后不一致",
        same_subject=True,
        same_time_scope=True,
        same_metric_scope=True,
    )
    different_year = PptCrossCandidate(
        **{**valid.__dict__, "related_slide_number": 2, "related_element_id": "slide:2/shape:1",
           "related_text": "2024年客户\t120万户", "same_time_scope": False}
    )
    one_side_missing = PptCrossCandidate(
        **{**valid.__dict__, "related_text": "客户130万户"}
    )

    finding = validate_cross_candidate(document, valid)

    assert finding is not None
    assert finding.related_slide_number == 3
    assert finding.related_text == "客户120万户"
    assert validate_cross_candidate(document, different_year) is None
    assert validate_cross_candidate(document, one_side_missing) is None


def test_reviewer_keeps_real_candidates_and_discards_hallucinated_evidence():
    async def fake_runner(stage: str, _prompt: str) -> dict[str, object]:
        if stage == "language":
            return {
                "issues": [
                    {
                        "category": "grammar",
                        "slide_number": 1,
                        "element_id": "slide:1/shape:1",
                        "target_text": "持续不断提升",
                        "description": "‘持续’与‘不断’语义重复",
                    },
                    {
                        "category": "typo",
                        "slide_number": 99,
                        "element_id": "slide:99/shape:1",
                        "target_text": "虚构原文",
                        "description": "虚构问题",
                    },
                ]
            }
        return {
            "issues": [
                {
                    "category": "data_inconsistency",
                    "slide_number": 1,
                    "element_id": "slide:1/shape:1",
                    "target_text": "客户100万户",
                    "related_slide_number": 3,
                    "related_element_id": "slide:3/shape:2",
                    "related_text": "客户120万户",
                    "same_subject": True,
                    "same_time_scope": True,
                    "same_metric_scope": True,
                    "description": "同一统计口径的客户数前后不一致",
                }
            ]
        }

    result = asyncio.run(review_ppt_document(_document(), model_runner=fake_runner))

    assert {item.target_text for item in result.findings} == {
        "持续不断提升",
        "客户100万户",
    }
    assert result.consistency_complete is True


def test_consistency_failure_keeps_language_findings_and_marks_degraded():
    async def fake_runner(stage: str, _prompt: str) -> dict[str, object]:
        if stage == "consistency":
            raise ConnectionError("model unavailable")
        return {
            "issues": [
                {
                    "category": "grammar",
                    "slide_number": 1,
                    "element_id": "slide:1/shape:1",
                    "target_text": "持续不断提升",
                    "description": "语义重复",
                }
            ]
        }

    result = asyncio.run(review_ppt_document(_document(), model_runner=fake_runner))

    assert [item.target_text for item in result.findings] == ["持续不断提升"]
    assert result.consistency_complete is False


def test_parse_model_payload_accepts_json_fence_and_rejects_non_issue_list():
    assert parse_model_payload('```json\n{"issues": []}\n```') == {"issues": []}

    try:
        parse_model_payload('{"issues": "invalid"}')
    except ValueError as exc:
        assert "输出格式无效" in str(exc)
    else:
        raise AssertionError("无效模型输出必须被拒绝")


def test_dedupe_prefers_data_issue_over_generic_grammar_at_same_source():
    grammar = PptFinding(
        rule_id="ppt-grammar",
        category="grammar",
        slide_number=1,
        element_id="slide:1/shape:1",
        target_text="客户100万户",
        description="表述有问题",
    )
    data = PptFinding(
        rule_id="ppt-data-inconsistency",
        category="data_inconsistency",
        slide_number=1,
        element_id="slide:1/shape:1",
        target_text="客户100万户",
        description="数据前后不一致",
        related_slide_number=3,
        related_element_id="slide:3/shape:2",
        related_text="客户120万户",
    )

    assert dedupe_findings((grammar, data)) == (data,)


def test_ppt_package_does_not_import_other_review_engines():
    forbidden = {
        "app.review.general_reviewer",
        "app.review.reviewer",
        "app.review.halfmonthly_reviewer",
        "app.review.official_format_checker",
        "app.review.format_checker",
        "app.review.general_rule_checker",
    }
    imported: set[str] = set()
    for path in Path("app/review/ppt").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

    assert not (imported & forbidden)
