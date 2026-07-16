from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


PptElementKind = Literal["text", "table", "chart"]
PptFindingCategory = Literal[
    "typo",
    "grammar",
    "punctuation",
    "name",
    "placeholder",
    "sequence",
    "data_inconsistency",
    "content_inconsistency",
]


class PptReviewInputError(ValueError):
    """PPT 文件无法进入低级错误审核。"""


@dataclass(frozen=True)
class PptElement:
    element_id: str
    slide_number: int
    kind: PptElementKind
    text: str
    bbox: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class PptSlide:
    slide_number: int
    elements: tuple[PptElement, ...]


@dataclass(frozen=True)
class PptReviewDocument:
    filename: str
    page_count: int
    slides: tuple[PptSlide, ...]
    excluded_image_count: int = 0
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PptFinding:
    rule_id: str
    category: PptFindingCategory
    slide_number: int
    element_id: str
    target_text: str
    description: str
    related_slide_number: int | None = None
    related_element_id: str = ""
    related_text: str = ""


@dataclass(frozen=True)
class PptReviewResult:
    filename: str
    page_count: int
    findings: tuple[PptFinding, ...]
    excluded_image_count: int = 0
    warnings: tuple[str, ...] = ()
    consistency_complete: bool = True
