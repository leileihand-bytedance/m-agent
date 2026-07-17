"""基于常用公文模板的独立 Word 实际格式审核。"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from .parser import iter_reviewable_paragraphs, open_docx_sanitized, paragraph_text
from .core.models import Finding, ReviewResult


_RULES_PATH = Path(__file__).with_name("official_format_rules.json")
_H1_RE = re.compile(r"^[一二三四五六七八九十百零〇]+、")
_H2_RE = re.compile(r"^（[一二三四五六七八九十百零〇]+）")
_H3_RE = re.compile(r"^\d+[.．、](?!\d)")
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_POINT_TOLERANCE = 1.0
_CM_TOLERANCE = 0.08


def load_official_format_rules(path: Path = _RULES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _style_chain(style) -> Iterable[Any]:
    seen: set[str] = set()
    current = style
    while current is not None and current.style_id not in seen:
        seen.add(current.style_id)
        yield current
        current = current.base_style


def _rpr_sources(run: Run, paragraph: Paragraph) -> Iterable[Any]:
    if run._r.rPr is not None:
        yield run._r.rPr
    # pPr/rPr 只描述段落标记，不代表现有 run 的实际显示格式。
    for style in _style_chain(run.style):
        if style.element.rPr is not None:
            yield style.element.rPr
    for style in _style_chain(paragraph.style):
        if style.element.rPr is not None:
            yield style.element.rPr


def _theme_fonts(path: Path) -> dict[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("word/theme/theme1.xml"))
    except (KeyError, OSError, ET.ParseError, zipfile.BadZipFile):
        return {}

    namespace = {"a": _A_NS}
    result: dict[str, str] = {}
    for prefix, element_name in (("major", "majorFont"), ("minor", "minorFont")):
        element = root.find(f".//a:{element_name}", namespace)
        if element is None:
            continue
        latin = element.find("a:latin", namespace)
        if latin is not None and latin.get("typeface"):
            result[f"{prefix}Ascii"] = latin.get("typeface", "")
            result[f"{prefix}HAnsi"] = latin.get("typeface", "")
        east_asia = element.find("a:ea", namespace)
        if east_asia is not None and east_asia.get("typeface"):
            result[f"{prefix}EastAsia"] = east_asia.get("typeface", "")
        for font in element.findall("a:font", namespace):
            if font.get("script") == "Hans" and font.get("typeface"):
                result[f"{prefix}EastAsia"] = font.get("typeface", "")
    return result


def _effective_font_name(
    run: Run,
    paragraph: Paragraph,
    theme_fonts: dict[str, str],
) -> str | None:
    for rpr in _rpr_sources(run, paragraph):
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            continue
        for key in ("w:eastAsia", "w:ascii", "w:hAnsi"):
            value = rfonts.get(qn(key))
            if value:
                return value
        for key in ("w:eastAsiaTheme", "w:asciiTheme", "w:hAnsiTheme"):
            theme_key = rfonts.get(qn(key))
            if theme_key and theme_fonts.get(theme_key):
                return theme_fonts[theme_key]
    return None


def _effective_size_pt(run: Run, paragraph: Paragraph) -> float | None:
    for rpr in _rpr_sources(run, paragraph):
        size = rpr.find(qn("w:sz"))
        if size is not None and size.get(qn("w:val")):
            return float(size.get(qn("w:val"))) / 2
    return None


def _effective_bold(run: Run, paragraph: Paragraph) -> bool:
    for rpr in _rpr_sources(run, paragraph):
        bold = rpr.find(qn("w:b"))
        if bold is None:
            continue
        value = bold.get(qn("w:val"))
        return value not in {"0", "false", "off", "no"}
    return False


def _effective_paragraph_value(paragraph: Paragraph, attribute: str):
    value = getattr(paragraph.paragraph_format, attribute)
    if value is not None:
        return value
    for style in _style_chain(paragraph.style):
        value = getattr(style.paragraph_format, attribute)
        if value is not None:
            return value
    return None


def _role_for_text(text: str, *, is_title: bool) -> str:
    if is_title:
        return "title"
    if _H1_RE.match(text):
        return "heading1"
    if _H2_RE.match(text):
        return "heading2"
    if _H3_RE.match(text):
        return "heading3"
    return "body"


def _alignment_name(value) -> str | None:
    if value == WD_ALIGN_PARAGRAPH.CENTER:
        return "center"
    if value in {WD_ALIGN_PARAGRAPH.JUSTIFY, WD_ALIGN_PARAGRAPH.DISTRIBUTE}:
        return "justify"
    if value == WD_ALIGN_PARAGRAPH.LEFT:
        return "left"
    if value == WD_ALIGN_PARAGRAPH.RIGHT:
        return "right"
    return None


def _line_spacing_pt(paragraph: Paragraph) -> float | None:
    value = _effective_paragraph_value(paragraph, "line_spacing")
    rule = _effective_paragraph_value(paragraph, "line_spacing_rule")
    if rule != WD_LINE_SPACING.EXACTLY or not hasattr(value, "pt"):
        return None
    return float(value.pt)


def _length_pt(value) -> float | None:
    return None if value is None else float(value.pt)


def _close(actual: float | None, expected: float, tolerance: float = _POINT_TOLERANCE) -> bool:
    return actual is not None and abs(actual - expected) <= tolerance


def _finding(
    *,
    rule_id: str,
    paragraph_index: int,
    text: str,
    description: str,
    target_text: str | None = None,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        paragraph_index=paragraph_index,
        line_number=paragraph_index + 1,
        original_text=text,
        description=description,
        target_text=(target_text or text)[:180],
    )


def _check_page_rules(
    path: Path,
    rules: dict[str, Any],
    *,
    anchor_index: int,
    anchor_text: str,
) -> list[Finding]:
    document = open_docx_sanitized(path)
    expected = rules["page"]
    findings: list[Finding] = []
    for section_number, section in enumerate(document.sections, start=1):
        actual_orientation = "landscape" if section.orientation == WD_ORIENT.LANDSCAPE else "portrait"
        actual_values = {
            "width_cm": section.page_width.cm,
            "height_cm": section.page_height.cm,
            "top_margin_cm": section.top_margin.cm,
            "bottom_margin_cm": section.bottom_margin.cm,
            "left_margin_cm": section.left_margin.cm,
            "right_margin_cm": section.right_margin.cm,
        }
        problems: list[str] = []
        if actual_orientation != expected["orientation"]:
            problems.append("页面应为A4纵向")
        for key, label in (
            ("width_cm", "纸张宽度"),
            ("height_cm", "纸张高度"),
            ("top_margin_cm", "上边距"),
            ("bottom_margin_cm", "下边距"),
            ("left_margin_cm", "左边距"),
            ("right_margin_cm", "右边距"),
        ):
            if abs(actual_values[key] - expected[key]) > _CM_TOLERANCE:
                problems.append(
                    f"{label}应为{expected[key]:g}cm，当前约{actual_values[key]:.2f}cm"
                )
        if problems:
            findings.append(
                _finding(
                    rule_id="official-format-page",
                    paragraph_index=anchor_index,
                    text=anchor_text,
                    description=f"第{section_number}节页面设置不符合公文模板：" + "；".join(problems),
                )
            )
    return findings


def _check_paragraph_rules(
    path: Path,
    rules: dict[str, Any],
) -> tuple[list[Finding], int, str]:
    document = open_docx_sanitized(path)
    theme_fonts = _theme_fonts(path)
    paragraphs = list(iter_reviewable_paragraphs(document))
    top_level_indexes = [
        index
        for index, paragraph in enumerate(paragraphs)
        if paragraph._p.getparent().tag == qn("w:body")
    ]
    if not top_level_indexes:
        return [], 0, "文档正文"

    title_index = top_level_indexes[0]
    title_text = paragraph_text(paragraphs[title_index]).strip()
    findings: list[Finding] = []

    for index in top_level_indexes:
        paragraph = paragraphs[index]
        text = paragraph_text(paragraph).strip()
        role = _role_for_text(text, is_title=index == title_index)
        expected = rules["roles"][role]
        rule_id = f"official-format-{role}"
        label = expected["label"]
        text_runs = [run for run in paragraph.runs if run.text]

        mismatched_fonts: list[tuple[str, str | None]] = []
        mismatched_sizes: list[tuple[str, float | None]] = []
        mismatched_bold: list[str] = []
        for run in text_runs:
            font_name = _effective_font_name(run, paragraph, theme_fonts)
            size_pt = _effective_size_pt(run, paragraph)
            bold = _effective_bold(run, paragraph)
            if font_name not in expected["fonts"]:
                mismatched_fonts.append((run.text, font_name))
            if not _close(size_pt, float(expected["size_pt"]), tolerance=0.2):
                mismatched_sizes.append((run.text, size_pt))
            if bold != bool(expected["bold"]):
                mismatched_bold.append(run.text)

        if mismatched_fonts:
            target, actual = mismatched_fonts[0]
            findings.append(
                _finding(
                    rule_id=rule_id,
                    paragraph_index=index,
                    text=text,
                    target_text=target,
                    description=f"{label}应使用{expected['fonts'][0]}，当前部分文字为{actual or '无法识别的字体'}",
                )
            )
        if mismatched_sizes:
            target, actual = mismatched_sizes[0]
            actual_label = "无法识别" if actual is None else f"{actual:g}pt"
            findings.append(
                _finding(
                    rule_id=rule_id,
                    paragraph_index=index,
                    text=text,
                    target_text=target,
                    description=f"{label}字号应为{expected['size_pt']:g}pt，当前部分文字为{actual_label}",
                )
            )
        if mismatched_bold:
            findings.append(
                _finding(
                    rule_id=rule_id,
                    paragraph_index=index,
                    text=text,
                    target_text=mismatched_bold[0],
                    description=f"{label}{'应加粗' if expected['bold'] else '不应加粗'}",
                )
            )

        alignment = _alignment_name(_effective_paragraph_value(paragraph, "alignment"))
        if alignment != expected["alignment"]:
            expected_label = "居中" if expected["alignment"] == "center" else "两端对齐"
            findings.append(
                _finding(
                    rule_id=rule_id,
                    paragraph_index=index,
                    text=text,
                    description=f"{label}应{expected_label}，当前对齐方式不符合模板",
                )
            )

        indent_pt = _length_pt(_effective_paragraph_value(paragraph, "first_line_indent"))
        if not _close(indent_pt, float(expected["first_line_indent_pt"])):
            findings.append(
                _finding(
                    rule_id=rule_id,
                    paragraph_index=index,
                    text=text,
                    description=(
                        f"{label}首行缩进应为{expected['first_line_indent_pt']:g}pt"
                        f"（约{'0字符' if expected['first_line_indent_pt'] == 0 else '2字符'}）"
                    ),
                )
            )

        line_spacing_pt = _line_spacing_pt(paragraph)
        if not _close(line_spacing_pt, float(expected["line_spacing_pt"])):
            actual_label = "非固定值" if line_spacing_pt is None else f"{line_spacing_pt:g}pt"
            findings.append(
                _finding(
                    rule_id=rule_id,
                    paragraph_index=index,
                    text=text,
                    description=f"{label}行距应为固定值{expected['line_spacing_pt']:g}pt，当前为{actual_label}",
                )
            )

        for spacing_attr, spacing_label in (("space_before", "段前"), ("space_after", "段后")):
            spacing_pt = _length_pt(_effective_paragraph_value(paragraph, spacing_attr)) or 0.0
            if abs(spacing_pt) > _POINT_TOLERANCE:
                findings.append(
                    _finding(
                        rule_id=rule_id,
                        paragraph_index=index,
                        text=text,
                        description=f"{label}{spacing_label}间距应为0pt，当前约{spacing_pt:g}pt",
                    )
                )

    return findings, title_index, title_text


def review_official_format(path: Path, filename: str) -> ReviewResult:
    """只检查公文实际格式，不调用内容审核或大模型。"""
    rules = load_official_format_rules()
    paragraph_findings, title_index, title_text = _check_paragraph_rules(path, rules)
    page_findings = _check_page_rules(
        path,
        rules,
        anchor_index=title_index,
        anchor_text=title_text,
    )
    findings = page_findings + paragraph_findings
    findings.sort(key=lambda finding: (finding.paragraph_index, finding.rule_id, finding.description))
    total_rules = 6
    failed_rules = len({finding.rule_id for finding in findings})
    return ReviewResult(
        findings=findings,
        total_rules=total_rules,
        passed_rules=max(0, total_rules - failed_rules),
        filename=filename,
    )
