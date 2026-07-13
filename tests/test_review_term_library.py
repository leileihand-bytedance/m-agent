"""通用审核微众银行术语库测试."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from app.review.document_type import DocumentType
from app.review.general_reviewer import review_general, _build_general_prompt
from app.review.general_term_checker import (
    GENERAL_TERM_VARIANT_RULE_ID,
    build_protected_terms_prompt_section,
    check_term_variants,
    select_relevant_terms,
)
from app.review.output_formatter import format_review_result
from app.review.term_loader import load_term_library, clear_term_library_cache


SYNTHETIC_CLEAN_PARAGRAPHS = [
    "本项目使用 OpenHive 和 FISCO BCOS 技术平台。",
    "微业贷用于服务小微企业，相关名称均按标准写法表述。",
]


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


def test_load_term_library_returns_terms():
    clear_term_library_cache()
    terms = load_term_library()

    assert isinstance(terms, list)
    assert len(terms) >= 5

    ids = {term.get("term_id") for term in terms}
    assert "webank" in ids
    assert "openhive" in ids
    assert "weiyedai" in ids


def test_load_term_library_missing_file_returns_empty():
    clear_term_library_cache()
    with tempfile.TemporaryDirectory() as tmpdir:
        missing = Path(tmpdir) / "not_exist.json"
        assert load_term_library(missing) == []


def test_check_term_variants_detects_openhive_typo():
    paragraphs = ["本季度OpenHiev项目正式上线。"]
    findings = check_term_variants(paragraphs)

    assert len(findings) == 1
    assert findings[0].rule_id == GENERAL_TERM_VARIANT_RULE_ID
    assert findings[0].target_text == "OpenHiev"
    assert "OpenHive" in findings[0].description
    assert findings[0].original_text == paragraphs[0]


def test_check_term_variants_detects_openhive_typo_case_insensitively():
    paragraphs = [
        "本季度Openhiev项目正式上线。",
        "另一批次openhiev能力开始灰度验证。",
    ]
    findings = check_term_variants(paragraphs)

    assert [finding.target_text for finding in findings] == ["Openhiev", "openhiev"]
    assert all(finding.rule_id == GENERAL_TERM_VARIANT_RULE_ID for finding in findings)
    assert all("OpenHive" in finding.description for finding in findings)


def test_check_term_variants_detects_weiyedai_typo():
    paragraphs = ["微业代产品专注于小微企业。"]
    findings = check_term_variants(paragraphs)

    assert len(findings) == 1
    assert findings[0].rule_id == GENERAL_TERM_VARIANT_RULE_ID
    assert findings[0].target_text == "微业代"
    assert "微业贷" in findings[0].description


def test_check_term_variants_ignores_variant_embedded_in_longer_word():
    paragraphs = ["我们讨论的是微业代理服务模式，不是贷款产品名称。"]
    findings = check_term_variants(paragraphs)

    assert findings == []


def test_check_term_variants_allows_standard_and_aliases():
    paragraphs = [
        "基于开放蜂巢OpenHive技术体系。",
        "Openhive开放蜂巢也是允许写法。",
        "微业贷产品专注于小微企业。",
    ]
    findings = check_term_variants(paragraphs)
    assert findings == []


def test_check_term_variants_no_false_positives_on_synthetic_clean_text():
    findings = check_term_variants(SYNTHETIC_CLEAN_PARAGRAPHS)

    assert findings == []


def test_select_relevant_terms_finds_terms_in_chunk():
    paragraphs = [
        "本项目基于OpenHive数字银行底座。",
        "同时使用FISCO BCOS区块链平台。",
    ]
    relevant = select_relevant_terms(paragraphs)

    standards = {term.get("standard") for term in relevant}
    assert "OpenHive" in standards
    assert "FISCO BCOS" in standards


def test_select_relevant_terms_empty_for_unrelated_text():
    paragraphs = ["今天天气不错,适合外出散步。"]
    relevant = select_relevant_terms(paragraphs)
    assert relevant == []


def test_build_protected_terms_section_present_when_terms_exist():
    paragraphs = ["本系统基于OpenHive和FISCO BCOS构建。"]
    section = build_protected_terms_prompt_section(paragraphs)

    assert "受保护术语" in section
    assert "OpenHive" in section
    assert "FISCO BCOS" in section


def test_build_protected_terms_section_empty_when_no_terms():
    paragraphs = ["这是一段与术语库无关的普通文字。"]
    section = build_protected_terms_prompt_section(paragraphs)
    assert section == ""


def test_build_general_prompt_includes_protected_terms(monkeypatch):
    """包含专业术语的 chunk,prompt 中应出现受保护术语段."""
    prompt = _build_general_prompt(
        "rules",
        [(0, "本系统基于OpenHive数字银行底座。")],
        "测试.docx",
    )
    assert "受保护术语" in prompt
    assert "OpenHive" in prompt


def test_build_general_prompt_no_protected_terms_for_unrelated_chunk():
    prompt = _build_general_prompt(
        "rules",
        [(0, "今天天气不错。")],
        "测试.docx",
    )
    assert "受保护术语" not in prompt


def test_review_general_includes_term_variant_when_llm_returns_empty(monkeypatch):
    monkeypatch.setattr(
        "app.review.general_reviewer.build_anthropic_client",
        lambda: (_FakeClient('{"issues": []}'), "fake-model"),
    )

    paragraphs = [
        "会议纪要",
        "本季度OpenHiev项目正式上线。",
    ]
    result = asyncio.run(review_general(paragraphs, "", "会议纪要.docx"))

    assert any(f.rule_id == GENERAL_TERM_VARIANT_RULE_ID for f in result.findings)
    assert result.total_rules == 21


def test_iso_27701_typo_is_detected_deterministically():
    findings = check_term_variants([
        "本行依据 IS027701 隐私体系认证要求完善管理框架。"
    ])

    assert len(findings) == 1
    assert findings[0].target_text == "IS027701"
    assert "ISO 27701" in findings[0].description


def test_review_general_synthetic_text_has_no_term_variant_false_positives(monkeypatch):
    monkeypatch.setattr(
        "app.review.general_reviewer.build_anthropic_client",
        lambda: (_FakeClient('{"issues": []}'), "fake-model"),
    )

    result = asyncio.run(review_general(SYNTHETIC_CLEAN_PARAGRAPHS, "", "示例文档.docx"))

    term_findings = [f for f in result.findings if f.rule_id == GENERAL_TERM_VARIANT_RULE_ID]
    assert term_findings == []
    assert result.total_rules == 21


def test_review_general_term_variant_does_not_affect_other_rules(monkeypatch):
    """mock LLM 返回空时,代码规则(占位内容 + 术语错写)仍能同时命中."""
    monkeypatch.setattr(
        "app.review.general_reviewer.build_anthropic_client",
        lambda: (_FakeClient('{"issues": []}'), "fake-model"),
    )

    paragraphs = [
        "一、总体情况",
        "【待补充】",
        "本季度OpenHiev项目上线。",
    ]
    result = asyncio.run(review_general(paragraphs, "", "会议纪要.docx"))

    rule_ids = {f.rule_id for f in result.findings}
    assert "general-placeholder" in rule_ids
    assert GENERAL_TERM_VARIANT_RULE_ID in rule_ids


def test_format_review_result_shows_term_variant_label():
    from app.review.reviewer import Finding, ReviewResult

    result = ReviewResult(
        findings=[
            Finding(
                rule_id=GENERAL_TERM_VARIANT_RULE_ID,
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
    assert "OpenHiev" in output
