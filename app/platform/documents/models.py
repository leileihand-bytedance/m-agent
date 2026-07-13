from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class DocumentFormat(StrEnum):
    DOCX = "docx"
    PDF = "pdf"
    PPTX = "pptx"


@dataclass(frozen=True)
class DocumentSource:
    original_name: str
    path: str
    size_bytes: int


@dataclass(frozen=True)
class DocumentBlock:
    block_id: str
    kind: str
    text: str
    location: str
    style: dict[str, Any] = field(default_factory=dict)
    bbox: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class DocumentAsset:
    asset_id: str
    kind: str
    location: str
    path: str = ""
    content_type: str = ""
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class DocumentWarning:
    code: str
    message: str
    locations: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentArtifact:
    artifact_id: str
    format: DocumentFormat
    source: DocumentSource
    sha256: str
    full_text: str
    blocks: tuple[DocumentBlock, ...]
    page_count: int | None = None
    assets: tuple[DocumentAsset, ...] = ()
    warnings: tuple[DocumentWarning, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    artifact_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["format"] = self.format.value
        return payload

    def to_material(self, *, max_chars: int = 12000) -> dict[str, object]:
        text, complete = _sample_blocks(self.blocks, self.full_text, max_chars=max_chars)
        return {
            "title": self.source.original_name,
            "text": text,
            "path": self.source.path,
            "source": "uploaded_file",
            "document_format": self.format.value,
            "page_count": self.page_count,
            "artifact_path": self.artifact_path,
            "content_complete": complete,
            "warnings": [warning.message for warning in self.warnings],
            "warning_codes": [warning.code for warning in self.warnings],
        }


def _sample_blocks(
    blocks: tuple[DocumentBlock, ...],
    full_text: str,
    *,
    max_chars: int,
) -> tuple[str, bool]:
    if max_chars <= 0:
        return "", not bool(full_text)
    if len(full_text) <= max_chars:
        return full_text, True

    nonempty = [block for block in blocks if block.text.strip()]
    if len(nonempty) < 2:
        return full_text[: max(0, max_chars - 8)] + "\n[内容较长]", False

    marker = "\n\n[中间内容已按位置抽样，完整解析结果保存在任务 work 目录]\n\n"
    budget = max(1, max_chars - len(marker))
    target_count = min(len(nonempty), 9)
    indices = {
        round(index * (len(nonempty) - 1) / max(1, target_count - 1))
        for index in range(target_count)
    }
    selected = [nonempty[index] for index in sorted(indices)]
    per_block = max(32, budget // max(1, len(selected)))
    snippets = [
        f"[{block.location}] {block.text.strip()[:per_block]}"
        for block in selected
    ]
    midpoint = max(1, len(snippets) // 2)
    sampled = "\n\n".join(snippets[:midpoint]) + marker + "\n\n".join(snippets[midpoint:])
    return sampled[:max_chars], False
