from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from ..models import DocumentBlock, DocumentWarning
from ..security import DocumentSecurityError


def parse_pdf_document(path: Path, *, asset_dir: Path) -> dict[str, object]:
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        raise DocumentSecurityError("暂不支持加密或带密码的 PDF 文件")

    blocks: list[DocumentBlock] = []
    warnings: list[DocumentWarning] = []
    ocr_locations: list[str] = []
    failed_locations: list[str] = []
    for index, page in enumerate(reader.pages, 1):
        location = f"page:{index}"
        try:
            try:
                text = page.extract_text(extraction_mode="layout") or ""
            except (TypeError, ValueError):
                text = page.extract_text() or ""
        except KeyError as exc:
            # 合法空白页可能没有 /Contents，不应被误记为解析器故障。
            text = ""
            if "Contents" not in str(exc):
                failed_locations.append(location)
        except Exception:
            text = ""
            failed_locations.append(location)
        clean = text.replace("\x00", "").strip()
        if len("".join(clean.split())) < 20:
            ocr_locations.append(location)
        if clean:
            blocks.append(
                DocumentBlock(
                    block_id=f"page-{index:05d}",
                    kind="page",
                    text=clean,
                    location=location,
                )
            )

    if ocr_locations:
        warnings.append(
            DocumentWarning(
                code="ocr_required",
                message="部分 PDF 页面未提取到足够文字，可能是扫描页，需要按需 OCR。",
                locations=tuple(ocr_locations),
            )
        )
    if failed_locations:
        warnings.append(
            DocumentWarning(
                code="page_parse_failed",
                message="部分 PDF 页面解析失败，完整性需要人工确认。",
                locations=tuple(failed_locations),
            )
        )

    metadata = {str(key).lstrip("/"): str(value or "") for key, value in (reader.metadata or {}).items()}
    return {
        "blocks": tuple(blocks),
        "assets": (),
        "warnings": tuple(warnings),
        "page_count": len(reader.pages),
        "metadata": metadata,
    }
