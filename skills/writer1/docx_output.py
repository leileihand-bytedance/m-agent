from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from uuid import uuid4
import zipfile

from lxml import etree


DEFAULT_TEMPLATE_PATH = Path(__file__).parent / "assets" / "brief-template.docx"

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_NS = {"w": _W_NS}
_TITLE_PLACEHOLDER = "标题"
_BODY_PLACEHOLDER = "正文正文正文。"
_WORD_OUTPUT_MARKERS = (
    "输出word",
    "导出word",
    "输出为word",
    "导出为word",
    "转成word",
    "生成word",
    "word文档",
    "word版",
    "正式文档",
    "正式版文档",
    "输出正式版",
    "导出正式版",
)
_CONTENT_EDIT_MARKERS = (
    "修改",
    "改成",
    "调整",
    "优化",
    "润色",
    "精简",
    "压缩",
    "扩写",
    "补充",
    "增加",
    "加入",
    "删掉",
    "删除",
    "替换",
    "标题改",
    "换个标题",
    "第一段",
    "第二段",
    "第三段",
    "全文",
)


def should_generate_brief_docx(text: str) -> bool:
    normalized = "".join(str(text or "").lower().split())
    return any(marker in normalized for marker in _WORD_OUTPUT_MARKERS)


def is_brief_docx_export_only(text: str) -> bool:
    normalized = "".join(str(text or "").lower().split())
    if not should_generate_brief_docx(normalized):
        return False
    if any(marker in normalized for marker in _CONTENT_EDIT_MARKERS):
        return False

    remainder = normalized
    for marker in _WORD_OUTPUT_MARKERS:
        remainder = remainder.replace(marker, "")
    remainder = re.sub(r"[，。！？、；：,.!?;:'\"（）()【】\[\]]", "", remainder)
    for filler in (
        "请",
        "帮我",
        "麻烦",
        "把",
        "将",
        "给我",
        "按",
        "这篇",
        "这个",
        "当前",
        "上一稿",
        "上一版",
        "简报",
        "稿子",
        "初稿",
        "文档",
        "格式",
        "输出",
        "导出",
        "生成",
    ):
        remainder = remainder.replace(filler, "")
    return not remainder


def generate_brief_docx(
    *,
    title: str,
    body: str,
    output_dir: str | Path,
    template_path: str | Path = DEFAULT_TEMPLATE_PATH,
) -> Path:
    clean_title = str(title or "").strip()
    paragraphs = _body_paragraphs(body)
    if not clean_title or not paragraphs:
        raise ValueError("生成简报 Word 前必须有完整标题和正文")

    source_path = Path(template_path).resolve(strict=True)
    destination_dir = Path(output_dir).resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    output_path = destination_dir / f"简报-{_safe_filename(clean_title)}.docx"
    temporary_path = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")

    try:
        with zipfile.ZipFile(source_path, "r") as source:
            document_xml = source.read("word/document.xml")
            final_xml = _replace_document_slots(document_xml, title=clean_title, body=paragraphs)
            with zipfile.ZipFile(temporary_path, "w") as final:
                final.comment = source.comment
                for info in source.infolist():
                    payload = (
                        final_xml
                        if info.filename == "word/document.xml"
                        else source.read(info.filename)
                    )
                    final.writestr(info, payload)
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return output_path


def _replace_document_slots(document_xml: bytes, *, title: str, body: list[str]) -> bytes:
    root = etree.fromstring(document_xml)
    paragraphs = root.xpath("//w:body/w:p", namespaces=_NS)
    title_slots = [item for item in paragraphs if _paragraph_text(item) == _TITLE_PLACEHOLDER]
    body_slots = [item for item in paragraphs if _paragraph_text(item) == _BODY_PLACEHOLDER]
    if len(title_slots) != 1 or len(body_slots) != 1:
        raise ValueError("简报模板中的标题或正文占位区不唯一")

    _replace_paragraph_text(title_slots[0], title)
    body_slot = body_slots[0]
    parent = body_slot.getparent()
    insert_at = parent.index(body_slot)
    for offset, text in enumerate(body):
        paragraph = deepcopy(body_slot)
        _replace_paragraph_text(paragraph, text)
        parent.insert(insert_at + offset, paragraph)
    parent.remove(body_slot)

    return etree.tostring(
        root,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )


def _paragraph_text(paragraph: etree._Element) -> str:
    return "".join(paragraph.xpath(".//w:t/text()", namespaces=_NS)).strip()


def _replace_paragraph_text(paragraph: etree._Element, value: str) -> None:
    text_nodes = paragraph.xpath(".//w:t", namespaces=_NS)
    if not text_nodes:
        raise ValueError("简报模板占位段落缺少文本节点")
    text_nodes[0].text = value
    if value[:1].isspace() or value[-1:].isspace():
        text_nodes[0].set(_XML_SPACE, "preserve")
    else:
        text_nodes[0].attrib.pop(_XML_SPACE, None)
    for node in text_nodes[1:]:
        node.text = ""


def _body_paragraphs(body: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"\s*\n+\s*", str(body or "").strip())
        if item.strip()
    ]


def _safe_filename(title: str) -> str:
    filename = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "-", title).strip(" .-")
    return (filename or "正式文档")[:40]
