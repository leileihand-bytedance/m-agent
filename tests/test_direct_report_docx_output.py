from __future__ import annotations

from datetime import date
from pathlib import Path
import zipfile

from lxml import etree
import pytest

from skills.direct_report.docx_output import (
    DEFAULT_TEMPLATE_PATH,
    generate_direct_report_docx,
    parse_direct_report_document_request,
    should_generate_direct_report_docx,
)


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _document_paragraphs(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    return [
        "".join(paragraph.itertext()).strip()
        for paragraph in root.xpath("//w:body//w:p", namespaces={"w": W})
    ]


def test_parse_direct_report_document_request_uses_only_explicit_metadata():
    request = parse_direct_report_document_request(
        "请输出Word，2026年第3期（总第28期），日期填写2026年7月21日。"
    )

    assert request.issue_year == "2026"
    assert request.issue_number == "3"
    assert request.total_issue_number == "28"
    assert request.report_date == date(2026, 7, 21)

    unlabeled = parse_direct_report_document_request(
        "根据2025年12月31日发布的材料写一篇直报。"
    )
    assert unlabeled.issue_year == ""
    assert unlabeled.issue_number == ""
    assert unlabeled.total_issue_number == ""
    assert unlabeled.report_date is None

    source_issue = parse_direct_report_document_request(
        "根据2025年第3期行业材料写一篇直报。"
    )
    assert source_issue.issue_year == ""
    assert source_issue.issue_number == ""

    export_with_source_issue = parse_direct_report_document_request(
        "输出Word，根据2025年第3期行业材料写直报，日期填写2026年7月21日。"
    )
    assert export_with_source_issue.issue_year == ""
    assert export_with_source_issue.issue_number == ""
    assert export_with_source_issue.report_date == date(2026, 7, 21)

    source_issue_line = parse_direct_report_document_request(
        "输出Word，参考2025年第3期（总第28期）材料写直报。"
    )
    assert source_issue_line.issue_year == ""
    assert source_issue_line.issue_number == ""
    assert source_issue_line.total_issue_number == ""

    labeled_issue = parse_direct_report_document_request(
        "输出Word，期号填写2026年第3期。"
    )
    assert labeled_issue.issue_year == "2026"
    assert labeled_issue.issue_number == "3"

    total_only = parse_direct_report_document_request("总期号填写28")
    assert total_only.issue_number == ""
    assert total_only.total_issue_number == "28"


def test_direct_report_word_generation_requires_explicit_user_request():
    assert should_generate_direct_report_docx("请写一篇直报") is False
    assert should_generate_direct_report_docx("请写一篇直报并输出Word") is True
    assert should_generate_direct_report_docx("请把上一稿导出word") is True
    assert should_generate_direct_report_docx("期号填第3期，总第28期") is True
    assert should_generate_direct_report_docx("根据2025年第3期材料写直报") is False
    assert should_generate_direct_report_docx("根据2025年第3期（总第28期）材料写直报") is False
    assert should_generate_direct_report_docx("请输出直报，参考2025年第3期材料") is False
    assert should_generate_direct_report_docx("请根据Word文件中的材料写直报") is False


def test_direct_report_template_does_not_keep_external_add_in_references():
    with zipfile.ZipFile(DEFAULT_TEMPLATE_PATH) as template:
        names = template.namelist()
        package_rels = template.read("_rels/.rels").decode("utf-8")

    assert not any(name.startswith("word/webextensions/") for name in names)
    assert "webextension" not in package_rels.lower()


def test_generate_direct_report_docx_replaces_slots_and_preserves_package(tmp_path: Path):
    output = generate_direct_report_docx(
        title="微众银行完善科技金融服务机制",
        body="第一段说明政策背景。\n\n第二段说明微众银行行动。\n第三段说明下一步安排。",
        request_text="输出Word，2026年第3期（总第28期），日期2026年7月21日",
        output_dir=tmp_path,
    )

    assert output.is_file()
    assert output.parent == tmp_path
    assert output.suffix == ".docx"
    paragraphs = _document_paragraphs(output)
    assert "微众银行完善科技金融服务机制" in paragraphs
    assert "第一段说明政策背景。" in paragraphs
    assert "第二段说明微众银行行动。" in paragraphs
    assert "第三段说明下一步安排。" in paragraphs
    assert "标题" not in paragraphs
    assert "正文正文正文。" not in paragraphs

    with zipfile.ZipFile(DEFAULT_TEMPLATE_PATH) as source, zipfile.ZipFile(output) as final:
        assert set(source.namelist()) == set(final.namelist())
        for part in source.namelist():
            if part != "word/document.xml":
                assert final.read(part) == source.read(part), part
        document_text = final.read("word/document.xml").decode("utf-8")

    assert "2026年第3期（总第28期）" in document_text
    assert document_text.count("2026年7月21日") == 2


def test_generate_direct_report_docx_keeps_unrequested_template_placeholders(tmp_path: Path):
    output = generate_direct_report_docx(
        title="直报标题",
        body="直报正文。",
        request_text="请输出Word",
        output_dir=tmp_path,
    )

    with zipfile.ZipFile(output) as archive:
        document_text = archive.read("word/document.xml").decode("utf-8")

    assert "2026年第X期（总第XX期）" in document_text
    assert _document_paragraphs(output).count("2026年X月XX日") == 2


def test_generate_direct_report_docx_requires_task_output_directory():
    with pytest.raises(ValueError, match="输出目录"):
        generate_direct_report_docx(
            title="直报标题",
            body="直报正文。",
            request_text="请输出Word",
            output_dir="",
        )
