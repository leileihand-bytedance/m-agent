"""独立 PPT 低级错误审核。"""

from .extractor import extract_ppt_document
from .models import (
    PptElement,
    PptFinding,
    PptReviewDocument,
    PptReviewInputError,
    PptReviewResult,
    PptSlide,
)

__all__ = [
    "PptElement",
    "PptFinding",
    "PptReviewDocument",
    "PptReviewInputError",
    "PptReviewResult",
    "PptSlide",
    "extract_ppt_document",
]
