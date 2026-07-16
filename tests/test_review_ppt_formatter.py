from __future__ import annotations

from app.review.ppt.formatter import format_ppt_review_messages
from app.review.ppt.models import PptFinding, PptReviewResult


def test_formatter_returns_facts_without_suggestions():
    result = PptReviewResult(
        filename="经营汇报.pptx",
        page_count=12,
        findings=(
            PptFinding(
                rule_id="ppt-grammar",
                category="grammar",
                slide_number=3,
                element_id="slide:3/shape:1",
                target_text="持续不断提升",
                description="‘持续’与‘不断’语义重复",
            ),
            PptFinding(
                rule_id="ppt-data-inconsistency",
                category="data_inconsistency",
                slide_number=4,
                element_id="slide:4/shape:2",
                target_text="客户100万户",
                description="同一统计口径的客户数前后不一致",
                related_slide_number=12,
                related_element_id="slide:12/shape:3",
                related_text="客户120万户",
            ),
        ),
    )

    messages = format_ppt_review_messages(result)
    joined = "\n".join(messages)

    assert "【第3页｜语病】" in joined
    assert "【第4页 ↔ 第12页｜数据不一致】" in joined
    assert "原文一：客户100万户" in joined
    assert "原文二：客户120万户" in joined
    assert "问题：同一统计口径的客户数前后不一致" in joined
    assert "建议" not in joined
    assert "修改为" not in joined


def test_formatter_splits_long_results_with_continuous_numbers():
    findings = tuple(
        PptFinding(
            rule_id="ppt-grammar",
            category="grammar",
            slide_number=number,
            element_id=f"slide:{number}/shape:1",
            target_text=f"第{number}处原文",
            description="存在明确语病",
        )
        for number in range(1, 31)
    )
    result = PptReviewResult(
        filename="经营汇报.pptx",
        page_count=30,
        findings=findings,
    )

    messages = format_ppt_review_messages(result, max_chars=600)

    assert len(messages) > 1
    assert "1.【" in messages[0]
    assert "30.【" in messages[-1]
    assert all(len(message) <= 600 for message in messages)


def test_formatter_reports_consistency_degradation_without_advice():
    result = PptReviewResult(
        filename="经营汇报.pptx",
        page_count=3,
        findings=(),
        consistency_complete=False,
    )

    message, = format_ppt_review_messages(result)

    assert "已完成文字检查，但全篇一致性检查未完成" in message
    assert "建议" not in message


def test_formatter_removes_advice_even_from_prebuilt_finding():
    result = PptReviewResult(
        filename="经营汇报.pptx",
        page_count=1,
        findings=(
            PptFinding(
                rule_id="ppt-grammar",
                category="grammar",
                slide_number=1,
                element_id="slide:1/shape:1",
                target_text="持续不断提升",
                description="语义重复，建议修改为持续提升",
            ),
        ),
    )

    joined = "\n".join(format_ppt_review_messages(result))

    assert "问题：语义重复" in joined
    assert "建议" not in joined
    assert "修改为" not in joined


def test_formatter_surfaces_parser_warnings_and_avoids_complete_pass_claim():
    result = PptReviewResult(
        filename="经营汇报.pptx",
        page_count=2,
        findings=(),
        warnings=("第2页图表数据未完整读取",),
    )

    message, = format_ppt_review_messages(result)

    assert "未发现低级文字或内部一致性问题" not in message
    assert "已成功读取范围内" in message
    assert "第2页图表数据未完整读取" in message
