"""通用审核术语库加载器.

负责加载 app/review/term_library_general_webank.json。
支持相对项目根路径、简单 mtime 缓存、缺文件安全降级。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# 从当前文件向上两级定位项目根目录。
_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_LIBRARY_PATH = _ROOT / "app" / "review" / "term_library_general_webank.json"

# 简单 mtime 缓存: {path: (mtime, data)}
_LIBRARY_CACHE: dict[Path, tuple[float, list[dict[str, Any]]]] = {}


def load_term_library(path: Path | str | None = None) -> list[dict[str, Any]]:
    """加载通用审核术语库.

    Args:
        path: 术语库 JSON 路径,默认使用 app/review/term_library_general_webank.json。

    Returns:
        术语条目列表。文件不存在或解析失败时返回空列表,不抛异常。
    """
    target = Path(path) if path else DEFAULT_LIBRARY_PATH
    if not target.is_absolute():
        target = _ROOT / target

    try:
        mtime = target.stat().st_mtime
    except (OSError, FileNotFoundError):
        return []

    cached = _LIBRARY_CACHE.get(target)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(raw, list):
        return []

    _LIBRARY_CACHE[target] = (mtime, raw)
    return raw


def clear_term_library_cache() -> None:
    """清空术语库缓存,主要用于测试."""
    _LIBRARY_CACHE.clear()
