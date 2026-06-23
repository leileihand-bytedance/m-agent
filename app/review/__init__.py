"""智能审核模块 (review module).

两层架构:
  - 格式类规则: format_checker.py 正则检测
  - 语义类规则: LLM CoT + 结构化输出 + 多次调用取并集
"""

from .parser import parse_docx, ParsedDocxResult
from .rule_loader import load_rules
from .reviewer import ReviewResult, Finding, review_text
from .format_checker import check_all_format_rules
from .output_formatter import format_review_result

__all__ = [
    "parse_docx",
    "ParsedDocxResult",
    "load_rules",
    "ReviewResult",
    "Finding",
    "review_text",
    "check_all_format_rules",
    "format_review_result",
]