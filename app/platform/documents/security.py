from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

from .models import DocumentFormat


OOXML_MAIN_CONTENT_TYPES = {
    DocumentFormat.DOCX: (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        "word/document.xml",
    ),
    DocumentFormat.PPTX: (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
        "ppt/presentation.xml",
    ),
}

CONTENT_TYPES_NAMESPACE = (
    "http://schemas.openxmlformats.org/package/2006/content-types"
)
CONTENT_TYPE_DEFAULT_TAG = f"{{{CONTENT_TYPES_NAMESPACE}}}Default"
CONTENT_TYPE_OVERRIDE_TAG = f"{{{CONTENT_TYPES_NAMESPACE}}}Override"
EMBEDDED_XLSB_CONTENT_TYPE = (
    "application/vnd.ms-excel.sheet.binary.macroenabled.12"
)


class DocumentSecurityError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedDocument:
    path: Path
    format: DocumentFormat
    size_bytes: int
    sha256: str


class DocumentSecurityValidator:
    def __init__(
        self,
        *,
        max_file_bytes: int = 50 * 1024 * 1024,
        max_archive_entries: int = 10_000,
        max_uncompressed_bytes: int = 250 * 1024 * 1024,
        max_compression_ratio: float = 500,
    ):
        self.max_file_bytes = max(1, int(max_file_bytes))
        self.max_archive_entries = max(1, int(max_archive_entries))
        self.max_uncompressed_bytes = max(1, int(max_uncompressed_bytes))
        self.max_compression_ratio = max(1.0, float(max_compression_ratio))

    def validate(self, path: str | Path, *, allowed_root: str | Path) -> ValidatedDocument:
        file_path = _resolve_allowed_file(path, allowed_root)
        document_format = _format_from_suffix(file_path.suffix.lower())
        size = file_path.stat().st_size
        if size <= 0:
            raise DocumentSecurityError("文件为空，无法处理")
        if size > self.max_file_bytes:
            raise DocumentSecurityError("文件超过底座允许的大小上限")

        if document_format == DocumentFormat.PDF:
            with file_path.open("rb") as stream:
                signature = stream.read(5)
            if signature != b"%PDF-":
                raise DocumentSecurityError("文件内容与 PDF 格式不一致")
        else:
            self._validate_ooxml(file_path, document_format)

        return ValidatedDocument(
            path=file_path,
            format=document_format,
            size_bytes=size,
            sha256=_sha256_file(file_path),
        )

    def _validate_ooxml(self, path: Path, document_format: DocumentFormat) -> None:
        try:
            archive = zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            raise DocumentSecurityError("Office 文件结构损坏或格式伪造") from exc

        with archive:
            infos = archive.infolist()
            if len(infos) > self.max_archive_entries:
                raise DocumentSecurityError("Office 文件内部条目过多")
            total_uncompressed = sum(max(0, info.file_size) for info in infos)
            if total_uncompressed > self.max_uncompressed_bytes:
                raise DocumentSecurityError("Office 文件展开后体积过大")
            for info in infos:
                if info.flag_bits & 0x1:
                    raise DocumentSecurityError("暂不支持加密的 Office 文件")
                if info.file_size >= 1024 * 1024:
                    ratio = info.file_size / max(1, info.compress_size)
                    if ratio > self.max_compression_ratio:
                        raise DocumentSecurityError("Office 文件压缩比异常，已停止处理")

            names = {info.filename for info in infos}
            expected_content_type, expected_part = OOXML_MAIN_CONTENT_TYPES[document_format]
            if "[Content_Types].xml" not in names or expected_part not in names:
                raise DocumentSecurityError("Office 文件内容与扩展名不一致")
            try:
                content_types = archive.read("[Content_Types].xml").decode("utf-8", errors="replace")
            except Exception as exc:
                raise DocumentSecurityError("Office 文件类型信息无法读取") from exc
            try:
                content_type_root = ET.fromstring(content_types)
            except ET.ParseError as exc:
                raise DocumentSecurityError("Office 文件类型信息无法读取") from exc
            content_type_entries = list(content_type_root)
            has_vba_project = any(
                "vbaproject" in name.lower() for name in names
            ) or any(
                "vbaproject" in str(item.attrib.get("PartName", "")).lower()
                or "vbaproject" in str(item.attrib.get("ContentType", "")).lower()
                for item in content_type_entries
            ) or _has_vba_project_relationship(
                archive,
                names,
            )
            if has_vba_project:
                raise DocumentSecurityError("暂不支持包含宏的 Office 文件")

            for item in content_type_entries:
                content_type = str(item.attrib.get("ContentType", ""))
                if "macroenabled" not in content_type.lower():
                    continue
                if not _is_allowed_pptx_embedded_xlsb(
                    item,
                    names=names,
                    document_format=document_format,
                ):
                    raise DocumentSecurityError("暂不支持包含宏的 Office 文件")

            canonical_main_part = f"/{expected_part}"
            main_overrides = [
                item
                for item in content_type_entries
                if item.tag == CONTENT_TYPE_OVERRIDE_TAG
                and item.attrib.get("PartName") == canonical_main_part
            ]
            if len(main_overrides) != 1:
                raise DocumentSecurityError("Office 文件内容与扩展名不一致")
            main_content_type = str(main_overrides[0].attrib.get("ContentType", ""))
            if main_content_type.lower() != expected_content_type.lower():
                raise DocumentSecurityError("Office 文件内容与扩展名不一致")


def _has_vba_project_relationship(
    archive: zipfile.ZipFile,
    names: set[str],
) -> bool:
    for name in names:
        if not name.lower().endswith(".rels"):
            continue
        try:
            relationship_root = ET.fromstring(archive.read(name))
        except (ET.ParseError, KeyError, RuntimeError, zipfile.BadZipFile) as exc:
            raise DocumentSecurityError("Office 文件关系信息无法读取") from exc
        if any(
            "vbaproject" in str(item.attrib.get("Type", "")).lower()
            for item in relationship_root.iter()
        ):
            return True
    return False


def _is_allowed_pptx_embedded_xlsb(
    item: ET.Element,
    *,
    names: set[str],
    document_format: DocumentFormat,
) -> bool:
    content_type = str(item.attrib.get("ContentType", "")).lower()
    if (
        document_format != DocumentFormat.PPTX
        or content_type != EMBEDDED_XLSB_CONTENT_TYPE
    ):
        return False

    if item.tag == CONTENT_TYPE_DEFAULT_TAG:
        if str(item.attrib.get("Extension", "")).lower() != "xlsb":
            return False
        xlsb_parts = [name for name in names if name.lower().endswith(".xlsb")]
        return bool(xlsb_parts) and all(
            _is_canonical_pptx_embedding(name) for name in xlsb_parts
        )

    if item.tag == CONTENT_TYPE_OVERRIDE_TAG:
        part_name = str(item.attrib.get("PartName", ""))
        if not part_name.startswith("/") or part_name.startswith("//"):
            return False
        archive_name = part_name[1:]
        return archive_name in names and _is_canonical_pptx_embedding(archive_name)

    return False


def _is_canonical_pptx_embedding(name: str) -> bool:
    prefix = "ppt/embeddings/"
    if not name.startswith(prefix):
        return False
    filename = name[len(prefix):]
    return (
        bool(filename)
        and filename.endswith(".xlsb")
        and "/" not in filename
        and "\\" not in filename
        and "%" not in filename
        and filename not in {".", ".."}
    )


def _format_from_suffix(suffix: str) -> DocumentFormat:
    mapping = {
        ".docx": DocumentFormat.DOCX,
        ".pdf": DocumentFormat.PDF,
        ".pptx": DocumentFormat.PPTX,
    }
    if suffix not in mapping:
        raise DocumentSecurityError("暂不支持该文件格式；当前支持 Word、PDF 和 PPTX")
    return mapping[suffix]


def _resolve_allowed_file(path: str | Path, allowed_root: str | Path) -> Path:
    root = Path(allowed_root).resolve()
    file_path = Path(path).resolve()
    if root != file_path and root not in file_path.parents:
        raise DocumentSecurityError("不允许读取当前任务目录之外的文件")
    if not file_path.exists() or not file_path.is_file():
        raise DocumentSecurityError("文件不存在或不是普通文件")
    return file_path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
