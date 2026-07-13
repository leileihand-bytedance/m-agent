"""通用审核新增规则测试."""

from __future__ import annotations

import asyncio

from app.review.document_type import DocumentType
from app.review.format_checker import check_mixed_punct
from app.review.general_reviewer import review_general
from app.review.general_rule_checker import check_general_document_rules
from app.review.output_formatter import format_review_result
from app.review.reviewer import Finding, ReviewResult


class _FakeBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeMessage:
    def __init__(self, text: str):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, text: str):
        self._text = text

    def create(self, **_: object) -> _FakeMessage:
        return _FakeMessage(self._text)


class _FakeClient:
    def __init__(self, text: str):
        self.messages = _FakeMessages(text)


def test_check_general_document_rules_finds_placeholder_text():
    paragraphs = [
        "会议纪要",
        "【待补充】请补充本节最终结论。",
    ]

    findings = check_general_document_rules(paragraphs)

    assert [f.rule_id for f in findings] == ["general-placeholder"]
    assert findings[0].target_text == "【待补充】"


def test_check_general_document_rules_finds_heading_sequence_gap():
    paragraphs = [
        "一、总体情况",
        "这里是正文。",
        "三、下一步安排",
        "这里也是正文。",
    ]

    findings = check_general_document_rules(paragraphs)

    assert any(f.rule_id == "general-heading-seq-skip" for f in findings)


def test_check_general_document_rules_finds_orphan_heading():
    paragraphs = [
        "一、总体情况",
        "二、下一步安排",
        "后续继续推进。",
    ]

    findings = check_general_document_rules(paragraphs)

    assert any(
        f.rule_id == "general-heading-empty" and f.target_text == "一、总体情况"
        for f in findings
    )


def test_check_general_document_rules_does_not_treat_key_value_line_as_empty_heading():
    paragraphs = [
        "1.会议时间：2029年7月15日",
        "2.会议地点：协会会议室",
    ]

    findings = check_general_document_rules(paragraphs)

    assert not any(f.rule_id == "general-heading-empty" for f in findings)


def test_check_general_document_rules_does_not_treat_decimal_heading_as_empty_when_inline_value_exists():
    paragraphs = [
        "2.1理事候选单位条件：符合协会章程要求",
        "2.2监事候选单位条件：具备独立监督能力",
    ]

    findings = check_general_document_rules(paragraphs)

    assert not any(f.rule_id == "general-heading-empty" for f in findings)


def test_check_general_document_rules_does_not_treat_decimal_percentages_as_headings():
    paragraphs = ["34.18%", "35.79%"]

    findings = check_general_document_rules(paragraphs)

    assert not any(
        finding.rule_id in {"general-heading-empty", "general-heading-seq-skip"}
        for finding in findings
    )


def test_check_general_document_rules_does_not_check_questionnaire_number_gaps_as_headings():
    paragraphs = [
        "3.贵行在碳产品创新方面有哪些具体成果？",
        "5.贵行近三年绿色债券发行情况如何？",
    ]

    findings = check_general_document_rules(paragraphs)

    assert not any(f.rule_id == "general-heading-seq-skip" for f in findings)


def test_questionnaire_prompt_still_advances_numbering_for_following_heading():
    paragraphs = [
        "5.前一部分",
        "7.重大业务首次展业（请贵行补充相关情况）",
        "8.重大组织机构调整",
    ]

    findings = check_general_document_rules(paragraphs)

    assert not any(f.rule_id == "general-heading-seq-skip" for f in findings)


def test_questionnaire_optional_subquestion_is_not_reported_as_empty_heading():
    paragraphs = [
        "《城市商业银行发展报告》调研问卷",
        "1.贵行认为当前资产负债管理中最亟需解决的问题及相关的对策建议。",
        "（1）资产端所面临的困难及对策建议",
        "资产端相关回答。",
        "（2）负债端所面临的困难及对策建议",
        "（3）定价管理所面临的困难及对策建议",
        "定价管理相关回答。",
    ]

    findings = check_general_document_rules(paragraphs)

    assert not any(
        finding.rule_id == "general-heading-empty"
        and finding.target_text == "（2）负债端所面临的困难及对策建议"
        for finding in findings
    )


def test_mixed_punctuation_does_not_flag_numbered_question_separator():
    findings = check_mixed_punct([
        "12 .请回顾贵行自成立以来在社会责任方面的典型案例。"
    ])

    assert findings == []


def test_check_general_document_rules_finds_missing_attachment_reference():
    paragraphs = [
        "具体数据详见附件1。",
        "一、总体情况",
        "后续继续推进。",
    ]

    findings = check_general_document_rules(paragraphs)

    assert any(
        f.rule_id == "general-reference-missing" and f.target_text == "附件1"
        for f in findings
    )


def test_check_general_document_rules_finds_attachment_name_number_conflict():
    paragraphs = [
        "详见附件7：《福建XXX》。",
        "附件7：《广东XXX》",
    ]

    findings = check_general_document_rules(paragraphs)

    assert any(
        f.rule_id == "general-attachment-name-mismatch" and f.target_text == "附件7"
        for f in findings
    )


def test_check_general_document_rules_finds_invalid_full_date():
    paragraphs = [
        "会议时间为2026年2月30日。",
    ]

    findings = check_general_document_rules(paragraphs)

    assert any(
        f.rule_id == "general-invalid-date" and f.target_text == "2026年2月30日"
        for f in findings
    )


def test_check_general_document_rules_finds_invalid_short_date_only_when_always_impossible():
    paragraphs = [
        "报名截止时间为4月31日。",
    ]

    findings = check_general_document_rules(paragraphs)

    assert any(
        f.rule_id == "general-invalid-date" and f.target_text == "4月31日"
        for f in findings
    )


def test_check_general_document_rules_allows_short_february_29_without_year():
    paragraphs = [
        "如遇特殊情况，活动可顺延至2月29日。",
    ]

    findings = check_general_document_rules(paragraphs)

    assert not any(f.rule_id == "general-invalid-date" for f in findings)


def test_check_general_document_rules_allows_valid_leap_day_with_explicit_year():
    paragraphs = [
        "活动时间为2024年2月29日。",
    ]

    findings = check_general_document_rules(paragraphs)

    assert not any(f.rule_id == "general-invalid-date" for f in findings)


def test_check_general_document_rules_finds_date_range_logic_conflict_with_explicit_years():
    paragraphs = [
        "意见征询及反馈周期为2026年7月9日至2026年7月8日。",
    ]

    findings = check_general_document_rules(paragraphs)

    assert any(
        f.rule_id == "general-date-range-logic" and f.target_text == "2026年7月9日至2026年7月8日"
        for f in findings
    )


def test_check_general_document_rules_finds_date_range_logic_conflict_with_same_month_short_dates():
    paragraphs = [
        "反馈时间为7月9日至7月8日。",
    ]

    findings = check_general_document_rules(paragraphs)

    assert any(
        f.rule_id == "general-date-range-logic" and f.target_text == "7月9日至7月8日"
        for f in findings
    )


def test_check_general_document_rules_does_not_flag_cross_year_short_range_without_end_year():
    paragraphs = [
        "公示时间为2026年12月30日至1月5日。",
    ]

    findings = check_general_document_rules(paragraphs)

    assert not any(f.rule_id == "general-date-range-logic" for f in findings)


def test_check_general_document_rules_finds_same_day_time_range_logic_conflict():
    paragraphs = [
        "会议时间为2026年7月9日15:00-14:00。",
    ]

    findings = check_general_document_rules(paragraphs)

    assert any(
        f.rule_id == "general-date-range-logic" and f.target_text == "2026年7月9日15:00-14:00"
        for f in findings
    )


def test_check_general_document_rules_does_not_flag_same_day_time_range_when_explicitly_cross_day():
    paragraphs = [
        "值班时间为2026年7月9日22:00-次日06:00。",
    ]

    findings = check_general_document_rules(paragraphs)

    assert not any(f.rule_id == "general-date-range-logic" for f in findings)


def test_check_general_document_rules_finds_attachment_name_number_conflict_without_colon():
    paragraphs = [
        "详见附件1《关于福建XXX的通知》。",
        "附件1：《关于广东XXX的通知》",
    ]

    findings = check_general_document_rules(paragraphs)

    assert any(
        f.rule_id == "general-attachment-name-mismatch" and f.target_text == "附件1"
        for f in findings
    )


def test_check_general_document_rules_finds_feedback_form_listed_under_another_number():
    paragraphs = [
        "对事项有修改、反对意见，请填写附件1议案意见反馈表，盖章扫描后发送。",
        "附件 1：换届工作领导小组名单",
        "附件 7：议案意见反馈表",
    ]

    findings = check_general_document_rules(paragraphs)

    mismatch = next(
        finding
        for finding in findings
        if finding.rule_id == "general-attachment-name-mismatch"
    )
    assert mismatch.target_text == "附件1"
    assert "实际为附件7" in mismatch.description


def test_check_general_document_rules_has_no_false_positive_on_synthetic_clean_doc():
    paragraphs = [
        "项目工作总结",
        "一、总体情况",
        "本期按计划完成各项任务，时间范围和附件名称保持一致。",
        "附件1：任务清单",
    ]

    findings = check_general_document_rules(paragraphs)

    assert findings == []


def test_review_general_merges_new_deterministic_rules(monkeypatch):
    monkeypatch.setattr(
        "app.review.general_reviewer.build_anthropic_client",
        lambda: (_FakeClient('{"issues": []}'), "fake-model"),
    )
    paragraphs = [
        "会议纪要",
        "【待补充】请补充本节最终结论。",
    ]

    result = asyncio.run(review_general(paragraphs, "", "会议纪要.docx"))

    assert any(f.rule_id == "general-placeholder" for f in result.findings)


def test_format_review_result_shows_new_rule_labels():
    result = ReviewResult(
        findings=[
            Finding(
                rule_id="general-placeholder",
                paragraph_index=1,
                line_number=2,
                original_text="【待补充】请补充本节最终结论。",
                description="存在未清理的占位内容",
                target_text="【待补充】",
            ),
            Finding(
                rule_id="general-reference-missing",
                paragraph_index=2,
                line_number=3,
                original_text="具体数据详见附件1。",
                description="文中提到附件1，但正文里未找到对应附件标题",
                target_text="附件1",
            ),
            Finding(
                rule_id="general-attachment-name-mismatch",
                paragraph_index=3,
                line_number=4,
                original_text="详见附件7：《福建XXX》。",
                description="正文提到的附件名称与附件7标题不一致",
                target_text="附件7",
            ),
            Finding(
                rule_id="general-invalid-date",
                paragraph_index=4,
                line_number=5,
                original_text="会议时间为2026年2月30日。",
                description="日期不存在",
                target_text="2026年2月30日",
            ),
            Finding(
                rule_id="general-date-range-logic",
                paragraph_index=5,
                line_number=6,
                original_text="反馈时间为7月9日至7月8日。",
                description="起始时间晚于结束时间",
                target_text="7月9日至7月8日",
            ),
            Finding(
                rule_id="general-logic-inconsistency",
                paragraph_index=6,
                line_number=7,
                original_text="本次会议共审议三项议案。",
                description="正文称三项，但后文实际列出四项议案",
                target_text="共审议三项议案",
            ),
        ],
        total_rules=21,
        passed_rules=15,
        filename="会议纪要.docx",
    )

    output = format_review_result(result, "会议纪要.docx", doc_type=DocumentType.GENERAL)

    assert "占位内容" in output
    assert "引用悬空" in output
    assert "附件名称不一致" in output
    assert "日期常识错误" in output
    assert "时间范围逻辑错误" in output
    assert "前后逻辑不一致" in output


def test_format_review_result_shows_term_variant_label():
    result = ReviewResult(
        findings=[
            Finding(
                rule_id="general-term-variant",
                paragraph_index=1,
                line_number=2,
                original_text="本季度OpenHiev项目正式上线。",
                description="术语写法不规范：'OpenHiev'应为'OpenHive'",
                target_text="OpenHiev",
            ),
        ],
        total_rules=17,
        passed_rules=16,
        filename="会议纪要.docx",
    )

    output = format_review_result(result, "会议纪要.docx", doc_type=DocumentType.GENERAL)
    assert "术语写法" in output
