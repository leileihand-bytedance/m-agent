"""安全提取 HTML 文件中的静态可见文字。"""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import re


_NON_CONTENT_TAGS = frozenset(
    {
        "head",
        "script",
        "style",
        "template",
        "noscript",
        "svg",
        "canvas",
    }
)
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "dd",
        "details",
        "dialog",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "summary",
        "ul",
    }
)
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_CELL_TAGS = frozenset({"td", "th"})
_CONTENT_CHARSET_RE = re.compile(
    r"charset\s*=\s*[\"']?\s*([A-Za-z0-9._:-]+)",
    re.IGNORECASE,
)
_HIDDEN_STYLE_RE = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse))"
    r"(?:\s*!important)?\s*(?:;|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedHtmlResult:
    paragraphs: list[str]
    paragraph_pages: list[int | None]
    encoding: str


@dataclass(frozen=True)
class _OpenElement:
    tag: str
    ignored: bool
    opened_page: bool = False


@dataclass
class _ClosedDetailsState:
    details_stack_index: int
    summary_seen: bool = False
    summary_stack_index: int | None = None


def _normalize_text(parts: list[str]) -> str:
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _is_explicitly_hidden(attrs: list[tuple[str, str | None]]) -> bool:
    normalized = {name.lower(): value for name, value in attrs}
    if "hidden" in normalized:
        return True
    aria_hidden = (normalized.get("aria-hidden") or "").strip().lower()
    if aria_hidden == "true":
        return True
    style = normalized.get("style") or ""
    return _HIDDEN_STYLE_RE.search(style) is not None


def _has_attribute(attrs: list[tuple[str, str | None]], name: str) -> bool:
    return any(attr_name.lower() == name for attr_name, _value in attrs)


def _is_slide_container(attrs: list[tuple[str, str | None]]) -> bool:
    for name, value in attrs:
        if name.lower() == "class":
            return "slide" in (value or "").split()
    return False


class _MetaCharsetParser(HTMLParser):
    """只从真实 meta 元素读取编码声明，忽略注释、脚本和正文。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.declared_encoding: str | None = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if self.declared_encoding is not None or tag.lower() != "meta":
            return
        normalized = {
            name.lower(): (value or "").strip()
            for name, value in attrs
        }
        charset = normalized.get("charset", "")
        if charset:
            self.declared_encoding = charset.lower()
            return
        if normalized.get("http-equiv", "").lower() != "content-type":
            return
        match = _CONTENT_CHARSET_RE.search(normalized.get("content", ""))
        if match is not None:
            self.declared_encoding = match.group(1).lower()


def _extract_declared_encoding(content: bytes) -> str | None:
    parser = _MetaCharsetParser()
    parser.feed(content[:8192].decode("latin-1"))
    parser.close()
    return parser.declared_encoding


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[_OpenElement] = []
        self._closed_details: list[_ClosedDetailsState] = []
        self._ignored_depth = 0
        self._text_parts: list[str] = []
        self._cell_parts: list[str] | None = None
        self._row_cells: list[str] | None = None
        self._row_page: int | None = None
        self._paragraphs: list[str] = []
        self._paragraph_pages: list[int | None] = []
        self._page_stack: list[int] = []
        self._next_page_number = 1
        self._finished = False

    @property
    def paragraphs(self) -> list[str]:
        self._finish()
        return list(self._paragraphs)

    @property
    def paragraph_pages(self) -> list[int | None]:
        self._finish()
        return list(self._paragraph_pages)

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.lower()
        opening_summary = self._summary_state_for_start(tag)
        hidden_by_closed_details = any(
            state.summary_stack_index is None and state is not opening_summary
            for state in self._closed_details
        )
        closed_details = tag == "details" and not _has_attribute(attrs, "open")
        ignored = (
            self._ignored_depth > 0
            or tag in _NON_CONTENT_TAGS
            or _is_explicitly_hidden(attrs)
            or hidden_by_closed_details
            or (tag == "dialog" and not _has_attribute(attrs, "open"))
        )

        if not ignored:
            if tag == "br":
                self._flush_text()
            elif tag == "tr":
                self._flush_text()
                self._flush_row()
                self._row_cells = []
                self._row_page = self._current_page()
            elif tag in _CELL_TAGS:
                self._flush_text()
                self._flush_cell()
                self._cell_parts = []
            elif tag in _BLOCK_TAGS:
                self._flush_text()

        if tag not in _VOID_TAGS:
            opened_page = (
                not ignored
                and _is_slide_container(attrs)
            )
            self._stack.append(
                _OpenElement(
                    tag=tag,
                    ignored=ignored,
                    opened_page=opened_page,
                )
            )
            if opened_page:
                self._page_stack.append(self._next_page_number)
                self._next_page_number += 1
            if ignored:
                if opening_summary is not None:
                    opening_summary.summary_seen = True
                self._ignored_depth += 1
            elif opening_summary is not None:
                opening_summary.summary_seen = True
                opening_summary.summary_stack_index = len(self._stack) - 1
            elif closed_details:
                self._closed_details.append(
                    _ClosedDetailsState(details_stack_index=len(self._stack) - 1)
                )

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._ignored_depth == 0 and not self._closed_details_hide_text():
            if tag in _CELL_TAGS:
                self._flush_cell()
            elif tag == "tr":
                self._flush_cell()
                self._flush_row()
            elif tag in _BLOCK_TAGS:
                self._flush_text()

        matching_index = next(
            (
                index
                for index in range(len(self._stack) - 1, -1, -1)
                if self._stack[index].tag == tag
            ),
            None,
        )
        if matching_index is None:
            return
        removed = self._stack[matching_index:]
        del self._stack[matching_index:]
        self._ignored_depth -= sum(element.ignored for element in removed)
        opened_pages = sum(element.opened_page for element in removed)
        if opened_pages:
            del self._page_stack[-opened_pages:]
        remaining_details: list[_ClosedDetailsState] = []
        for state in self._closed_details:
            if (
                state.summary_stack_index is not None
                and state.summary_stack_index >= matching_index
            ):
                state.summary_stack_index = None
            if state.details_stack_index < matching_index:
                remaining_details.append(state)
        self._closed_details = remaining_details

    def handle_data(self, data: str) -> None:
        if self._ignored_depth or self._closed_details_hide_text() or not data:
            return
        if self._cell_parts is not None:
            self._cell_parts.append(data)
        else:
            self._text_parts.append(data)

    def _summary_state_for_start(
        self,
        tag: str,
    ) -> _ClosedDetailsState | None:
        if tag != "summary" or self._ignored_depth or not self._closed_details:
            return None
        state = self._closed_details[-1]
        if state.summary_seen or state.details_stack_index != len(self._stack) - 1:
            return None
        if any(
            outer.summary_stack_index is None
            for outer in self._closed_details[:-1]
        ):
            return None
        return state

    def _closed_details_hide_text(self) -> bool:
        return any(
            state.summary_stack_index is None
            for state in self._closed_details
        )

    def _current_page(self) -> int | None:
        return self._page_stack[-1] if self._page_stack else None

    def _flush_text(self) -> None:
        text = _normalize_text(self._text_parts)
        self._text_parts = []
        if text:
            self._paragraphs.append(text)
            self._paragraph_pages.append(self._current_page())

    def _flush_cell(self) -> None:
        if self._cell_parts is None:
            return
        text = _normalize_text(self._cell_parts)
        self._cell_parts = None
        if text:
            if self._row_cells is None:
                self._row_cells = []
                self._row_page = self._current_page()
            self._row_cells.append(text)

    def _flush_row(self) -> None:
        if self._row_cells is None:
            return
        row = " | ".join(cell for cell in self._row_cells if cell)
        self._row_cells = None
        if row:
            self._paragraphs.append(row)
            self._paragraph_pages.append(self._row_page)
        self._row_page = None

    def _finish(self) -> None:
        if self._finished:
            return
        self._flush_cell()
        self._flush_row()
        self._flush_text()
        self._finished = True


def _decode_html(content: bytes) -> tuple[str, str]:
    if content.startswith(codecs.BOM_UTF8):
        return content.decode("utf-8-sig"), "utf-8-sig"
    if content.startswith(codecs.BOM_UTF32_LE) or content.startswith(codecs.BOM_UTF32_BE):
        return content.decode("utf-32"), "utf-32"
    if content.startswith(codecs.BOM_UTF16_LE) or content.startswith(codecs.BOM_UTF16_BE):
        return content.decode("utf-16"), "utf-16"

    candidates: list[str] = []
    declared_encoding = _extract_declared_encoding(content)
    if declared_encoding is not None:
        candidates.append(declared_encoding)
    candidates.extend(["utf-8", "gb18030"])

    tried: set[str] = set()
    for encoding in candidates:
        if encoding in tried:
            continue
        tried.add(encoding)
        try:
            codecs.lookup(encoding)
            return content.decode(encoding), encoding
        except (LookupError, UnicodeDecodeError):
            continue
    raise ValueError("HTML 文件编码无法识别")


def parse_html(path: Path | str) -> ParsedHtmlResult:
    content = Path(path).read_bytes()
    text, encoding = _decode_html(content)
    parser = _VisibleTextParser()
    parser.feed(text)
    parser.close()
    paragraphs = parser.paragraphs
    if not paragraphs:
        raise ValueError("HTML 文件中没有可审核的可见文字")
    return ParsedHtmlResult(
        paragraphs=paragraphs,
        paragraph_pages=parser.paragraph_pages,
        encoding=encoding,
    )


__all__ = ["ParsedHtmlResult", "parse_html"]
