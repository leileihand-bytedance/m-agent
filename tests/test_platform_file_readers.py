from pathlib import Path
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.platform.builtin_tools as builtin_tools  # noqa: E402
from app.platform.builtin_tools import read_document_file, read_pdf_file, read_word_file  # noqa: E402


def test_read_word_file_extracts_docx_text_inside_allowed_root(tmp_path):
    docx_path = tmp_path / "input" / "sample.docx"
    docx_path.parent.mkdir()
    document_xml = """
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:r><w:t>第一段</w:t></w:r></w:p>
        <w:p><w:r><w:t>第二段</w:t></w:r></w:p>
      </w:body>
    </w:document>
    """
    with zipfile.ZipFile(docx_path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)

    result = read_word_file(str(docx_path), allowed_root=tmp_path)

    assert result["path"] == str(docx_path.resolve())
    assert result["title"] == "sample.docx"
    assert result["text"] == "第一段\n第二段"


def test_read_word_file_rejects_path_outside_allowed_root(tmp_path):
    outside = tmp_path.parent / "outside.docx"

    with pytest.raises(ValueError, match="不允许读取当前任务目录之外的文件"):
        read_word_file(str(outside), allowed_root=tmp_path)


def test_read_pdf_file_uses_extractor_inside_allowed_root(tmp_path):
    pdf_path = tmp_path / "input" / "sample.pdf"
    pdf_path.parent.mkdir()
    pdf_path.write_bytes(b"%PDF-1.4")

    result = read_pdf_file(
        str(pdf_path),
        allowed_root=tmp_path,
        extractor=lambda path: "PDF 正文",
    )

    assert result["path"] == str(pdf_path.resolve())
    assert result["title"] == "sample.pdf"
    assert result["text"] == "PDF 正文"


def test_read_pdf_file_rejects_path_outside_allowed_root(tmp_path):
    outside = tmp_path.parent / "outside.pdf"

    with pytest.raises(ValueError, match="不允许读取当前任务目录之外的文件"):
        read_pdf_file(str(outside), allowed_root=tmp_path, extractor=lambda path: "x")


def test_read_document_file_persists_standard_artifact_in_work_dir(tmp_path):
    pdf_path = tmp_path / "input" / "sample.pdf"
    pdf_path.parent.mkdir()
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with pdf_path.open("wb") as stream:
        writer.write(stream)

    result = read_document_file(
        str(pdf_path),
        allowed_root=tmp_path / "input",
        work_dir=tmp_path / "work",
    )

    assert result["document_format"] == "pdf"
    assert result["page_count"] == 1
    assert result["artifact_path"].endswith("document.json")
    assert Path(result["artifact_path"]).is_file()
    assert result["warnings"]


def test_read_document_file_can_enable_ocr_only_through_explicit_option(tmp_path, monkeypatch):
    calls = []

    class FakeArtifact:
        def to_material(self):
            return {"text": "OCR text"}

    class FakeService:
        def __init__(self, *, max_file_bytes):
            calls.append(("init", max_file_bytes))

        def parse(self, path, **kwargs):
            calls.append(("parse", path, kwargs))
            return FakeArtifact()

    monkeypatch.setattr(builtin_tools, "DocumentService", FakeService)

    result = read_document_file(
        "scan.pdf",
        allowed_root=tmp_path / "input",
        work_dir=tmp_path / "work",
        ocr_scanned_pages=True,
    )

    assert result == {"text": "OCR text"}
    assert calls[-1][2]["ocr_scanned_pages"] is True
    assert calls[-1][2]["render_pages"] is False
