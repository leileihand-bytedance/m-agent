from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
import zipfile

from lxml import etree


DEFAULT_TEMPLATE_PATH = Path(__file__).parent / "assets" / "direct-report-template.docx"

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_NS = {"w": _W_NS}

_CONTENT_EDIT_MARKERS = (
    "修改",
    "改成",
    "调整",
    "优化",
    "润色",
    "精简",
    "压缩",
    "扩写",
    "补充正文",
    "删掉",
    "删除",
    "突出",
    "强化",
    "弱化",
    "标题改",
    "换个标题",
    "第一段",
    "第二段",
    "第三段",
    "开头",
    "结尾",
)


@dataclass(frozen=True)
class DirectReportDocumentRequest:
    issue_year: str = ""
    issue_number: str = ""
    total_issue_number: str = ""
    report_date: date | None = None


def parse_direct_report_document_request(text: str) -> DirectReportDocumentRequest:
    normalized = _normalize_request(text)
    issue_year = ""
    issue_number = ""
    material_reference = any(
        marker in normalized
        for marker in ("根据", "参考", "基于", "材料", "素材", "来源", "链接")
    )

    annual_issue = re.search(
        r"(?:期号|本期)(?:为|填(?:写)?|用|[:：])?"
        r"(20\d{2})年第(\d{1,4})期",
        normalized,
    )
    if annual_issue is None and _has_explicit_document_export_request(normalized):
        annual_issue = re.search(
            r"(?:word|docx)(?:文档|文件)?[，,:：]?"
            r"(20\d{2})年第(\d{1,4})期",
            normalized,
            re.I,
        )
    if annual_issue is None and not material_reference:
        annual_issue = re.search(
            r"(20\d{2})年第(\d{1,4})期(?:[（(，,])总第",
            normalized,
        )
    if annual_issue:
        issue_year, issue_number = annual_issue.groups()
    else:
        labeled_issue = re.search(
            r"(?<!总)(?:期号|本期)(?:为|填(?:写)?|用|[:：])?"
            r"第?(\d{1,4})(?:期)?(?!\d|年)",
            normalized,
        )
        if labeled_issue:
            issue_number = labeled_issue.group(1)
        else:
            paired_issue = None
            if not material_reference:
                paired_issue = re.search(
                    r"(?<!总)第?(\d{1,4})期(?:[（(，,])总第",
                    normalized,
                )
            if paired_issue:
                issue_number = paired_issue.group(1)

    total_issue = re.search(
        r"总期号(?:为|填(?:写)?|用|[:：])?第?(\d{1,6})期?",
        normalized,
    )
    if total_issue is None and (issue_number or not material_reference):
        total_issue = re.search(r"总第(\d{1,6})期?", normalized)
    report_date = _parse_labeled_date(normalized)
    return DirectReportDocumentRequest(
        issue_year=issue_year,
        issue_number=issue_number,
        total_issue_number=total_issue.group(1) if total_issue else "",
        report_date=report_date,
    )


def should_generate_direct_report_docx(text: str) -> bool:
    normalized = _normalize_request(text)
    if _has_explicit_document_export_request(normalized):
        return True
    metadata = parse_direct_report_document_request(normalized)
    return any(
        (
            metadata.issue_year,
            metadata.issue_number,
            metadata.total_issue_number,
            metadata.report_date is not None,
        )
    )


def is_direct_report_export_only_request(text: str) -> bool:
    if not should_generate_direct_report_docx(text):
        return False
    normalized = _normalize_request(text)
    return not any(marker in normalized for marker in _CONTENT_EDIT_MARKERS)


def generate_direct_report_docx(
    *,
    title: str,
    body: str,
    request_text: str,
    output_dir: str | Path,
) -> Path:
    clean_title = title.strip()
    body_paragraphs = _split_body(body)
    if not clean_title or not body_paragraphs:
        raise ValueError("生成直报 Word 前必须先有标题和正文")
    if not DEFAULT_TEMPLATE_PATH.is_file():
        raise FileNotFoundError("直报 Word 母版不存在")

    raw_output_dir = str(output_dir).strip()
    if not raw_output_dir:
        raise ValueError("直报 Word 输出目录不存在")
    target_dir = Path(raw_output_dir).resolve(strict=True)
    if not target_dir.is_dir():
        raise ValueError("直报 Word 输出目录不存在")
    target = target_dir / f"微众银行信息直报件-{_safe_filename_stem(clean_title)}.docx"
    temporary = target.with_suffix(".tmp.docx")

    with zipfile.ZipFile(DEFAULT_TEMPLATE_PATH, "r") as source:
        entries = [(info, source.read(info.filename)) for info in source.infolist()]
    document_index = next(
        (index for index, (info, _data) in enumerate(entries) if info.filename == "word/document.xml"),
        None,
    )
    if document_index is None:
        raise ValueError("直报 Word 母版缺少 document.xml")

    document_info, document_bytes = entries[document_index]
    root = etree.fromstring(document_bytes)
    _replace_title(root, clean_title)
    _replace_body(root, body_paragraphs)
    _replace_metadata(root, parse_direct_report_document_request(request_text))
    entries[document_index] = (
        document_info,
        etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True),
    )

    try:
        with zipfile.ZipFile(temporary, "w") as output:
            for info, data in entries:
                output.writestr(info, data)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _normalize_request(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip())


def _has_explicit_document_export_request(text: str) -> bool:
    action_first = (
        r"(?:导出|输出|生成|转成|转为|做成)(?:为|成)?(?:一份|一个)?(?:正式)?"
        r"(?:word|docx|文档)(?:文档|文件)?"
    )
    format_first = (
        r"(?:以|按)?(?:word|docx)(?:文档|文件|格式)?(?:形式|格式)?"
        r"(?:导出|输出|生成)"
    )
    return bool(re.search(action_first, text, re.I) or re.search(format_first, text, re.I))


def _parse_labeled_date(text: str) -> date | None:
    if re.search(r"(?:日期|落款日期|报送日期).{0,6}(?:今天|今日)", text):
        return date.today()
    match = re.search(
        r"(?:日期|落款日期|报送日期)\s*(?:为|填(?:写)?|用|[:：])?\s*"
        r"(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?",
        text,
    )
    if not match:
        return None
    try:
        return date(*(int(value) for value in match.groups()))
    except ValueError as exc:
        raise ValueError("用户提供的直报日期无效") from exc


def _split_body(body: str) -> list[str]:
    return [item.strip() for item in re.split(r"\n\s*\n|\n", body.strip()) if item.strip()]


def _safe_filename_stem(title: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", title).strip().rstrip(".")
    return cleaned[:42] or "直报"


def _paragraph_text(paragraph: etree._Element) -> str:
    return "".join(paragraph.xpath(".//w:t/text()", namespaces=_NS)).strip()


def _find_unique_paragraph(root: etree._Element, text: str) -> etree._Element:
    matches = [
        paragraph
        for paragraph in root.xpath("//w:body//w:p", namespaces=_NS)
        if _paragraph_text(paragraph) == text
    ]
    if len(matches) != 1:
        raise ValueError(f"直报 Word 母版中的“{text}”槽位数量异常")
    return matches[0]


def _replace_title(root: etree._Element, title: str) -> None:
    _set_paragraph_text(_find_unique_paragraph(root, "标题"), title)


def _replace_body(root: etree._Element, body_paragraphs: list[str]) -> None:
    prototype = _find_unique_paragraph(root, "正文正文正文。")
    parent = prototype.getparent()
    if parent is None:
        raise ValueError("直报 Word 母版正文槽位结构异常")
    contact = next(
        (
            paragraph
            for paragraph in parent.xpath("./w:p", namespaces=_NS)
            if _paragraph_text(paragraph).startswith("联系人：")
        ),
        None,
    )
    if contact is None:
        raise ValueError("直报 Word 母版缺少联系人定位段")

    children = list(parent)
    start_index = children.index(prototype)
    contact_index = children.index(contact)
    if contact_index <= start_index:
        raise ValueError("直报 Word 母版正文槽位顺序异常")
    for child in children[start_index:contact_index]:
        parent.remove(child)

    insert_index = parent.index(contact)
    for offset, text in enumerate(body_paragraphs):
        paragraph = deepcopy(prototype)
        _clear_duplicate_paragraph_ids(paragraph)
        _set_paragraph_text(paragraph, text)
        parent.insert(insert_index + offset, paragraph)


def _replace_metadata(
    root: etree._Element,
    request: DirectReportDocumentRequest,
) -> None:
    paragraphs = list(root.xpath("//w:body//w:p", namespaces=_NS))
    issue_paragraph = next(
        (
            paragraph
            for paragraph in paragraphs
            if "年第" in _paragraph_text(paragraph) and "总第" in _paragraph_text(paragraph)
        ),
        None,
    )
    if issue_paragraph is None:
        raise ValueError("直报 Word 母版缺少期号段")
    issue_text = _paragraph_text(issue_paragraph)
    current = re.fullmatch(r"(.+?)年第(.+?)期（总第(.+?)期）", issue_text)
    if current is None:
        raise ValueError("直报 Word 母版期号格式异常")
    year, issue_number, total_issue_number = current.groups()
    if request.issue_year:
        year = request.issue_year
    if request.issue_number:
        issue_number = request.issue_number
    if request.total_issue_number:
        total_issue_number = request.total_issue_number
    _set_paragraph_text(
        issue_paragraph,
        f"{year}年第{issue_number}期（总第{total_issue_number}期）",
    )

    if request.report_date is None:
        return
    date_text = (
        f"{request.report_date.year}年{request.report_date.month}月"
        f"{request.report_date.day}日"
    )
    date_paragraphs = [
        paragraph
        for paragraph in paragraphs
        if re.fullmatch(r"20\d{2}年.+?月.+?日", _paragraph_text(paragraph))
    ]
    if len(date_paragraphs) != 2:
        raise ValueError("直报 Word 母版日期槽位数量异常")
    for paragraph in date_paragraphs:
        _set_paragraph_text(paragraph, date_text)


def _set_paragraph_text(paragraph: etree._Element, text: str) -> None:
    paragraph_properties = paragraph.find(f"{{{_W_NS}}}pPr")
    first_run_properties = paragraph.find(f".//{{{_W_NS}}}rPr")
    for child in list(paragraph):
        if child is not paragraph_properties:
            paragraph.remove(child)
    run = etree.Element(f"{{{_W_NS}}}r")
    if first_run_properties is not None:
        run.append(deepcopy(first_run_properties))
    text_node = etree.SubElement(run, f"{{{_W_NS}}}t")
    if text[:1].isspace() or text[-1:].isspace():
        text_node.set(f"{{{_XML_NS}}}space", "preserve")
    text_node.text = text
    paragraph.append(run)


def _clear_duplicate_paragraph_ids(paragraph: etree._Element) -> None:
    paragraph.attrib.pop(f"{{{_W14_NS}}}paraId", None)
    paragraph.attrib.pop(f"{{{_W14_NS}}}textId", None)
