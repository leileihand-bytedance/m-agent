from __future__ import annotations

from app.review.ppt.models import PptElement, PptReviewDocument, PptSlide
from app.review.ppt.rules import check_ppt_rules


def _document_with_elements(*texts: str) -> PptReviewDocument:
    elements = tuple(
        PptElement(
            element_id=f"slide:1/shape:{index}",
            slide_number=1,
            kind="text",
            text=text,
        )
        for index, text in enumerate(texts, 1)
    )
    return PptReviewDocument(
        filename="测试.pptx",
        page_count=1,
        slides=(PptSlide(slide_number=1, elements=elements),),
    )


def test_sequence_rule_detects_skip_inside_one_element():
    document = _document_with_elements("1、背景\n2、做法\n4、成效")

    findings = check_ppt_rules(document)

    assert [(item.rule_id, item.target_text) for item in findings] == [
        ("ppt-sequence-skip", "4、"),
    ]
    assert findings[0].slide_number == 1
    assert findings[0].element_id == "slide:1/shape:1"
    assert findings[0].description == "同一组序号由2跳到4"


def test_sequence_rule_detects_duplicate_inside_one_element():
    document = _document_with_elements("1. 背景\n2. 做法\n2. 成效")

    findings = check_ppt_rules(document)

    assert [(item.rule_id, item.target_text) for item in findings] == [
        ("ppt-sequence-duplicate", "2."),
    ]


def test_sequence_rule_detects_chinese_ordinal_skip():
    document = _document_with_elements("一、背景\n二、做法\n四、成效")

    findings = check_ppt_rules(document)

    assert [(item.rule_id, item.target_text) for item in findings] == [
        ("ppt-sequence-skip", "四、"),
    ]


def test_sequence_rule_detects_reverse_order():
    document = _document_with_elements("3、附录\n2、结语")

    findings = check_ppt_rules(document)

    assert [(item.rule_id, item.target_text) for item in findings] == [
        ("ppt-sequence-reverse", "2、"),
    ]


def test_sequence_rule_does_not_join_different_elements_or_decimal_values():
    document = _document_with_elements(
        "1、背景\n2、做法",
        "4、附录",
        "增长1.5个百分点",
    )

    findings = check_ppt_rules(document)

    assert not [item for item in findings if item.category == "sequence"]


def test_sequence_rule_does_not_treat_line_starting_decimals_as_ordinals():
    document = _document_with_elements("1.5个百分点\n1.6个百分点")

    assert not [
        item for item in check_ppt_rules(document) if item.category == "sequence"
    ]


def test_sequence_rule_tracks_nested_levels_independently():
    document = _document_with_elements("1、一级\n  1、二级\n2、一级")

    assert not [
        item for item in check_ppt_rules(document) if item.category == "sequence"
    ]


def test_sequence_rule_resets_after_non_list_text_between_independent_lists():
    document = _document_with_elements("1、第一组\n2、第一组\n说明文字\n1、第二组\n2、第二组")

    assert not [
        item for item in check_ppt_rules(document) if item.category == "sequence"
    ]


def test_sequence_rule_resets_after_blank_line_between_independent_lists():
    document = _document_with_elements("1、第一组\n2、第一组\n\n1、第二组\n2、第二组")

    assert not [
        item for item in check_ppt_rules(document) if item.category == "sequence"
    ]


def test_placeholder_quote_and_punctuation_rules_are_independent():
    document = _document_with_elements("XX项目已完成。。“阶段目标")

    findings = check_ppt_rules(document)

    assert {item.rule_id for item in findings} == {
        "ppt-placeholder",
        "ppt-consecutive-punctuation",
        "ppt-quote-pair",
    }
    assert {item.target_text for item in findings} == {"XX", "。。", "“"}


def test_balanced_quotes_and_normal_sequence_do_not_report():
    document = _document_with_elements("1、背景\n2、做法\n3、成效\n“阶段目标”")

    assert check_ppt_rules(document) == ()
