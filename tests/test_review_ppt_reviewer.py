from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

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


def test_cross_candidate_rejects_explicit_different_years_even_if_model_flags_match():
    document = PptReviewDocument(
        filename="经营汇报.pptx",
        page_count=2,
        slides=(
            PptSlide(
                slide_number=1,
                elements=(
                    PptElement(
                        element_id="slide:1/shape:1",
                        slide_number=1,
                        kind="text",
                        text="2023年客户100万户",
                    ),
                ),
            ),
            PptSlide(
                slide_number=2,
                elements=(
                    PptElement(
                        element_id="slide:2/shape:1",
                        slide_number=2,
                        kind="text",
                        text="2024年客户120万户",
                    ),
                ),
            ),
        ),
    )
    candidate = PptCrossCandidate(
        category="data_inconsistency",
        slide_number=1,
        element_id="slide:1/shape:1",
        target_text="2023年客户100万户",
        related_slide_number=2,
        related_element_id="slide:2/shape:1",
        related_text="2024年客户120万户",
        description="客户数前后不一致",
        same_subject=True,
        same_time_scope=True,
        same_metric_scope=True,
    )

    assert validate_cross_candidate(document, candidate) is None


def test_cross_candidate_uses_element_context_when_candidate_omits_year():
    document = PptReviewDocument(
        filename="经营汇报.pptx",
        page_count=2,
        slides=(
            PptSlide(
                1,
                (PptElement("slide:1/shape:1", 1, "text", "2023年\n客户100万户"),),
            ),
            PptSlide(
                2,
                (PptElement("slide:2/shape:1", 2, "text", "2024年\n客户120万户"),),
            ),
        ),
    )
    candidate = PptCrossCandidate(
        category="data_inconsistency",
        slide_number=1,
        element_id="slide:1/shape:1",
        target_text="客户100万户",
        related_slide_number=2,
        related_element_id="slide:2/shape:1",
        related_text="客户120万户",
        description="客户数前后不一致",
        same_subject=True,
        same_time_scope=True,
        same_metric_scope=True,
    )

    assert validate_cross_candidate(document, candidate) is None


@pytest.mark.parametrize(
    ("first_text", "second_text"),
    [
        ("上半年客户100万户", "下半年客户120万户"),
        ("客户100万户", "客户120户"),
        ("客户100 万户", "客户120 户"),
        ("目标客户100万户", "实际客户120万户"),
    ],
)
def test_cross_candidate_rejects_other_explicit_scope_conflicts(
    first_text: str,
    second_text: str,
):
    document = PptReviewDocument(
        filename="经营汇报.pptx",
        page_count=2,
        slides=(
            PptSlide(
                slide_number=1,
                elements=(
                    PptElement("slide:1/shape:1", 1, "text", first_text),
                ),
            ),
            PptSlide(
                slide_number=2,
                elements=(
                    PptElement("slide:2/shape:1", 2, "text", second_text),
                ),
            ),
        ),
    )
    candidate = PptCrossCandidate(
        category="data_inconsistency",
        slide_number=1,
        element_id="slide:1/shape:1",
        target_text=first_text,
        related_slide_number=2,
        related_element_id="slide:2/shape:1",
        related_text=second_text,
        description="客户数前后不一致",
        same_subject=True,
        same_time_scope=True,
        same_metric_scope=True,
    )

    assert validate_cross_candidate(document, candidate) is None


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


def test_reviewer_removes_model_advice_from_issue_description():
    async def fake_runner(stage: str, _prompt: str) -> dict[str, object]:
        if stage == "language":
            return {
                "issues": [
                    {
                        "category": "grammar",
                        "slide_number": 1,
                        "element_id": "slide:1/shape:1",
                        "target_text": "持续不断提升",
                        "description": "‘持续’与‘不断’语义重复，建议修改为‘持续提升’",
                    }
                ]
            }
        return {"issues": []}

    result = asyncio.run(review_ppt_document(_document(), model_runner=fake_runner))

    assert result.findings[0].description == "‘持续’与‘不断’语义重复"
    assert "建议" not in result.findings[0].description
    assert "修改为" not in result.findings[0].description


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


def test_dedupe_keeps_distinct_cross_findings_with_same_first_source():
    first = PptFinding(
        rule_id="ppt-data-inconsistency",
        category="data_inconsistency",
        slide_number=1,
        element_id="slide:1/shape:1",
        target_text="客户100万户",
        description="客户数前后不一致",
        related_slide_number=2,
        related_element_id="slide:2/shape:1",
        related_text="客户120万户",
    )
    second = PptFinding(
        rule_id="ppt-data-inconsistency",
        category="data_inconsistency",
        slide_number=1,
        element_id="slide:1/shape:1",
        target_text="客户100万户",
        description="客户数前后不一致",
        related_slide_number=3,
        related_element_id="slide:3/shape:2",
        related_text="客户130万户",
    )

    assert dedupe_findings((first, second)) == (first, second)


def test_reviewer_resumes_completed_language_batches_from_progress(tmp_path):
    long_document = PptReviewDocument(
        filename="长材料.pptx",
        page_count=2,
        slides=(
            PptSlide(
                slide_number=1,
                elements=(
                    PptElement(
                        element_id="slide:1/shape:1",
                        slide_number=1,
                        kind="text",
                        text="甲" * 5000,
                    ),
                ),
            ),
            PptSlide(
                slide_number=2,
                elements=(
                    PptElement(
                        element_id="slide:2/shape:1",
                        slide_number=2,
                        kind="text",
                        text="乙" * 5000,
                    ),
                ),
            ),
        ),
    )
    first_calls: list[str] = []

    async def interrupted_runner(stage: str, prompt: str) -> dict[str, object]:
        first_calls.append(stage)
        if stage == "language" and "乙" in prompt:
            raise ConnectionError("second batch interrupted")
        return {"issues": []}

    progress_path = tmp_path / "ppt_review_progress.json"
    try:
        asyncio.run(
            review_ppt_document(
                long_document,
                model_runner=interrupted_runner,
                progress_path=progress_path,
                input_digest="stable-input",
            )
        )
    except ConnectionError:
        pass
    else:
        raise AssertionError("第二批模型调用中断时必须保留失败状态")

    resumed_prompts: list[str] = []

    async def resumed_runner(stage: str, prompt: str) -> dict[str, object]:
        resumed_prompts.append(f"{stage}:{prompt}")
        return {"issues": []}

    result = asyncio.run(
        review_ppt_document(
            long_document,
            model_runner=resumed_runner,
            progress_path=progress_path,
            input_digest="stable-input",
        )
    )

    assert result.consistency_complete is True
    assert first_calls == ["language", "language"]
    language_prompts = [
        item for item in resumed_prompts if item.startswith("language:")
    ]
    assert len(language_prompts) == 1
    assert "甲" not in "\n".join(language_prompts)
    assert "乙" in "\n".join(language_prompts)


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
