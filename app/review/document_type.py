"""文档类型识别.

基于文件名和文档头内容,判断是"内参周报"还是"半月报".
"""

from __future__ import annotations

from enum import Enum, auto


class DocumentType(Enum):
    """审核文档类型."""

    NEI_CAN = auto()       # 微众银行信息内参周报
    HALF_MONTHLY = auto()  # 微众银行信息动态半月报
    GENERAL = auto()       # 通用文档审核
    OFFICIAL_FORMAT = auto()  # 用户显式触发的独立公文格式审核


def detect_document_type(filename: str | None, paragraphs: list[str]) -> DocumentType:
    """识别文档类型.

    识别逻辑(按优先级):
      1. 文件名含"半月报" → 半月报
      2. 文件名含"内参"或"周报" → 内参周报
      3. 前 5 段内容含"半月报" → 半月报
      4. 前 5 段内容含"内参"或"周报" → 内参周报
      5. 默认 → 通用审核
    """
    name = (filename or "").lower()

    if "半月报" in name:
        return DocumentType.HALF_MONTHLY
    if "内参" in name or "周报" in name:
        return DocumentType.NEI_CAN

    header = "\n".join(paragraphs[:5]).lower()
    if "半月报" in header:
        return DocumentType.HALF_MONTHLY
    if "内参" in header or "周报" in header:
        return DocumentType.NEI_CAN

    return DocumentType.GENERAL


def document_type_label(doc_type: DocumentType) -> str:
    """返回人类可读的文档类型名称."""
    if doc_type == DocumentType.HALF_MONTHLY:
        return "半月报"
    if doc_type == DocumentType.GENERAL:
        return "通用审核"
    if doc_type == DocumentType.OFFICIAL_FORMAT:
        return "公文格式审核"
    return "内参周报"
