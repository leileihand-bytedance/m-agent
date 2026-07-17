from __future__ import annotations

import re
from datetime import date


_FULL_DATE_PATTERN = re.compile(
    r"(?<!\d)(?P<year>20\d{2})\s*[-/.年]\s*(?P<month>\d{1,2})"
    r"\s*[-/.月]\s*(?P<day>\d{1,2})\s*日?"
)
_MONTH_DAY_PATTERN = re.compile(
    r"(?<!\d)(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日"
)


def parse_flexible_date(value: str, *, default_year: int | None = None) -> date:
    """解析网页和模型常见日期格式；缺少年份时必须由调用方提供。"""
    text = str(value or "").strip()
    match = _FULL_DATE_PATTERN.search(text)
    if match:
        return date(
            int(match["year"]),
            int(match["month"]),
            int(match["day"]),
        )
    match = _MONTH_DAY_PATTERN.search(text)
    if match and default_year is not None:
        return date(default_year, int(match["month"]), int(match["day"]))
    raise ValueError(f"无法解析日期：{text}")
