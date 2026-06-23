"""Docx 解析器 - 从 .docx 文件中提取结构化文本."""

from dataclasses import dataclass
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


@dataclass(frozen=True)
class ParsedDocxResult:
    """解析结果."""

    paragraphs: list[str]
    total_chars: int
    total_paragraphs: int


def parse_docx(path: Path) -> ParsedDocxResult:
    """解析 .docx 文件，返回段落列表及统计信息.

    复用 app/main.py 中的 extract_docx_text 逻辑。
    """
    with zipfile.ZipFile(path) as archive:
        document_xml = archive.read("word/document.xml")

    root = ET.fromstring(document_xml)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    paragraphs: list[str] = []
    for paragraph in root.iter(f"{namespace}p"):
        parts = [
            node.text
            for node in paragraph.iter(f"{namespace}t")
            if node.text and node.text.strip()
        ]
        if parts:
            paragraphs.append("".join(parts))

    total_chars = sum(len(p) for p in paragraphs)
    return ParsedDocxResult(
        paragraphs=paragraphs,
        total_chars=total_chars,
        total_paragraphs=len(paragraphs),
    )