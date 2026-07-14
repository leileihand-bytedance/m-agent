from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from ..models import DocumentAsset, DocumentBlock, DocumentWarning


def parse_docx_document(path: Path, *, asset_dir: Path) -> dict[str, object]:
    document = Document(str(path))
    blocks: list[DocumentBlock] = []
    assets: list[DocumentAsset] = []
    image_locations: list[str] = []
    counter = 0
    paragraph_index = 0
    table_index = 0
    image_index = 0

    def add(kind: str, text: str, location: str, style: dict[str, Any] | None = None) -> None:
        nonlocal counter
        clean = text.strip()
        if not clean:
            return
        counter += 1
        blocks.append(
            DocumentBlock(
                block_id=f"block-{counter:05d}",
                kind=kind,
                text=clean,
                location=location,
                style=style or {},
            )
        )

    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph_index += 1
            paragraph = Paragraph(child, document._body)
            location = f"body:paragraph:{paragraph_index}"
            add("paragraph", paragraph.text, location, _paragraph_style(paragraph))
        elif child.tag == qn("w:tbl"):
            table_index += 1
            table = Table(child, document._body)
            location = f"body:table:{table_index}"
            rows = ["\t".join(cell.text.strip() for cell in row.cells) for row in table.rows]
            add("table", "\n".join(row for row in rows if row.strip()), location)
        else:
            continue

        relationship_ids = _image_relationship_ids(child)
        for relationship_id in relationship_ids:
            image_index += 1
            image_location = f"{location}:image:{image_index}"
            asset = _persist_image_asset(
                document=document,
                relationship_id=relationship_id,
                asset_dir=asset_dir,
                asset_index=image_index,
                location=image_location,
            )
            if asset is not None:
                assets.append(asset)
            image_locations.append(image_location)
            add("image_reminder", _image_reminder(path), image_location)

    seen_headers: set[str] = set()
    seen_footers: set[str] = set()
    for section_index, section in enumerate(document.sections, 1):
        for kind, container, seen in (
            ("header", section.header, seen_headers),
            ("footer", section.footer, seen_footers),
        ):
            text = "\n".join(paragraph.text.strip() for paragraph in container.paragraphs if paragraph.text.strip())
            if text and text not in seen:
                seen.add(text)
                add(kind, text, f"{kind}:section:{section_index}")

    core = document.core_properties
    metadata = {
        "title": core.title or "",
        "subject": core.subject or "",
        "author": core.author or "",
        "keywords": core.keywords or "",
    }
    return {
        "blocks": tuple(blocks),
        "assets": tuple(assets),
        "warnings": (
            DocumentWarning(
                code="embedded_image_unread",
                message="文档包含图片；未读取图片内容，已在原位置加入人工评估提醒。",
                locations=tuple(image_locations),
            ),
        )
        if image_locations
        else (),
        "page_count": None,
        "metadata": metadata,
    }


def _image_relationship_ids(element: Any) -> list[str]:
    relationship_ids: list[str] = []
    for blip in element.xpath(".//a:blip"):
        relationship_id = str(blip.get(qn("r:embed")) or "").strip()
        if relationship_id and relationship_id not in relationship_ids:
            relationship_ids.append(relationship_id)
    return relationship_ids


def _persist_image_asset(
    *,
    document: Any,
    relationship_id: str,
    asset_dir: Path,
    asset_index: int,
    location: str,
) -> DocumentAsset | None:
    image_part = document.part.related_parts.get(relationship_id)
    if image_part is None or not hasattr(image_part, "blob"):
        return None
    suffix = Path(str(getattr(image_part, "partname", ""))).suffix.lower() or ".bin"
    asset_dir.mkdir(parents=True, exist_ok=True)
    target = asset_dir / f"image-{asset_index:04d}{suffix}"
    target.write_bytes(image_part.blob)
    return DocumentAsset(
        asset_id=f"asset-{asset_index:05d}",
        kind="image",
        location=location,
        path=str(target),
        content_type=str(getattr(image_part, "content_type", "") or ""),
    )


def _image_reminder(path: Path) -> str:
    label = _document_source_label(path)
    return f"【提醒：{label}素材含图片，请评估是否需要】"


def _document_source_label(path: Path) -> str:
    stem = path.stem.strip()
    department_matches = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,20}部", stem)
    if department_matches:
        return department_matches[-1]
    return re.sub(r"(?:素材|材料)$", "", stem) or stem or "该部门"


def _paragraph_style(paragraph: Any) -> dict[str, Any]:
    style: dict[str, Any] = {
        "paragraph_style": str(getattr(paragraph.style, "name", "") or ""),
        "alignment": str(paragraph.alignment or ""),
    }
    run = next((item for item in paragraph.runs if item.text.strip()), None)
    if run is not None:
        style.update(
            {
                "font_name": str(run.font.name or ""),
                "font_size_pt": float(run.font.size.pt) if run.font.size else None,
                "bold": run.bold,
                "italic": run.italic,
            }
        )
    return {key: value for key, value in style.items() if value not in (None, "")}
