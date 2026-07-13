from __future__ import annotations

import json
from pathlib import Path
import zipfile

import pytest
from docx import Document
from pypdf import PdfWriter

from app.platform.documents import (
    DocumentFormat,
    DocumentSecurityError,
    DocumentService,
)


def _make_docx(path: Path) -> None:
    document = Document()
    document.add_heading("测试标题", level=1)
    document.add_paragraph("第一段正文。")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "指标"
    table.cell(0, 1).text = "100亿元"
    section = document.sections[0]
    section.header.paragraphs[0].text = "页眉文字"
    document.save(path)


def _make_blank_pdf(path: Path, pages: int = 2) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    with path.open("wb") as stream:
        writer.write(stream)


def _make_pptx(path: Path) -> None:
    pptx = pytest.importorskip("pptx")
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "经营情况"
    slide.placeholders[1].text = "累计服务客户超过100万户"
    table_shape = slide.shapes.add_table(1, 2, 0, 0, 4_000_000, 1_000_000)
    table_shape.table.cell(0, 0).text = "指标"
    table_shape.table.cell(0, 1).text = "100万户"
    presentation.save(path)


def test_document_service_parses_docx_and_persists_complete_artifact(tmp_path):
    input_dir = tmp_path / "input"
    work_dir = tmp_path / "work"
    input_dir.mkdir()
    path = input_dir / "材料.docx"
    _make_docx(path)

    artifact = DocumentService().parse(
        path,
        allowed_root=input_dir,
        work_dir=work_dir,
    )

    assert artifact.format == DocumentFormat.DOCX
    assert "测试标题" in artifact.full_text
    assert "第一段正文" in artifact.full_text
    assert "指标\t100亿元" in artifact.full_text
    assert any(block.kind == "header" and "页眉文字" in block.text for block in artifact.blocks)
    assert any(block.kind == "table" for block in artifact.blocks)
    assert artifact.sha256

    stored = list((work_dir / "documents").glob("*/document.json"))
    assert len(stored) == 1
    payload = json.loads(stored[0].read_text(encoding="utf-8"))
    assert payload["format"] == "docx"
    assert payload["full_text"] == artifact.full_text
    assert payload["source"]["original_name"] == "材料.docx"


def test_document_service_marks_textless_pdf_pages_for_ocr(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    path = input_dir / "扫描材料.pdf"
    _make_blank_pdf(path, pages=2)

    artifact = DocumentService().parse(path, allowed_root=input_dir, work_dir=tmp_path / "work")

    assert artifact.format == DocumentFormat.PDF
    assert artifact.page_count == 2
    assert artifact.full_text == ""
    assert {warning.code for warning in artifact.warnings} == {"ocr_required"}
    assert artifact.warnings[0].locations == ("page:1", "page:2")


def test_document_service_parses_pptx_with_slide_locations_and_tables(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    path = input_dir / "汇报材料.pptx"
    _make_pptx(path)

    artifact = DocumentService().parse(path, allowed_root=input_dir, work_dir=tmp_path / "work")

    assert artifact.format == DocumentFormat.PPTX
    assert artifact.page_count == 1
    assert "经营情况" in artifact.full_text
    assert "累计服务客户超过100万户" in artifact.full_text
    assert "指标\t100万户" in artifact.full_text
    assert all(block.location.startswith("slide:1") for block in artifact.blocks)
    assert any(block.kind == "table" for block in artifact.blocks)


def test_document_service_rejects_extension_signature_mismatch(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    fake_pdf = input_dir / "伪造.pdf"
    fake_pdf.write_bytes(b"not-a-pdf")

    with pytest.raises(DocumentSecurityError, match="文件内容与 PDF 格式不一致"):
        DocumentService().parse(fake_pdf, allowed_root=input_dir, work_dir=tmp_path / "work")


def test_document_service_rejects_paths_outside_current_task(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    outside = tmp_path / "outside.pdf"
    _make_blank_pdf(outside, pages=1)

    with pytest.raises(DocumentSecurityError, match="当前任务目录之外"):
        DocumentService().parse(outside, allowed_root=input_dir, work_dir=tmp_path / "work")


def test_document_service_rejects_html_until_scope_is_reopened(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    html = input_dir / "材料.html"
    html.write_text("<html><body>正文</body></html>", encoding="utf-8")

    with pytest.raises(DocumentSecurityError, match="暂不支持"):
        DocumentService().parse(html, allowed_root=input_dir, work_dir=tmp_path / "work")


def test_document_service_rejects_file_over_configured_limit(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    path = input_dir / "超限.pdf"
    path.write_bytes(b"%PDF-" + b"0" * 32)

    with pytest.raises(DocumentSecurityError, match="大小上限"):
        DocumentService(max_file_bytes=16).parse(
            path,
            allowed_root=input_dir,
            work_dir=tmp_path / "work",
        )


def test_document_service_rejects_intermediate_output_outside_task_work(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    path = input_dir / "材料.docx"
    _make_docx(path)

    with pytest.raises(ValueError, match="work 目录"):
        DocumentService().parse(
            path,
            allowed_root=input_dir,
            work_dir=tmp_path / "output",
        )


def test_document_service_rejects_suspicious_ooxml_compression_ratio(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    path = input_dir / "异常.docx"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr("word/document.xml", "A" * 2_000_000)

    with pytest.raises(DocumentSecurityError, match="压缩比异常"):
        DocumentService(max_compression_ratio=100).parse(
            path,
            allowed_root=input_dir,
            work_dir=tmp_path / "work",
        )


def test_prompt_material_samples_across_long_document_instead_of_prefix_only(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    path = input_dir / "长材料.docx"
    document = Document()
    for index in range(1, 31):
        document.add_paragraph(f"第{index}段-" + str(index) * 80)
    document.save(path)

    artifact = DocumentService().parse(path, allowed_root=input_dir, work_dir=tmp_path / "work")
    material = artifact.to_material(max_chars=1200)

    assert material["content_complete"] is False
    assert "第1段" in material["text"]
    assert "第30段" in material["text"]
    assert "中间内容已按位置抽样" in material["text"]
    assert Path(str(material["artifact_path"])).is_relative_to(tmp_path / "work")
