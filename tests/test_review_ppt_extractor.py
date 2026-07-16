from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

import pytest

from app.review.ppt.extractor import extract_ppt_document
from app.review.ppt.models import PptReviewInputError


def _tiny_png() -> BytesIO:
    payload = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    return BytesIO(payload)


def _make_pptx(
    path: Path,
    *,
    include_text: bool = True,
    include_table: bool = True,
    include_picture: bool = True,
    include_notes: bool = True,
) -> None:
    pptx = pytest.importorskip("pptx")
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    if include_text:
        textbox = slide.shapes.add_textbox(100_000, 100_000, 5_000_000, 800_000)
        textbox.text_frame.text = "经营情况"
    if include_table:
        table = slide.shapes.add_table(1, 2, 100_000, 1_100_000, 5_000_000, 800_000)
        table.table.cell(0, 0).text = "客户数"
        table.table.cell(0, 1).text = "100万户"
    if include_picture:
        slide.shapes.add_picture(_tiny_png(), 100_000, 2_100_000)
    if include_notes:
        slide.notes_slide.notes_text_frame.text = "演讲备注不应进入审核"
    presentation.save(path)


def test_extract_ppt_document_keeps_editable_content_and_excludes_notes_and_images(
    tmp_path: Path,
):
    task_dir = tmp_path / "task"
    input_dir = task_dir / "input"
    input_dir.mkdir(parents=True)
    path = input_dir / "经营汇报.pptx"
    _make_pptx(path)

    document = extract_ppt_document(path, task_dir=task_dir)

    assert document.filename == "经营汇报.pptx"
    assert document.page_count == 1
    elements = document.slides[0].elements
    assert {element.kind for element in elements} == {"text", "table"}
    assert any("经营情况" in element.text for element in elements)
    assert any("客户数\t100万户" in element.text for element in elements)
    assert all("演讲备注" not in element.text for element in elements)
    assert document.excluded_image_count == 1
    assert (task_dir / "work" / "documents").is_dir()


def test_extract_ppt_document_rejects_path_outside_task_input(tmp_path: Path):
    task_dir = tmp_path / "task"
    (task_dir / "input").mkdir(parents=True)
    outside = tmp_path / "outside.pptx"
    _make_pptx(outside, include_table=False, include_picture=False, include_notes=False)

    with pytest.raises(ValueError, match="任务 input"):
        extract_ppt_document(outside, task_dir=task_dir)


def test_extract_ppt_document_rejects_ppt_without_editable_text(tmp_path: Path):
    task_dir = tmp_path / "task"
    input_dir = task_dir / "input"
    input_dir.mkdir(parents=True)
    path = input_dir / "纯图片汇报.pptx"
    _make_pptx(
        path,
        include_text=False,
        include_table=False,
        include_picture=True,
        include_notes=True,
    )

    with pytest.raises(PptReviewInputError, match="没有可审核的可编辑文字"):
        extract_ppt_document(path, task_dir=task_dir)
