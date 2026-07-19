from __future__ import annotations

from functools import lru_cache

from opencc import OpenCC


@lru_cache(maxsize=1)
def _traditional_to_simplified_converter() -> OpenCC:
    return OpenCC("t2s")


def to_simplified_chinese(text: str) -> str:
    """把入选稿件统一转换为简体中文。"""
    if not text:
        return text
    return _traditional_to_simplified_converter().convert(text)


__all__ = ["to_simplified_chinese"]
