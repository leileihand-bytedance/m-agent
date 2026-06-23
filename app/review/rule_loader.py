"""规则库加载器 (给 LLM 读).

第一版规则库是给 LLM 读的"审核清单",不是一个解析配置:
  - 整文件读成纯文本,直接拼到 prompt 里
  - 不解析 frontmatter / 不分条规则
  - Bot 启动时加载(带 mtime 缓存,改 rules.md 后重启 Bot 才生效)

后续如果要"按规则类型分别调用不同 prompt"再扩展。
"""

from __future__ import annotations

import os
from pathlib import Path


_rules_cache_text: str | None = None
_rules_cache_mtime: float | None = None


def load_rules(rules_md_path) -> str:
    """加载 rules.md,返回纯文本内容(给 LLM 读).

    Args:
        rules_md_path: 规则库文件路径(支持相对路径,会自动找项目根)

    Returns:
        rules.md 的完整文本内容
    """
    global _rules_cache_text, _rules_cache_mtime

    path = Path(rules_md_path)
    if not path.is_absolute():
        # 相对路径:相对项目根(M-Agent/)
        # reviewer.py 在 app/review/,项目根是 parents[2]
        project_root = Path(__file__).resolve().parents[2]
        path = project_root / path

    if not path.exists():
        return f"(规则库文件不存在:{path})"

    mtime = path.stat().st_mtime
    if _rules_cache_text is not None and _rules_cache_mtime == mtime:
        return _rules_cache_text

    text = path.read_text(encoding="utf-8")
    _rules_cache_text = text
    _rules_cache_mtime = mtime
    return text


def clear_cache():
    """清空缓存(测试用)."""
    global _rules_cache_text, _rules_cache_mtime
    _rules_cache_text = None
    _rules_cache_mtime = None