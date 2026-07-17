from datetime import date

import pytest

from skills.shenyinxie_news.selection import (
    calculate_issue_number,
    calculate_news_period,
    extract_explicit_half_month,
)


@pytest.mark.parametrize(
    "today,expected_start,expected_end",
    [
        # 每月 1 日：上月 16 日至上月最后一日
        (date(2026, 7, 1), date(2026, 6, 16), date(2026, 6, 30)),
        # 每月 2-15 日：当月 1 日至执行日
        (date(2026, 7, 2), date(2026, 7, 1), date(2026, 7, 2)),
        (date(2026, 7, 15), date(2026, 7, 1), date(2026, 7, 15)),
        # 每月 16-28 日：仍生成当月上半月
        (date(2026, 7, 16), date(2026, 7, 1), date(2026, 7, 15)),
        (date(2026, 7, 17), date(2026, 7, 1), date(2026, 7, 15)),
        (date(2026, 7, 28), date(2026, 7, 1), date(2026, 7, 15)),
        # 每月 29 日至月末：当月 16 日至执行日
        (date(2026, 7, 29), date(2026, 7, 16), date(2026, 7, 29)),
        # 跨年
        (date(2026, 1, 1), date(2025, 12, 16), date(2025, 12, 31)),
        # 2 月平年
        (date(2026, 2, 1), date(2026, 1, 16), date(2026, 1, 31)),
        (date(2026, 2, 16), date(2026, 2, 1), date(2026, 2, 15)),
        (date(2026, 2, 28), date(2026, 2, 1), date(2026, 2, 15)),
        # 闰年 2 月
        (date(2024, 3, 1), date(2024, 2, 16), date(2024, 2, 29)),
        # 大月 31 日
        (date(2026, 1, 31), date(2026, 1, 16), date(2026, 1, 31)),
    ],
)
def test_calculate_news_period(today, expected_start, expected_end):
    start, end = calculate_news_period(today)
    assert start == expected_start
    assert end == expected_end


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2026, 1, 1), "2026-01"),
        (date(2026, 1, 16), "2026-02"),
        (date(2026, 7, 1), "2026-13"),
        (date(2026, 7, 15), "2026-13"),
        (date(2026, 7, 16), "2026-14"),
        (date(2026, 7, 29), "2026-14"),
        (date(2026, 12, 31), "2026-24"),
    ],
)
def test_calculate_issue_number(today, expected):
    assert calculate_issue_number(today) == expected


@pytest.mark.parametrize(
    "instruction,today,expected",
    [
        (
            "生成7月上半月的深银协动态",
            date(2026, 7, 17),
            (date(2026, 7, 1), date(2026, 7, 15)),
        ),
        (
            "生成7月下半月的深银协动态",
            date(2026, 7, 29),
            (date(2026, 7, 16), date(2026, 7, 29)),
        ),
        (
            "生成6月下半月的深银协动态",
            date(2026, 7, 17),
            (date(2026, 6, 16), date(2026, 6, 30)),
        ),
        (
            "生成6月的深银协动态\n上半月",
            date(2026, 7, 17),
            (date(2026, 6, 1), date(2026, 6, 15)),
        ),
        ("生成上半月的深银协动态", date(2026, 7, 17), None),
        ("生成深银协动态", date(2026, 7, 17), None),
    ],
)
def test_extract_explicit_half_month(instruction, today, expected):
    assert extract_explicit_half_month(instruction, today) == expected
