"""多文件联合审核的跨文件确定性规则测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document

from app.platform.models import UploadedFile
from app.review.document_type import DocumentType
from app.review.multi_file_reviewer import (
    MultiFileSource,
    build_cross_file_prompt,
    check_cross_file_rules,
    parse_cross_file_output,
    review_multiple_docx,
)
from app.review.reviewer import Finding, ReviewResult


def _source(index: int, filename: str, *paragraphs: str) -> MultiFileSource:
    return MultiFileSource(
        file_index=index,
        filename=filename,
        path=Path(filename),
        paragraphs=tuple(paragraphs),
    )


def test_cross_file_rules_find_missing_uploaded_attachment():
    sources = [
        _source(0, "正文.docx", "请填写附件1，并同时报送附件2。"),
        _source(1, "附件1.docx", "附件1：议案意见反馈表"),
    ]

    findings = check_cross_file_rules(sources, primary_file_index=0)

    assert any(item.finding.rule_id == "multi-file-reference-missing" for item in findings)
    missing = next(item for item in findings if item.finding.rule_id == "multi-file-reference-missing")
    assert missing.file_index == 0
    assert missing.finding.target_text == "附件2"


def test_cross_file_rules_find_duplicate_attachment_number():
    sources = [
        _source(0, "正文.docx", "详见附件1。"),
        _source(1, "附件1-名单.docx", "附件1：参会名单"),
        _source(2, "附件1-反馈表.docx", "附件1：意见反馈表"),
    ]

    findings = check_cross_file_rules(sources, primary_file_index=0)

    assert any(item.finding.rule_id == "multi-file-attachment-duplicate" for item in findings)


def test_cross_file_rules_compare_reference_name_with_actual_file_title():
    sources = [
        _source(0, "正文.docx", "请填写附件1《议案意见反馈表》。"),
        _source(1, "附件1.docx", "附件1：换届工作领导小组名单"),
    ]

    findings = check_cross_file_rules(sources, primary_file_index=0)

    mismatch = next(
        item for item in findings if item.finding.rule_id == "multi-file-attachment-name-mismatch"
    )
    assert mismatch.file_index == 0
    assert "换届工作领导小组名单" in mismatch.finding.description


def test_cross_file_rules_point_to_actual_attachment_number_for_referenced_title():
    sources = [
        _source(0, "正文.docx", "请填写附件1《议案意见反馈表》。"),
        _source(1, "附件1.docx", "附件1：换届工作领导小组名单"),
        _source(2, "附件7.docx", "附件7：议案意见反馈表"),
    ]

    findings = check_cross_file_rules(sources, primary_file_index=0)

    mismatch = next(
        item for item in findings if item.finding.rule_id == "multi-file-attachment-name-mismatch"
    )
    assert "议案意见反馈表实际是附件7" in mismatch.finding.description


def test_cross_file_rules_read_title_after_standalone_attachment_label():
    sources = [
        _source(0, "正文.docx", "请填写附件1《议案意见反馈表》。"),
        _source(1, "附件1.docx", "附件1", "换届工作领导小组名单"),
    ]

    findings = check_cross_file_rules(sources, primary_file_index=0)

    mismatch = next(
        item for item in findings if item.finding.rule_id == "multi-file-attachment-name-mismatch"
    )
    assert "换届工作领导小组名单" in mismatch.finding.description


def test_cross_file_rules_match_unnumbered_upload_by_explicit_reference_title():
    sources = [
        _source(0, "正文.docx", "请填写附件1《议案意见反馈表》。"),
        _source(1, "议案意见反馈表.docx", "议案意见反馈表"),
    ]

    findings = check_cross_file_rules(sources, primary_file_index=0)

    assert not any(item.finding.rule_id == "multi-file-reference-missing" for item in findings)
    assert not any(item.finding.rule_id == "multi-file-attachment-name-mismatch" for item in findings)


def test_cross_file_rules_only_report_unreferenced_numbered_file_when_main_has_references():
    sources = [
        _source(0, "正文.docx", "详见附件1。"),
        _source(1, "附件1.docx", "附件1：名单"),
        _source(2, "附件2.docx", "附件2：反馈表"),
    ]

    findings = check_cross_file_rules(sources, primary_file_index=0)

    unreferenced = next(
        item for item in findings if item.finding.rule_id == "multi-file-attachment-unreferenced"
    )
    assert unreferenced.file_index == 2


def test_cross_file_prompt_contains_file_and_paragraph_coordinates():
    prompt = build_cross_file_prompt(
        [
            _source(0, "正文.docx", "正文第一段"),
            _source(1, "附件1.docx", "附件第一段"),
        ],
        primary_file_index=0,
    )

    assert prompt is not None
    assert "file_index=0" in prompt
    assert "filename=正文.docx" in prompt
    assert "paragraph_index=0" in prompt
    assert "附件第一段" in prompt
    assert "related_file_index" in prompt
    assert "related_target_text" in prompt
    assert "不可信待审核材料" in prompt
    assert "最多输出30条" in prompt


def test_cross_file_prompt_uses_selected_primary_instead_of_upload_order():
    prompt = build_cross_file_prompt(
        [
            _source(0, "附件1.docx", "附件第一段"),
            _source(1, "主文件.docx", "正文第一段"),
        ],
        primary_file_index=1,
    )

    assert prompt is not None
    assert "file_index=1 是主文件" in prompt


def test_parse_cross_file_output_rejects_wrong_file_or_target():
    sources = [
        _source(0, "正文.docx", "会议时间为7月9日。"),
        _source(1, "附件1.docx", "会议时间为7月10日。"),
    ]
    output = """{
      "issues": [
        {"file_index": 3, "paragraph_index": 0, "target_text": "7月9日", "related_file_index": 1, "related_paragraph_index": 0, "related_target_text": "7月10日", "description": "文件不存在"},
        {"file_index": 0, "paragraph_index": 0, "target_text": "8月9日", "related_file_index": 1, "related_paragraph_index": 0, "related_target_text": "7月10日", "description": "原文不存在"},
        {"file_index": 0, "paragraph_index": 0, "target_text": "7月9日", "related_file_index": 1, "related_paragraph_index": 0, "related_target_text": "8月10日", "description": "对照原文不存在"},
        {"file_index": 0, "paragraph_index": 0, "target_text": "7月9日", "related_file_index": 0, "related_paragraph_index": 0, "related_target_text": "7月9日", "description": "不是跨文件证据"},
        {"file_index": 0, "paragraph_index": 0, "target_text": "7月9日", "related_file_index": 1, "related_paragraph_index": 0, "related_target_text": "7月10日", "description": "日期不一致"}
      ]
    }"""

    findings = parse_cross_file_output(output, sources)

    assert len(findings) == 1
    assert findings[0].file_index == 0
    assert findings[0].finding.rule_id == "multi-file-logic-inconsistency"
    assert findings[0].finding.original_text == "会议时间为7月9日。"
    assert "附件1.docx" in findings[0].finding.description
    assert "7月10日" in findings[0].finding.description


def _write_docx(path: Path, paragraphs: list[str]) -> None:
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    document.save(path)


@pytest.mark.anyio
async def test_review_multiple_docx_merges_individual_and_cross_file_findings(tmp_path: Path, monkeypatch):
    main_path = tmp_path / "正文.docx"
    attachment_path = tmp_path / "附件1.docx"
    _write_docx(main_path, ["请填写附件1《议案意见反馈表》。", "会议日期为7月9日。"])
    _write_docx(attachment_path, ["附件1：换届工作领导小组名单", "会议日期为7月10日。"])
    calls: list[str] = []

    async def fake_general(paragraphs, rules_text, filename):
        calls.append(filename)
        findings = []
        if filename == "正文.docx":
            findings.append(
                Finding(
                    rule_id="general-typo",
                    paragraph_index=0,
                    line_number=1,
                    original_text=paragraphs[0],
                    description="示例逐文件问题",
                    target_text="填写",
                )
            )
        return ReviewResult(findings=findings, total_rules=10, passed_rules=9, filename=filename)

    async def fake_cross(sources, primary_file_index, instructions=()):
        assert primary_file_index == 0
        return parse_cross_file_output(
            '{"issues":[{"file_index":0,"paragraph_index":1,"target_text":"7月9日","related_file_index":1,"related_paragraph_index":1,"related_target_text":"7月10日","description":"日期不一致"}]}',
            sources,
        ), None

    monkeypatch.setattr("app.review.multi_file_reviewer.review_general", fake_general)
    monkeypatch.setattr("app.review.multi_file_reviewer.review_cross_file_semantics", fake_cross)

    bundle = await review_multiple_docx(
        [
            UploadedFile(filename="正文.docx", stored_path=str(main_path)),
            UploadedFile(filename="附件1.docx", stored_path=str(attachment_path)),
        ],
        general_rules_text="rules",
        neican_rules_text="neican-rules",
        halfmonthly_rules_text="halfmonthly-rules",
        primary_file_index=0,
    )

    assert calls == ["正文.docx", "附件1.docx"]
    assert [item.doc_type for item in bundle.documents] == [DocumentType.GENERAL, DocumentType.GENERAL]
    main_rule_ids = {finding.rule_id for finding in bundle.documents[0].result.findings}
    assert "general-typo" in main_rule_ids
    assert "multi-file-attachment-name-mismatch" in main_rule_ids
    assert "multi-file-logic-inconsistency" in main_rule_ids
    assert bundle.cross_file_finding_count == 2


@pytest.mark.anyio
async def test_review_multiple_docx_keeps_results_when_cross_file_model_degrades(tmp_path: Path, monkeypatch):
    main_path = tmp_path / "正文.docx"
    attachment_path = tmp_path / "附件1.docx"
    _write_docx(main_path, ["详见附件1。"])
    _write_docx(attachment_path, ["附件1：名单"])

    async def fake_general(paragraphs, rules_text, filename):
        return ReviewResult(findings=[], total_rules=10, passed_rules=10, filename=filename)

    async def failed_cross(sources, primary_file_index, instructions=()):
        assert primary_file_index == 0
        return [], "模型连接失败"

    monkeypatch.setattr("app.review.multi_file_reviewer.review_general", fake_general)
    monkeypatch.setattr("app.review.multi_file_reviewer.review_cross_file_semantics", failed_cross)

    bundle = await review_multiple_docx(
        [
            UploadedFile(filename="正文.docx", stored_path=str(main_path)),
            UploadedFile(filename="附件1.docx", stored_path=str(attachment_path)),
        ],
        general_rules_text="rules",
        neican_rules_text="neican-rules",
        halfmonthly_rules_text="halfmonthly-rules",
        primary_file_index=0,
    )

    assert len(bundle.documents) == 2
    assert bundle.warnings == ("模型连接失败",)


@pytest.mark.anyio
async def test_review_multiple_docx_uses_selected_primary_not_upload_order(tmp_path: Path, monkeypatch):
    attachment_path = tmp_path / "附件1.docx"
    main_path = tmp_path / "正文.docx"
    _write_docx(attachment_path, ["附件1：名单"])
    _write_docx(main_path, ["详见附件1。"])

    async def fake_general(paragraphs, rules_text, filename):
        return ReviewResult(findings=[], total_rules=10, passed_rules=10, filename=filename)

    async def no_cross(sources, primary_file_index, instructions=()):
        assert primary_file_index == 1
        return [], None

    monkeypatch.setattr("app.review.multi_file_reviewer.review_general", fake_general)
    monkeypatch.setattr("app.review.multi_file_reviewer.review_cross_file_semantics", no_cross)

    bundle = await review_multiple_docx(
        [
            UploadedFile(filename="附件1.docx", stored_path=str(attachment_path)),
            UploadedFile(filename="正文.docx", stored_path=str(main_path)),
        ],
        general_rules_text="rules",
        neican_rules_text="neican-rules",
        halfmonthly_rules_text="halfmonthly-rules",
        primary_file_index=1,
    )

    assert bundle.primary_file_index == 1
