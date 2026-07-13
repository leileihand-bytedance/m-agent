"""时间校准模块.

提供审核用时间基准。
"""
from __future__ import annotations

from datetime import datetime


def get_beijing_time() -> datetime:
    """获取当前本地时间作为基准。

    Returns:
        datetime: 当前本地时间
    """
    return datetime.now()