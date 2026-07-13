from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Callable

from .models import DocumentArtifact, DocumentFormat, DocumentSource
from .parsers import parse_docx_document, parse_pdf_document, parse_pptx_document
from .security import DocumentSecurityValidator


DocumentParser = Callable[..., dict[str, object]]


class DocumentService:
    def __init__(
        self,
        *,
        max_file_bytes: int = 50 * 1024 * 1024,
        max_archive_entries: int = 10_000,
        max_uncompressed_bytes: int = 250 * 1024 * 1024,
        max_compression_ratio: float = 500,
        parsers: dict[DocumentFormat, DocumentParser] | None = None,
    ):
        self._validator = DocumentSecurityValidator(
            max_file_bytes=max_file_bytes,
            max_archive_entries=max_archive_entries,
            max_uncompressed_bytes=max_uncompressed_bytes,
            max_compression_ratio=max_compression_ratio,
        )
        self._parsers = parsers or {
            DocumentFormat.DOCX: parse_docx_document,
            DocumentFormat.PDF: parse_pdf_document,
            DocumentFormat.PPTX: parse_pptx_document,
        }

    def parse(
        self,
        path: str | Path,
        *,
        allowed_root: str | Path,
        work_dir: str | Path,
    ) -> DocumentArtifact:
        validated = self._validator.validate(path, allowed_root=allowed_root)
        work_path = _resolve_work_dir(work_dir, allowed_root=allowed_root)
        artifact_id = validated.sha256[:16]
        artifact_dir = work_path / "documents" / artifact_id
        asset_dir = artifact_dir / "assets"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        parser = self._parsers[validated.format]
        parsed = parser(validated.path, asset_dir=asset_dir)
        blocks = tuple(parsed.get("blocks") or ())
        full_text = "\n\n".join(block.text.strip() for block in blocks if block.text.strip())
        artifact = DocumentArtifact(
            artifact_id=artifact_id,
            format=validated.format,
            source=DocumentSource(
                original_name=validated.path.name,
                path=str(validated.path),
                size_bytes=validated.size_bytes,
            ),
            sha256=validated.sha256,
            full_text=full_text,
            blocks=blocks,
            page_count=parsed.get("page_count") if isinstance(parsed.get("page_count"), int) else None,
            assets=tuple(parsed.get("assets") or ()),
            warnings=tuple(parsed.get("warnings") or ()),
            metadata=dict(parsed.get("metadata") or {}),
        )
        document_path = artifact_dir / "document.json"
        artifact = replace(artifact, artifact_path=str(document_path))
        temporary = artifact_dir / "document.json.tmp"
        temporary.write_text(
            json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(document_path)
        return artifact


def _resolve_work_dir(work_dir: str | Path, *, allowed_root: str | Path) -> Path:
    input_root = Path(allowed_root).resolve()
    task_root = input_root.parent
    expected_work_root = task_root / "work"
    candidate = Path(work_dir).resolve()
    if candidate != expected_work_root and expected_work_root not in candidate.parents:
        raise ValueError("文档中间产物必须保存在当前任务 work 目录")
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate
