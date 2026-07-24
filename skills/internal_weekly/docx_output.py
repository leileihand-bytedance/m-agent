from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
import re
from uuid import uuid4
from zipfile import ZipFile

from lxml import etree

from app.platform.documents.word_toc import finalize_word_toc
from skills.internal_weekly.template_tools import (
    _remove_automatic_field_update,
    _reset_toc_field,
)


DEFAULT_TEMPLATE_PATH = Path(__file__).parent / "assets" / "internal-weekly-template.docx"

SECTION_ORDER = ("党政要闻", "监管动态", "同业动向", "市场观察", "前沿观点")
BASE_PUBLICATION_DATE = date(2026, 7, 20)
BASE_ANNUAL_ISSUE = 28
BASE_TOTAL_ISSUE = 409

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}
W = f"{{{W_NS}}}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

_WORD_MARKERS = (
    "输出word",
    "导出word",
    "生成word",
    "word文档",
    "word版",
    "正式文档",
    "正式版",
    "洁净版",
)
_APPROVAL_MARKERS = (
    "核对无误",
    "确认无误",
    "确认通过",
    "审核通过",
    "这版没问题",
    "没有问题",
    "批准",
    "同意生成",
    "可以生成",
    "可生成",
    "定稿",
)
_FORBIDDEN_CLEAN_MARKERS = (
    "核对信息",
    "草稿版本",
    "待核事项",
    "今日资本市场内容待收盘后更新",
)


@dataclass(frozen=True)
class ApprovedWeeklyItem:
    title: str
    paragraphs: tuple[str, ...]


@dataclass(frozen=True)
class ApprovedWeeklySection:
    name: str
    items: tuple[ApprovedWeeklyItem, ...]


@dataclass(frozen=True)
class ApprovedWeeklyDraft:
    publication_date: date
    period_start: date
    period_end: date
    draft_version: str
    sections: tuple[ApprovedWeeklySection, ...]


def requests_clean_word(text: str) -> bool:
    normalized = _normalize_request(text)
    return any(marker in normalized for marker in _WORD_MARKERS)


def is_explicit_word_approval(text: str) -> bool:
    normalized = _normalize_request(text)
    return requests_clean_word(normalized) and any(
        marker in normalized for marker in _APPROVAL_MARKERS
    )


def parse_approved_review(
    review_markdown: str,
    metadata: Mapping[str, object] | None,
) -> ApprovedWeeklyDraft:
    clean_metadata = {
        str(key): str(value or "").strip()
        for key, value in dict(metadata or {}).items()
    }
    if clean_metadata.get("generation_mode") != "full_weekly":
        raise ValueError("只有完整内参周报可以生成洁净版 Word")
    if clean_metadata.get("ready_for_approval", "").lower() not in {"true", "1", "yes"}:
        raise ValueError("当前核对稿尚未达到可批准状态")

    publication_date = _parse_iso_date(
        clean_metadata.get("publication_date", ""),
        label="出版日",
    )
    period_start = _parse_iso_date(
        clean_metadata.get("period_start", ""),
        label="统计期开始日",
    )
    period_end = _parse_iso_date(
        clean_metadata.get("period_end", ""),
        label="统计期结束日",
    )
    draft_version = clean_metadata.get("draft_version", "")
    if not draft_version:
        raise ValueError("核对稿缺少草稿版本")

    review_publication = _metadata_value(review_markdown, "出版日")
    review_period = re.search(
        r"(?m)^-\s*统计期：(\d{4}-\d{2}-\d{2})\s+至\s+(\d{4}-\d{2}-\d{2})\s*$",
        review_markdown,
    )
    review_version = re.search(
        r"(?m)^-\s*草稿版本：`([^`]+)`\s*$",
        review_markdown,
    )
    if (
        review_publication != publication_date.isoformat()
        or review_period is None
        or review_period.group(1) != period_start.isoformat()
        or review_period.group(2) != period_end.isoformat()
    ):
        raise ValueError("核对稿日期与批准元数据不一致")
    if review_version is None or review_version.group(1).strip() != draft_version:
        raise ValueError("核对稿草稿版本不一致")
    if "- 状态：可提交人工核对" not in review_markdown:
        raise ValueError("核对稿尚未达到可批准状态")

    sections = _parse_sections(review_markdown)
    return ApprovedWeeklyDraft(
        publication_date=publication_date,
        period_start=period_start,
        period_end=period_end,
        draft_version=draft_version,
        sections=sections,
    )


def generate_internal_weekly_docx(
    *,
    draft: ApprovedWeeklyDraft,
    request_text: str,
    output_dir: str | Path,
    template_path: str | Path = DEFAULT_TEMPLATE_PATH,
    toc_finalizer: Callable[..., object] | None = None,
) -> Path:
    if not is_explicit_word_approval(request_text):
        raise ValueError("生成洁净版 Word 前必须明确确认核对无误")
    template = Path(template_path).resolve(strict=True)
    target_dir = Path(output_dir).resolve(strict=True)
    if not target_dir.is_dir():
        raise ValueError("内参周报 Word 输出目录不存在")
    target = target_dir / (
        f"微众银行信息内参周报-{draft.publication_date.isoformat()}.docx"
    )
    temporary = target.with_name(f".{target.stem}.{uuid4().hex}.docx")

    with ZipFile(template) as package:
        entries = [(info, package.read(info.filename)) for info in package.infolist()]
    document_xml = _part(entries, "word/document.xml")
    settings_xml = _part(entries, "word/settings.xml")
    replacements = {
        "word/document.xml": _render_document(
            document_xml,
            draft=draft,
            request_text=request_text,
        ),
        "word/settings.xml": _remove_automatic_field_update(settings_xml),
    }

    try:
        with ZipFile(temporary, "w") as output:
            for info, payload in entries:
                output.writestr(info, replacements.get(info.filename, payload))
        finalizer = toc_finalizer or finalize_word_toc
        finalizer(
            temporary,
            allowed_root=target_dir,
            expected_headings=_toc_headings(draft),
        )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _toc_headings(draft: ApprovedWeeklyDraft) -> tuple[str, ...]:
    headings: list[str] = []
    for section in draft.sections:
        headings.append(section.name)
        headings.extend(item.title for item in section.items)
    return tuple(headings)


def _parse_sections(review_markdown: str) -> tuple[ApprovedWeeklySection, ...]:
    section_items: dict[str, list[ApprovedWeeklyItem]] = {
        name: [] for name in SECTION_ORDER
    }
    seen_sections: list[str] = []
    current_section = ""
    current_title = ""
    current_body: list[str] = []
    collecting_body = False

    def finish_item() -> None:
        nonlocal current_title, current_body, collecting_body
        if not current_title:
            return
        paragraphs = tuple(
            _clean_business_text(paragraph)
            for paragraph in _split_paragraphs("\n".join(current_body))
            if _clean_business_text(paragraph)
        )
        if not paragraphs:
            raise ValueError(f"《{current_title}》缺少可写入 Word 的正文")
        section_items[current_section].append(
            ApprovedWeeklyItem(
                title=_clean_business_text(current_title),
                paragraphs=paragraphs,
            )
        )
        current_title = ""
        current_body = []
        collecting_body = False

    for raw_line in review_markdown.splitlines():
        line = raw_line.rstrip()
        section_match = re.fullmatch(r"##\s+(.+?)\s*", line)
        if section_match:
            finish_item()
            candidate = section_match.group(1).strip()
            if candidate == "待核事项":
                current_section = ""
                continue
            if candidate not in SECTION_ORDER or candidate in seen_sections:
                raise ValueError("核对稿板块名称或顺序无效")
            seen_sections.append(candidate)
            current_section = candidate
            continue
        item_match = re.fullmatch(r"###\s+\d+\.\s+(.+?)\s*", line)
        if item_match:
            finish_item()
            if not current_section:
                raise ValueError("核对稿条目缺少所属板块")
            current_title = item_match.group(1).strip()
            collecting_body = True
            continue
        if current_title and line.strip() == "核对信息：":
            collecting_body = False
            continue
        if current_title and collecting_body:
            current_body.append(line)
    finish_item()

    if tuple(seen_sections) != SECTION_ORDER:
        raise ValueError("核对稿必须按固定顺序包含五个板块")
    if any(not section_items[name] for name in SECTION_ORDER):
        raise ValueError("核对稿存在空板块，不能生成洁净版 Word")
    return tuple(
        ApprovedWeeklySection(name=name, items=tuple(section_items[name]))
        for name in SECTION_ORDER
    )


def _render_document(
    document_xml: bytes,
    *,
    draft: ApprovedWeeklyDraft,
    request_text: str,
) -> bytes:
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
    section_prototype = next(
        (
            child
            for child in children
            if child.tag == f"{W}p"
            and _paragraph_style(child) == "1"
            and not _paragraph_text(child)
        ),
        None,
    )
    item_prototype = next(
        (
            child
            for child in children
            if child.tag == f"{W}p"
            and _paragraph_style(child) == "3"
            and not _paragraph_text(child)
        ),
        None,
    )
    if section_prototype is None or item_prototype is None:
        raise ValueError("内参周报母版缺少标题样式原型")
    item_index = children.index(item_prototype)
    body_prototype = next(
        (
            child
            for child in children[item_index + 1 :]
            if child.tag == f"{W}p"
            and not _paragraph_style(child)
            and not _paragraph_text(child)
            and not child.xpath(".//w:br[@w:type='page']", namespaces=NS)
        ),
        None,
    )
    if body_prototype is None:
        raise ValueError("内参周报母版缺少正文样式原型")
    for prototype in (section_prototype, item_prototype, body_prototype):
        body.remove(prototype)

    sect_pr = body.find(f"{W}sectPr")
    if sect_pr is None:
        raise ValueError("内参周报母版缺少节属性")
    insert_at = body.index(sect_pr)
    for section in draft.sections:
        body.insert(insert_at, _paragraph_from_prototype(section_prototype, section.name))
        insert_at += 1
        for item in section.items:
            body.insert(insert_at, _paragraph_from_prototype(item_prototype, item.title))
            insert_at += 1
            for paragraph_text in item.paragraphs:
                body.insert(
                    insert_at,
                    _paragraph_from_prototype(body_prototype, paragraph_text),
                )
                insert_at += 1

    annual_issue, total_issue = _issue_numbers(
        draft.publication_date,
        request_text=request_text,
    )
    issue_paragraphs = [
        paragraph
        for paragraph in body.xpath("./w:p", namespaces=NS)
        if "发稿日期：" in _paragraph_text(paragraph)
    ]
    if len(issue_paragraphs) != 1:
        raise ValueError("内参周报母版期号日期栏位数量异常")
    issue_text = (
        f"{draft.publication_date.year}年第{annual_issue}期（总{total_issue}期）"
        f"       发稿日期：{draft.publication_date.year}年"
        f"{draft.publication_date.month}月{draft.publication_date.day}日"
    )
    _replace_text_nodes(issue_paragraphs[0], issue_text)

    output = etree.tostring(
        root,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )
    output_text = output.decode("utf-8")
    if any(marker in output_text for marker in _FORBIDDEN_CLEAN_MARKERS):
        raise ValueError("内参周报洁净版仍含核对信息")
    return output


def _paragraph_from_prototype(
    prototype: etree._Element,
    text: str,
) -> etree._Element:
    paragraph = deepcopy(prototype)
    ppr = paragraph.find(f"{W}pPr")
    first_run = paragraph.find(f"{W}r")
    run_properties = (
        deepcopy(first_run.find(f"{W}rPr"))
        if first_run is not None and first_run.find(f"{W}rPr") is not None
        else None
    )
    for child in list(paragraph):
        if child is not ppr:
            paragraph.remove(child)
    run = etree.SubElement(paragraph, f"{W}r")
    if run_properties is not None:
        run.append(run_properties)
    text_node = etree.SubElement(run, f"{W}t")
    text_node.text = text
    if text != text.strip():
        text_node.set(XML_SPACE, "preserve")
    return paragraph


def _replace_text_nodes(paragraph: etree._Element, value: str) -> None:
    nodes = paragraph.xpath(".//w:t", namespaces=NS)
    if not nodes:
        raise ValueError("内参周报母版期号日期栏位缺少文字节点")
    nodes[0].text = value
    nodes[0].set(XML_SPACE, "preserve")
    for node in nodes[1:]:
        node.text = ""


def _issue_numbers(publication_date: date, *, request_text: str) -> tuple[int, int]:
    normalized = _normalize_request(request_text)
    annual_match = re.search(
        r"(?<!总)(?:20\d{2}年)?第(\d{1,4})期"
        r"(?:[（(，,]总(?:第)?\d+期?[）)]?)?",
        normalized,
    )
    total_match = re.search(r"总(?:第)?(\d{1,6})期?", normalized)
    if publication_date.weekday() != 0:
        raise ValueError("内参周报出版日必须是周一")
    delta_days = (publication_date - BASE_PUBLICATION_DATE).days
    if delta_days % 7:
        raise ValueError("内参周报出版日与期号基准不对齐")
    if publication_date.year != BASE_PUBLICATION_DATE.year and (
        annual_match is None or total_match is None
    ):
        raise ValueError("跨年度生成 Word 时必须明确提供年度期号和总期号")
    week_delta = delta_days // 7
    annual = int(annual_match.group(1)) if annual_match else BASE_ANNUAL_ISSUE + week_delta
    total = int(total_match.group(1)) if total_match else BASE_TOTAL_ISSUE + week_delta
    if annual <= 0 or total <= 0:
        raise ValueError("内参周报期号无效")
    return annual, total


def _metadata_value(markdown: str, label: str) -> str:
    match = re.search(rf"(?m)^-\s*{re.escape(label)}：(.+?)\s*$", markdown)
    return match.group(1).strip() if match else ""


def _split_paragraphs(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"\n\s*\n|\n", value) if item.strip()]


def _clean_business_text(value: str) -> str:
    clean = value.strip()
    clean = re.sub(r"^\*\*(.+)\*\*$", r"\1", clean)
    clean = clean.replace("\\*", "*")
    return clean


def _parse_iso_date(value: str, *, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"核对稿{label}无效") from exc


def _normalize_request(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip()).lower()


def _paragraph_style(paragraph: etree._Element) -> str:
    values = paragraph.xpath("./w:pPr/w:pStyle/@w:val", namespaces=NS)
    return str(values[0]) if values else ""


def _paragraph_text(paragraph: etree._Element) -> str:
    return "".join(paragraph.xpath(".//w:t/text()", namespaces=NS)).strip()


def _part(entries: list[tuple[object, bytes]], name: str) -> bytes:
    try:
        return next(
            payload
            for info, payload in entries
            if getattr(info, "filename", "") == name
        )
    except StopIteration as exc:
        raise ValueError(f"内参周报母版缺少 {name}") from exc
