from __future__ import annotations

import re
import shutil
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from app.review.official_format_checker import load_official_format_rules
from skills.shenyinxie_news.schema import SelectedArticle


OUTPUT_FILENAME = "深银协动态{issue}.docx"
_TEMPLATE_PLACEHOLDERS = (
    "{{TITLE}}",
    "{{YEAR}}",
    "{{MONTH}}",
    "{{ISSUE}}",
    "{{PERIOD_RANGE}}",
    "{{ARTICLE_1}}",
    "{{ARTICLE_2}}",
    "{{ARTICLE_3}}",
)


def _default_template_path() -> Path:
    return Path(__file__).resolve().parent / "assets" / "shenyinxie-template.docx"


def write_shenyinxie_docx(
    *,
    title: str,
    period_start: date,
    period_end: date,
    issue_number: str,
    articles: list[SelectedArticle],
    output_dir: str | Path,
    template_path: Path | None = None,
) -> Path:
    """生成深银协动态 Word 文档。

    如果提供真实模板且包含约定占位符，会按占位符替换；
    否则用代码按公文格式新建文档。
    """
    target_dir = Path(output_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    issue_part = issue_number.replace("-", "")
    target = target_dir / OUTPUT_FILENAME.format(issue=issue_part)

    template = template_path or _default_template_path()
    if template.exists() and _looks_like_real_template(template):
        _fill_template(template, target, title, period_start, period_end, issue_number, articles)
    else:
        _build_from_scratch(target, title, period_start, period_end, issue_number, articles)

    return target


def _looks_like_real_template(template_path: Path) -> bool:
    """模板文件若包含任意占位符，视为真实模板；否则视为空白占位。"""
    try:
        doc = Document(str(template_path))
        full_text = "\n".join(p.text for p in doc.paragraphs)
        return any(ph in full_text for ph in _TEMPLATE_PLACEHOLDERS)
    except Exception:
        return False


def _fill_template(
    template_path: Path,
    target: Path,
    title: str,
    period_start: date,
    period_end: date,
    issue_number: str,
    articles: list[SelectedArticle],
) -> None:
    """基于模板占位符替换生成文档。"""
    shutil.copy(str(template_path), str(target))
    doc = Document(str(target))

    period_range = _format_period_range(period_start, period_end)

    for para in doc.paragraphs:
        text = para.text
        if not any(ph in text for ph in _TEMPLATE_PLACEHOLDERS):
            continue
        new_text = text
        new_text = new_text.replace("{{TITLE}}", title)
        new_text = new_text.replace("{{YEAR}}", str(period_start.year))
        new_text = new_text.replace("{{MONTH}}", str(period_start.month))
        new_text = new_text.replace("{{ISSUE}}", issue_number)
        new_text = new_text.replace("{{PERIOD_RANGE}}", period_range)
        for idx in range(3):
            placeholder = f"{{{{ARTICLE_{idx + 1}}}}}"
            if placeholder in new_text:
                if idx < len(articles):
                    new_text = new_text.replace(placeholder, _article_block_text(articles[idx]))
                else:
                    new_text = new_text.replace(placeholder, "")
        para.clear()
        para.add_run(new_text)

    doc.save(str(target))


def _build_from_scratch(
    target: Path,
    title: str,
    period_start: date,
    period_end: date,
    issue_number: str,
    articles: list[SelectedArticle],
) -> None:
    """无真实模板时，按公文格式新建文档。"""
    rules = load_official_format_rules()
    doc = Document()
    _apply_page_rules(doc, rules["page"])
    doc.core_properties.title = title.strip() or "深银协动态"

    _add_formatted_paragraph(doc, title, rules["roles"]["title"])
    _add_formatted_paragraph(doc, f"（{_format_period_range(period_start, period_end)}）", rules["roles"]["body"])

    for idx, article in enumerate(articles, start=1):
        heading = f"动态{['一', '二', '三'][idx - 1]}"
        _add_formatted_paragraph(doc, heading, rules["roles"]["heading1"])
        _add_article_block(doc, article, rules)

    doc.save(str(target))


def _format_period_range(period_start: date, period_end: date) -> str:
    if period_start.year == period_end.year and period_start.month == period_end.month:
        return f"{period_start.year}年{period_start.month}月{period_start.day}日—{period_end.day}日"
    if period_start.year == period_end.year:
        return f"{period_start.year}年{period_start.month}月{period_start.day}日—{period_end.month}月{period_end.day}日"
    return f"{period_start.year}年{period_start.month}月{period_start.day}日—{period_end.year}年{period_end.month}月{period_end.day}日"


def _article_block_text(article: SelectedArticle) -> str:
    lines = [
        f"标题：{article.title}",
        f"来源：{article.media_name}　发布时间：{article.publish_date}",
        "",
        article.body,
        "",
    ]
    if article.content_mode == "extract" and article.source_title:
        lines.append(f"原报道标题：{article.source_title}")
    lines.append(f"原文链接：{article.original_url}")
    if article.content_mode == "extract" and article.editor_note:
        lines.append(article.editor_note)
    return "\n".join(lines)


def _add_article_block(doc: Document, article: SelectedArticle, rules: dict[str, object]) -> None:
    body_rules = rules["roles"]["body"]

    # 报道标题
    para = _add_formatted_paragraph(doc, f"标题：{article.title}", body_rules)
    para.runs[0].bold = True

    # 来源与日期
    _add_formatted_paragraph(doc, f"来源：{article.media_name}　发布时间：{article.publish_date}", body_rules)

    # 正文
    for line in article.body.replace("\r\n", "\n").split("\n"):
        clean = line.strip()
        if clean:
            _add_formatted_paragraph(doc, clean, body_rules)

    # 摘编稿保留原报道标题，便于用户核对标题调整。
    if article.content_mode == "extract" and article.source_title:
        _add_formatted_paragraph(doc, f"原报道标题：{article.source_title}", body_rules)

    # 原文链接与摘编说明
    _add_formatted_paragraph(doc, f"原文链接：{article.original_url}", body_rules)
    if article.content_mode == "extract" and article.editor_note:
        _add_formatted_paragraph(doc, article.editor_note, body_rules)


def _apply_page_rules(document: Document, rules: dict[str, object]) -> None:
    for section in document.sections:
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width = Cm(float(rules["width_cm"]))
        section.page_height = Cm(float(rules["height_cm"]))
        section.top_margin = Cm(float(rules["top_margin_cm"]))
        section.bottom_margin = Cm(float(rules["bottom_margin_cm"]))
        section.left_margin = Cm(float(rules["left_margin_cm"]))
        section.right_margin = Cm(float(rules["right_margin_cm"]))


def _add_formatted_paragraph(document: Document, text: str, rules: dict[str, object]) -> object:
    paragraph = document.add_paragraph()
    paragraph.alignment = (
        WD_ALIGN_PARAGRAPH.CENTER
        if rules["alignment"] == "center"
        else WD_ALIGN_PARAGRAPH.JUSTIFY
    )
    paragraph.paragraph_format.first_line_indent = Pt(float(rules["first_line_indent_pt"]))
    paragraph.paragraph_format.line_spacing = Pt(float(rules["line_spacing_pt"]))
    paragraph.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)

    run = paragraph.add_run(_clean_text(text))
    font_name = str(list(rules["fonts"])[0])
    run.font.name = font_name
    run.font.size = Pt(float(rules["size_pt"]))
    run.font.bold = bool(rules["bold"])
    rfonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), font_name)
    rfonts.set(qn("w:ascii"), font_name)
    rfonts.set(qn("w:hAnsi"), font_name)
    return paragraph


def _clean_text(text: str) -> str:
    clean = text.strip()
    clean = re.sub(r"^#{1,6}\s*", "", clean)
    clean = clean.replace("**", "").replace("__", "")
    return clean.strip()


__all__ = ["write_shenyinxie_docx"]
