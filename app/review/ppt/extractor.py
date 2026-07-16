from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re
from typing import cast

from app.platform.documents import DocumentFormat, DocumentService

from .models import (
    PptElement,
    PptElementKind,
    PptReviewDocument,
    PptReviewInputError,
    PptSlide,
)


_LOCATION_RE = re.compile(r"^slide:(\d+)(?:/shape:(\d+))?$")
_AUDITABLE_KINDS = {"text", "table", "chart"}


def extract_ppt_document(path: Path, *, task_dir: Path) -> PptReviewDocument:
    """安全解析当前任务内 PPTX，并转成独立审核结构。"""
    resolved_task = task_dir.resolve(strict=True)
    input_root = (resolved_task / "input").resolve(strict=True)
    resolved_path = path.resolve(strict=True)
    if not resolved_path.is_relative_to(input_root):
        raise ValueError("PPT审核文件必须位于当前任务 input 目录")

    work_dir = resolved_task / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    artifact = DocumentService().parse(
        resolved_path,
        allowed_root=input_root,
        work_dir=work_dir,
    )
    if artifact.format != DocumentFormat.PPTX:
        raise PptReviewInputError("PPT审核只支持 .pptx 文件")

    elements_by_slide: dict[int, list[PptElement]] = defaultdict(list)
    for block in artifact.blocks:
        if block.kind not in _AUDITABLE_KINDS or not block.text.strip():
            continue
        match = _LOCATION_RE.match(block.location)
        if not match:
            continue
        slide_number = int(match.group(1))
        elements_by_slide[slide_number].append(
            PptElement(
                element_id=block.location,
                slide_number=slide_number,
                kind=cast(PptElementKind, block.kind),
                text=block.text.strip(),
                bbox=block.bbox,
            )
        )

    if not elements_by_slide:
        raise PptReviewInputError("PPT中没有可审核的可编辑文字")

    page_count = artifact.page_count or 0
    return PptReviewDocument(
        filename=resolved_path.name,
        page_count=page_count,
        slides=tuple(
            PptSlide(
                slide_number=number,
                elements=tuple(elements_by_slide.get(number, ())),
            )
            for number in range(1, page_count + 1)
        ),
        excluded_image_count=len(artifact.assets),
        warnings=tuple(warning.message for warning in artifact.warnings),
    )
