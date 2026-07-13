from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document

from ..models import DocumentBlock


def parse_docx_document(path: Path, *, asset_dir: Path) -> dict[str, object]:
    document = Document(str(path))
    blocks: list[DocumentBlock] = []
    counter = 0

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

    for index, paragraph in enumerate(document.paragraphs, 1):
        add("paragraph", paragraph.text, f"body:paragraph:{index}", _paragraph_style(paragraph))

    for table_index, table in enumerate(document.tables, 1):
        rows = ["\t".join(cell.text.strip() for cell in row.cells) for row in table.rows]
        add("table", "\n".join(row for row in rows if row.strip()), f"body:table:{table_index}")

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
        "assets": (),
        "warnings": (),
        "page_count": None,
        "metadata": metadata,
    }


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
