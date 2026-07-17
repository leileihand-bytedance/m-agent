"""格式类规则代码检测器.

规则:
- quote-pair: 引号不成对
- num-unit: 数字和单位之间有空格
- mixed-punct: 中英文标点混用
- consecutive-punct: 连续相同标点（如。。、！！）
- toc-no-ordinal: 目录项带序号
- toc-seq-skip: 目录序号跳号
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .toc_utils import find_toc_range

if TYPE_CHECKING:
    from .core.models import Finding


# ============================================================
# 辅助函数
# ============================================================

def _find_toc_range(paragraphs: list[str]) -> tuple[int, int]:
    return find_toc_range(paragraphs)


def _is_toc_entry(paragraphs: list[str], idx: int, toc_end: int) -> bool:
    """判断段 idx 是否在目录区域(到 toc_end 之前)。"""
    return idx >= 0 and idx < toc_end


# ============================================================
# quote-pair: 引号不成对
# ============================================================

def check_quote_pair(paragraphs: list[str]) -> list["Finding"]:
    """检测引号不成对."""
    from .core.models import Finding
    findings = []
    symmetric_quotes = {'"'}
    paired_quotes = {
        '《': '》',
        '『': '』',
        '「': '」',
        '“': '”',
        '‘': '’',
    }
    reverse_quotes = {close: open_ for open_, close in paired_quotes.items()}

    def _build_target_text(
        text: str,
        quote_char: str,
        pos: int,
        is_opening: bool,
        unmatched_count: int,
    ) -> str:
        total_count = text.count(quote_char)
        if total_count <= 1 or total_count == unmatched_count:
            return quote_char
        if is_opening:
            return text[pos:min(len(text), pos + 12)]
        return text[max(0, pos - 11):pos + 1]

    def _is_english_word_char(char: str) -> bool:
        return bool(char) and char.isascii() and char.isalnum()

    def _is_english_apostrophe(
        text: str,
        pos: int,
        quote_char: str,
        stack: list[tuple[str, int]],
    ) -> bool:
        previous = text[pos - 1] if pos > 0 else ""
        following = text[pos + 1] if pos + 1 < len(text) else ""

        # Curly apostrophes are often normalized in either direction by editors.
        if _is_english_word_char(previous) and _is_english_word_char(following):
            return True

        # Plural possessives such as "customers’ needs" end after s/S. A real
        # closing Chinese single quote still closes the pending opening quote.
        has_pending_single_quote = bool(stack and stack[-1][0] == "‘")
        return (
            quote_char == "’"
            and previous in "sS"
            and not _is_english_word_char(following)
            and not has_pending_single_quote
        )

    def check_paragraph(text: str) -> list[tuple[str, int, bool]]:
        """返回这一段里所有未配对的引号类型和位置。"""
        stack: list[tuple[str, int]] = []
        unmatched: list[tuple[str, int, bool]] = []

        for idx, char in enumerate(text):
            if char in {"‘", "’"} and _is_english_apostrophe(text, idx, char, stack):
                continue

            if char in symmetric_quotes:
                if stack and stack[-1][0] == char:
                    stack.pop()
                else:
                    stack.append((char, idx))
                continue

            if char in paired_quotes:
                stack.append((char, idx))
                continue

            if char in reverse_quotes:
                expected_open = reverse_quotes[char]
                if stack and stack[-1][0] == expected_open:
                    stack.pop()
                else:
                    unmatched.append((char, idx, False))

        unmatched.extend((quote_char, pos, True) for quote_char, pos in stack)
        return unmatched

    for idx, para in enumerate(paragraphs):
        unmatched = check_paragraph(para)
        unmatched_counts: dict[str, int] = {}
        for qtype, _, _ in unmatched:
            unmatched_counts[qtype] = unmatched_counts.get(qtype, 0) + 1

        for qtype, pos, is_opening in unmatched:
            findings.append(Finding(
                rule_id="quote-pair",
                paragraph_index=idx,
                line_number=idx + 1,
                original_text=para,
                description=f"引号'{qtype}'只开不闭或只闭不开",
                target_text=_build_target_text(
                    para,
                    qtype,
                    pos,
                    is_opening,
                    unmatched_counts.get(qtype, 1),
                ),
            ))

    return findings


# ============================================================
# num-unit: 数字和单位之间有空格
# ============================================================

# 常见中文数字形式
_CHINESE_DIGIT_RE = re.compile(r'\d+\.?\d+%?')


def check_num_unit(paragraphs: list[str]) -> list["Finding"]:
    """检测数字和单位之间有空格(如 '3 万亿元')。"""
    from .core.models import Finding
    findings = []
    # 匹配: 数字 空白 中文单位
    # 中文单位通常是: 万 亿 千 元 吨 万吨 等
    pattern = re.compile(
        r'(\d+\.?\d+%?)(\s+)([万千亿个点]?[零一二三四五六七八九十百千万亿]?[元吨万千百亿个]?)'
    )

    for idx, para in enumerate(paragraphs):
        # 跳过引号内的内容(在引号内可能有正常的空格)
        for m in pattern.finditer(para):
            num_part = m.group(1)
            space = m.group(2)
            unit = m.group(3)
            # 如果空格存在 且 单位是中文单位
            if len(space) > 0 and unit:
                # 排除合理的: "5 %" (英文单位), "3.14" 等
                # 确认是中文单位开头
                if any(c >= '一' for c in unit):
                    findings.append(Finding(
                        rule_id="num-unit",
                        paragraph_index=idx,
                        line_number=idx + 1,
                        original_text=para,
                        description=f"数字'{num_part}'和中文单位'{unit}'之间有空格",
                        target_text=f"{num_part}{space}{unit}",
                    ))
    return findings


# ============================================================
# mixed-punct: 中英文标点混用
# ============================================================

# 中文文本中不应出现的英文标点
_EN_PUNCT_RE = re.compile(r'[一-鿿][,.:;!?](?!\w)|(?<!\w)[,.:;!?][一-鿿]')


def check_mixed_punct(paragraphs: list[str]) -> list["Finding"]:
    """检测中英文标点混用."""
    from .core.models import Finding
    findings = []

    for idx, para in enumerate(paragraphs):
        # 找所有英文标点在中文语境中的位置
        for m in _EN_PUNCT_RE.finditer(para):
            # 确认是中文句子里的英文标点(不是引号内的)
            pos = m.start()
            if m.group().startswith(".") and re.search(r"\d+\s*$", para[:pos]):
                # 问卷/列表编号允许“12.请说明”或“12 .请说明”。
                continue
            # 检查前后是否是中文字符
            before = para[pos - 1] if pos > 0 else ''
            after = para[m.end()] if m.end() < len(para) else ''
            # 简单判断: 前后有中文则算中文句子内的英文标点
            if ('一' <= before <= '鿿' or '一' <= after <= '鿿'):
                findings.append(Finding(
                    rule_id="mixed-punct",
                    paragraph_index=idx,
                    line_number=idx + 1,
                    original_text=para,
                    description=f"中文句子里出现英文标点'{m.group()}'",
                    target_text=para[pos:m.end()],
                ))
    return findings


# ============================================================
# consecutive-punct: 连续相同标点
# ============================================================

_CONSECUTIVE_PUNCT_RE = re.compile(r'[，。！？；]{2,}|(?<=[？！；])([？！；])')


def check_consecutive_punct(paragraphs: list[str]) -> list["Finding"]:
    """检测连续相同标点字符（如。。、！！），排除书名号/引号后紧跟标点的正常用法。"""
    from .core.models import Finding
    findings = []

    for idx, para in enumerate(paragraphs):
        for m in _CONSECUTIVE_PUNCT_RE.finditer(para):
            repeated = m.group()
            # 跳过书名号/引号后紧跟标点的情况（如"》。"、"》，"是正常用法）
            if m.start() > 0:
                prev_char = para[m.start() - 1]
                if prev_char in '""''""''《』（）【】':
                    continue
            findings.append(Finding(
                rule_id="consecutive-punct",
                paragraph_index=idx,
                line_number=idx + 1,
                original_text=para,
                description=f"连续相同标点: '{repeated}'",
                target_text=repeated,
            ))
    return findings


# ============================================================
# toc-no-ordinal: 目录项/章节标题带序号
# ============================================================

_ORDINAL_RE = re.compile(r'^[一二三四五六七八九十]+[、.．]')

# 正文区章节分类关键词(4字板块名)
_SECTION_KEYWORDS = {"党政要闻", "监管动态", "市场观察", "前沿观点", "同业动向", "同业动态"}


def check_toc_no_ordinal(paragraphs: list[str]) -> list["Finding"]:
    """检测目录项和正文区章节标题带'一、二、三'序号."""
    from .core.models import Finding
    toc_start, toc_end = _find_toc_range(paragraphs)
    findings = []

    for idx in range(len(paragraphs)):
        para = paragraphs[idx].strip()
        # 去掉末尾的 PAGEREF 等 word 字段
        clean = re.sub(r'PAGEREF[^\s]+', '', para).strip()

        if not _ORDINAL_RE.match(clean):
            continue

        # 去掉序号后看剩下什么
        remaining = _ORDINAL_RE.sub('', clean)

        # 情况1: 目录区域的目录项(去掉序号后可以是任意内容)
        if toc_start <= idx < toc_end:
            findings.append(Finding(
                rule_id="toc-no-ordinal",
                paragraph_index=idx,
                line_number=idx + 1,
                original_text=para,
                description=f"目录项不应带'一、二、三'序号:'{clean[:20]}'",
            ))
        # 情况2: 正文区的章节标题(如"二、监管动态"去掉序号后剩"监管动态")
        elif remaining in _SECTION_KEYWORDS:
            findings.append(Finding(
                rule_id="toc-no-ordinal",
                paragraph_index=idx,
                line_number=idx + 1,
                original_text=para,
                description=f"正文区章节标题不应带序号:'{clean}'",
            ))
    return findings


# ============================================================
# toc-seq-skip: 目录序号跳号
# ============================================================

_ORDINAL_NUM_RE = re.compile(r'^([一二三四五六七八九十]+)[、.．]')


def _ordinal_to_num(s: str) -> int:
    """把中文数字转成阿拉伯数字."""
    map_ = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
            '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
    if s == '十':
        return 10
    if len(s) == 2 and s[1] == '十':
        return 10 + map_.get(s[0], 0)
    return map_.get(s, 0)


def check_toc_seq_skip(paragraphs: list[str]) -> list["Finding"]:
    """检测目录序号跳号."""
    from .core.models import Finding
    toc_start, toc_end = _find_toc_range(paragraphs)
    findings = []

    ordinals: list[tuple[int, int, str]] = []  # (段号, 序号数字, 原始文本)
    for idx in range(toc_start, toc_end):
        para = paragraphs[idx].strip()
        clean = re.sub(r'PAGEREF[^\s]+', '', para).strip()
        m = _ORDINAL_NUM_RE.match(clean)
        if m:
            num = _ordinal_to_num(m.group(1))
            ordinals.append((idx, num, para))

    # 检查是否跳号
    prev_num = 0
    for idx, num, para in ordinals:
        if prev_num > 0 and num > prev_num + 1:
            findings.append(Finding(
                rule_id="toc-seq-skip",
                paragraph_index=idx,
                line_number=idx + 1,
                original_text=para,
                description=f"目录序号跳号:前一项是'{prev_num}',此项是'{num}',跳过'{prev_num+1}'",
            ))
        prev_num = num

    return findings


# ============================================================
# 统一入口
# ============================================================

FORMAT_RULE_CHECKERS = (
    ("quote-pair", check_quote_pair),
    ("num-unit", check_num_unit),
    ("mixed-punct", check_mixed_punct),
    ("consecutive-punct", check_consecutive_punct),
    ("toc-no-ordinal", check_toc_no_ordinal),
    ("toc-seq-skip", check_toc_seq_skip),
)


def check_format_rules(
    paragraphs: list[str],
    enabled_rule_ids: tuple[str, ...],
) -> list["Finding"]:
    """Run configured format rules in the established output order."""
    enabled = frozenset(enabled_rule_ids)
    known = {rule_id for rule_id, _ in FORMAT_RULE_CHECKERS}
    unknown = enabled - known
    if unknown:
        raise ValueError(f"Unknown format rules: {sorted(unknown)}")
    all_findings: list[Finding] = []
    for rule_id, checker in FORMAT_RULE_CHECKERS:
        if rule_id in enabled:
            all_findings.extend(checker(paragraphs))
    all_findings.sort(key=lambda finding: finding.paragraph_index)
    return all_findings


def check_all_format_rules(paragraphs: list[str]) -> list["Finding"]:
    """Backward-compatible entry point that runs the legacy six-rule set."""
    return check_format_rules(
        paragraphs,
        tuple(rule_id for rule_id, _ in FORMAT_RULE_CHECKERS),
    )
