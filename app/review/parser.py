"""Docx 解析器 - 从 .docx 文件中提取结构化文本."""

from __future__ import annotations

import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from docx import Document as open_docx
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


@dataclass(frozen=True)
class ParsedDocxResult:
    """解析结果."""

    paragraphs: list[str]
    total_chars: int
    total_paragraphs: int
    numbering: tuple[int | None, ...] = ()


def paragraph_text(paragraph: Paragraph) -> str:
    """返回 paragraph 的可见文本."""
    return "".join(run.text for run in paragraph.runs if run.text)


def _load_numbering_defs(path: Path) -> dict[int, dict[int, int]]:
    """从 .docx 读取编号定义，返回 numId -> {ilvl -> start_value}."""
    try:
        with zipfile.ZipFile(path) as z:
            if "word/numbering.xml" not in z.namelist():
                return {}

            root = ET.fromstring(z.read("word/numbering.xml"))

            abstract_levels: dict[int, dict[int, int]] = {}
            for an in root.findall(f"{{{_W_NS}}}abstractNum"):
                aid = int(an.get(f"{{{_W_NS}}}abstractNumId"))
                levels: dict[int, int] = {}
                for lvl in an.findall(f"{{{_W_NS}}}lvl"):
                    ilvl = int(lvl.get(f"{{{_W_NS}}}ilvl"))
                    start_el = lvl.find(f"{{{_W_NS}}}start")
                    levels[ilvl] = (
                        int(start_el.get(f"{{{_W_NS}}}val"))
                        if start_el is not None
                        else 1
                    )
                abstract_levels[aid] = levels

            num_mapping: dict[int, int] = {}
            for num in root.findall(f"{{{_W_NS}}}num"):
                nid = int(num.get(f"{{{_W_NS}}}numId"))
                aid_el = num.find(f"{{{_W_NS}}}abstractNumId")
                if aid_el is not None:
                    num_mapping[nid] = int(aid_el.get(f"{{{_W_NS}}}val"))

            result: dict[int, dict[int, int]] = {}
            for nid, aid in num_mapping.items():
                result[nid] = abstract_levels.get(aid, {}).copy()
            return result
    except Exception:
        return {}


def _paragraph_number(
    paragraph: Paragraph,
    numbering_defs: dict[int, dict[int, int]],
    counters: dict[int, dict[int, int]],
) -> int | None:
    """提取段落 ilvl=0 的 decimal 编号渲染值，无编号返回 None."""
    p_elem = paragraph._p
    ppr = p_elem.find(qn("w:pPr"))
    if ppr is None:
        return None

    numpr = ppr.find(qn("w:numPr"))
    if numpr is None:
        return None

    numid_el = numpr.find(qn("w:numId"))
    ilvl_el = numpr.find(qn("w:ilvl"))
    if numid_el is None or ilvl_el is None:
        return None

    numid = int(numid_el.get(qn("w:val")))
    ilvl = int(ilvl_el.get(qn("w:val")))
    if ilvl != 0:
        return None

    if numid not in counters:
        counters[numid] = {}
    if ilvl not in counters[numid]:
        start = numbering_defs.get(numid, {}).get(ilvl, 1)
        counters[numid][ilvl] = start

    current = counters[numid][ilvl]
    counters[numid][ilvl] = current + 1
    return current


def _remove_null_rels_from_xml(data: bytes) -> bytes:
    """从 OPC 关系 XML 中移除指向 NULL 的项,避免 python-docx 抛 KeyError."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return data

    namespaces = {
        "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
        "r": "http://schemas.openxmlformats.org/package/2006/relationships",
    }

    changed = False
    for override in list(root.findall(".//ct:Override", namespaces)):
        part_name = override.get("PartName", "")
        if part_name and "NULL" in part_name.upper():
            root.remove(override)
            changed = True

    for rel in list(root.findall(".//r:Relationship", namespaces)):
        target = rel.get("Target", "")
        if target and "NULL" in target.upper():
            root.remove(rel)
            changed = True

    if not changed:
        return data

    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)


def _sanitize_docx(input_path: Path, output_path: Path) -> None:
    """复制 .docx 并清理其中指向 NULL 的 OPC 关系."""
    with zipfile.ZipFile(input_path, "r") as zin:
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "[Content_Types].xml" or item.filename.endswith(
                    ".rels"
                ):
                    data = _remove_null_rels_from_xml(data)
                zout.writestr(item, data)


def _iter_block_items(parent: DocxDocument | _Cell) -> Iterator[Paragraph | Table]:
    """按文档出现顺序遍历段落和表格."""
    if isinstance(parent, DocxDocument):
        parent_elm = parent.element.body
    elif isinstance(parent, _Cell):
        parent_elm = parent._tc
    else:
        raise TypeError(f"unsupported parent type: {type(parent)!r}")

    paragraph_tag = qn("w:p")
    table_tag = qn("w:tbl")

    for child in parent_elm.iterchildren():
        if child.tag == paragraph_tag:
            yield Paragraph(child, parent)
        elif child.tag == table_tag:
            yield Table(child, parent)


def iter_reviewable_paragraphs(
    parent: DocxDocument | _Cell,
    *,
    _seen_cells: set[object] | None = None,
) -> Iterator[Paragraph]:
    """遍历审核链路里真正参与编号的非空段落.

    约束:
      1. 跳过纯空段,保持与旧 parser 的编号习惯一致
      2. 递归包含表格单元格中的段落
      3. 去重合并单元格,避免同一 cell 被重复遍历
    """
    seen_cells = _seen_cells if _seen_cells is not None else set()

    for block in _iter_block_items(parent):
        if isinstance(block, Paragraph):
            if paragraph_text(block).strip():
                yield block
            continue

        for row in block.rows:
            for cell in row.cells:
                cell_element = cell._tc
                if cell_element in seen_cells:
                    continue
                # 保留 XML 元素本身，避免长表格遍历时临时对象的 id 被复用。
                seen_cells.add(cell_element)
                yield from iter_reviewable_paragraphs(cell, _seen_cells=seen_cells)


def open_docx_sanitized(path: Path | str) -> DocxDocument:
    """用 python-docx 打开 .docx,对包含 NULL 关系的损坏包自动清理后重试."""
    try:
        return open_docx(str(path))
    except KeyError as exc:
        if "NULL" not in str(exc).upper():
            raise
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            sanitized_path = Path(tmp.name)
        try:
            _sanitize_docx(Path(path), sanitized_path)
            return open_docx(str(sanitized_path))
        finally:
            sanitized_path.unlink(missing_ok=True)


def parse_docx(path: Path) -> ParsedDocxResult:
    """解析 .docx 文件，返回段落列表及统计信息.

    使用 python-docx 遍历审核链路里的真实可见段落。
    这样读取和回写 marked 文档时使用同一套段号口径。
    对包含 NULL 关系的损坏包做自动清理后重试。
    """
    document = open_docx_sanitized(path)
    numbering_defs = _load_numbering_defs(path)
    counters: dict[int, dict[int, int]] = {}

    paragraphs: list[str] = []
    numbering: list[int | None] = []

    for paragraph in iter_reviewable_paragraphs(document):
        paragraphs.append(paragraph_text(paragraph).strip())
        numbering.append(_paragraph_number(paragraph, numbering_defs, counters))

    total_chars = sum(len(p) for p in paragraphs)
    return ParsedDocxResult(
        paragraphs=paragraphs,
        total_chars=total_chars,
        total_paragraphs=len(paragraphs),
        numbering=tuple(numbering),
    )
