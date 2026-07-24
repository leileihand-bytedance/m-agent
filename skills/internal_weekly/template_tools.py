from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from lxml import etree


APPROVED_SOURCE_SHA256 = (
    "0aefd25748e14d15e5e1643fc2ad2aa0a81ea9487ff343b28f70564e678132a1"
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"
CUSTOM_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
)
NS = {"w": W_NS}
W = f"{{{W_NS}}}"


def sanitize_internal_weekly_template(
    source_path: str | Path,
    destination_path: str | Path,
) -> Path:
    """从用户批准母版生成不含案例正文和作者元数据的 Skill 资产。"""
    source = Path(source_path).resolve(strict=True)
    if hashlib.sha256(source.read_bytes()).hexdigest() != APPROVED_SOURCE_SHA256:
        raise ValueError("内参周报源母版与已批准版本不一致")

    destination = Path(destination_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")

    with ZipFile(source) as package:
        entries = [(info, package.read(info.filename)) for info in package.infolist()]

    replacements: dict[str, bytes] = {}
    document_bytes = _part(entries, "word/document.xml")
    settings_bytes = _part(entries, "word/settings.xml")
    replacements["word/document.xml"] = _sanitize_document(document_bytes)
    replacements["word/settings.xml"] = _remove_automatic_field_update(settings_bytes)
    for name in ("docProps/core.xml", "docProps/core0.xml"):
        if _has_part(entries, name):
            replacements[name] = _scrub_core_properties(_part(entries, name))
    if _has_part(entries, "docProps/custom.xml"):
        replacements["docProps/custom.xml"] = _clear_custom_properties(
            _part(entries, "docProps/custom.xml")
        )

    try:
        with ZipFile(temporary, "w") as output:
            for info, payload in entries:
                output.writestr(info, replacements.get(info.filename, payload))
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _sanitize_document(document_xml: bytes) -> bytes:
    root = etree.fromstring(document_xml)
    body = root.find(f"{W}body")
    if body is None:
        raise ValueError("内参周报母版缺少正文")

    toc_nodes = [
        child
        for child in body
        if child.tag == f"{W}sdt"
        and child.xpath(".//w:instrText[contains(., 'TOC')]", namespaces=NS)
    ]
    if len(toc_nodes) != 1:
        raise ValueError("内参周报母版自动目录数量异常")
    _reset_toc_field(toc_nodes[0])

    children = list(body)
    toc_index = children.index(toc_nodes[0])
    section_prototype = _first_styled_paragraph(
        children,
        style_id="1",
        start=toc_index + 1,
    )
    section_index = children.index(section_prototype)
    item_prototype = _first_styled_paragraph(
        children,
        style_id="3",
        start=section_index + 1,
    )
    item_index = children.index(item_prototype)
    body_prototype = next(
        (
            child
            for child in children[item_index + 1 :]
            if child.tag == f"{W}p"
            and not _paragraph_style(child)
            and _paragraph_text(child)
        ),
        None,
    )
    if body_prototype is None:
        raise ValueError("内参周报母版缺少正文样式原型")

    clean_section = _empty_paragraph_prototype(section_prototype)
    clean_item = _empty_paragraph_prototype(item_prototype)
    clean_body = _empty_paragraph_prototype(body_prototype)
    sect_pr = body.find(f"{W}sectPr")
    if sect_pr is None:
        raise ValueError("内参周报母版缺少节属性")

    for child in list(body)[section_index:]:
        if child is not sect_pr:
            body.remove(child)
    insert_at = body.index(sect_pr)
    body.insert(insert_at, _page_break_paragraph())
    body.insert(insert_at + 1, clean_section)
    body.insert(insert_at + 2, clean_item)
    body.insert(insert_at + 3, clean_body)
    return _serialize(root)


def _reset_toc_field(sdt: etree._Element) -> None:
    content = sdt.find(f"{W}sdtContent")
    if content is None or len(content) < 2:
        raise ValueError("内参周报母版自动目录结构异常")
    title = deepcopy(content[0])
    field_style = deepcopy(content[1].find(f"{W}pPr"))
    for child in list(content):
        content.remove(child)
    content.append(title)

    paragraph = etree.Element(f"{W}p")
    if field_style is not None:
        paragraph.append(field_style)
    paragraph.extend(
        (
            _field_char_run("begin"),
            _instruction_run(' TOC \\o "1-3" \\h \\z \\u '),
            _field_char_run("separate"),
            _field_char_run("end"),
        )
    )
    content.append(paragraph)


def _field_char_run(kind: str) -> etree._Element:
    run = etree.Element(f"{W}r")
    field = etree.SubElement(run, f"{W}fldChar")
    field.set(f"{W}fldCharType", kind)
    return run


def _instruction_run(instruction: str) -> etree._Element:
    run = etree.Element(f"{W}r")
    node = etree.SubElement(run, f"{W}instrText")
    node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    node.text = instruction
    return run


def _page_break_paragraph() -> etree._Element:
    paragraph = etree.Element(f"{W}p")
    run = etree.SubElement(paragraph, f"{W}r")
    page_break = etree.SubElement(run, f"{W}br")
    page_break.set(f"{W}type", "page")
    return paragraph


def _empty_paragraph_prototype(paragraph: etree._Element) -> etree._Element:
    clone = deepcopy(paragraph)
    for node in clone.xpath(".//w:bookmarkStart|.//w:bookmarkEnd", namespaces=NS):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    for text in clone.xpath(".//w:t", namespaces=NS):
        text.text = ""
    for node in clone.xpath(".//w:fldChar|.//w:instrText", namespaces=NS):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    return clone


def _first_styled_paragraph(
    children: list[etree._Element],
    *,
    style_id: str,
    start: int,
) -> etree._Element:
    paragraph = next(
        (
            child
            for child in children[start:]
            if child.tag == f"{W}p" and _paragraph_style(child) == style_id
        ),
        None,
    )
    if paragraph is None:
        raise ValueError(f"内参周报母版缺少样式 {style_id} 原型")
    return paragraph


def _paragraph_style(paragraph: etree._Element) -> str:
    values = paragraph.xpath("./w:pPr/w:pStyle/@w:val", namespaces=NS)
    return str(values[0]) if values else ""


def _paragraph_text(paragraph: etree._Element) -> str:
    return "".join(paragraph.xpath(".//w:t/text()", namespaces=NS)).strip()


def _remove_automatic_field_update(settings_xml: bytes) -> bytes:
    root = etree.fromstring(settings_xml)
    for node in root.findall(f"{W}updateFields"):
        root.remove(node)
    return _serialize(root)


def _scrub_core_properties(core_xml: bytes) -> bytes:
    root = etree.fromstring(core_xml)
    for tag in (f"{{{DC_NS}}}creator", f"{{{CP_NS}}}lastModifiedBy"):
        node = root.find(tag)
        if node is not None:
            node.text = "M-Agent"
    printed = root.find(f"{{{CP_NS}}}lastPrinted")
    if printed is not None:
        root.remove(printed)
    return _serialize(root)


def _clear_custom_properties(custom_xml: bytes) -> bytes:
    root = etree.fromstring(custom_xml)
    if root.tag != f"{{{CUSTOM_NS}}}Properties":
        raise ValueError("内参周报母版自定义属性结构异常")
    for child in list(root):
        root.remove(child)
    return _serialize(root)


def _serialize(root: etree._Element) -> bytes:
    return etree.tostring(
        root,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )


def _has_part(entries: list[tuple[object, bytes]], name: str) -> bool:
    return any(getattr(info, "filename", "") == name for info, _payload in entries)


def _part(entries: list[tuple[object, bytes]], name: str) -> bytes:
    try:
        return next(
            payload
            for info, payload in entries
            if getattr(info, "filename", "") == name
        )
    except StopIteration as exc:
        raise ValueError(f"内参周报母版缺少 {name}") from exc
