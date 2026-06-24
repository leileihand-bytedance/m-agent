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

if TYPE_CHECKING:
    from .reviewer import Finding


# ============================================================
# 辅助函数
# ============================================================

def _find_toc_range(paragraphs: list[str]) -> tuple[int, int]:
    toc_start = -1
    toc_end = len(paragraphs)

    for i, p in enumerate(paragraphs):
        stripped = p.strip()
        if stripped == "目录":
            toc_start = i + 1
        elif toc_start >= 0 and ("主编" in stripped or "责编" in stripped):
            toc_end = i
            break

    if toc_start < 0:
        return 0, 0
    return toc_start, toc_end


def _is_toc_entry(paragraphs: list[str], idx: int, toc_end: int) -> bool:
    """判断段 idx 是否在目录区域(到 toc_end 之前)。"""
    return idx >= 0 and idx < toc_end


# ============================================================
# quote-pair: 引号不成对
# ============================================================

def check_quote_pair(paragraphs: list[str]) -> list["Finding"]:
    """检测引号不成对."""
    from .reviewer import Finding
    findings = []
    # 追踪开引号
    open_quotes: list[tuple[int, str]] = []  # (段号, 引号类型)

    QUOTE_PAIRS = [
        ('"', '"'),
        ('"', '"'),
        ('《', '》'),
        ('『', '』'),
        ('「', '」'),
    ]

    def check_paragraph(idx: int, text: str) -> list[tuple[int, str]]:
        """返回这一段里所有未配对的开引号。"""
        opens = []
        i = 0
        while i < len(text):
            for open_q, close_q in QUOTE_PAIRS:
                if text[i:i+len(open_q)] == open_q:
                    opens.append((i, open_q))
                    i += len(open_q)
                    break
                elif text[i:i+len(close_q)] == close_q:
                    if opens and opens[-1][1] == open_q:
                        opens.pop()
                    i += len(close_q)
                    break
            else:
                i += 1
        return opens

    for idx, para in enumerate(paragraphs):
        opens = check_paragraph(idx, para)
        for _, qtype in opens:
            findings.append(Finding(
                rule_id="quote-pair",
                paragraph_index=idx,
                line_number=idx + 1,
                original_text=para,
                description=f"引号'{qtype}'只开不闭或只闭不开",
            ))

    return findings


# ============================================================
# num-unit: 数字和单位之间有空格
# ============================================================

# 常见中文数字形式
_CHINESE_DIGIT_RE = re.compile(r'\d+\.?\d+%?')


def check_num_unit(paragraphs: list[str]) -> list["Finding"]:
    """检测数字和单位之间有空格(如 '3 万亿元')。"""
    from .reviewer import Finding
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
                    ))
    return findings


# ============================================================
# mixed-punct: 中英文标点混用
# ============================================================

# 中文文本中不应出现的英文标点
_EN_PUNCT_RE = re.compile(r'[一-鿿][,.:;!?](?!\w)|(?<!\w)[,.:;!?][一-鿿]')


def check_mixed_punct(paragraphs: list[str]) -> list["Finding"]:
    """检测中英文标点混用."""
    from .reviewer import Finding
    findings = []

    for idx, para in enumerate(paragraphs):
        # 找所有英文标点在中文语境中的位置
        for m in _EN_PUNCT_RE.finditer(para):
            # 确认是中文句子里的英文标点(不是引号内的)
            pos = m.start()
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
                ))
    return findings


# ============================================================
# consecutive-punct: 连续相同标点
# ============================================================

_CONSECUTIVE_PUNCT_RE = re.compile(r'[，。！？；]{2,}|(?<=[？！；])([？！；])')


def check_consecutive_punct(paragraphs: list[str]) -> list["Finding"]:
    """检测连续相同标点字符（如。。、！！），排除书名号/引号后紧跟标点的正常用法。"""
    from .reviewer import Finding
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
            ))
    return findings


# ============================================================
# toc-no-ordinal: 目录项/章节标题带序号
# ============================================================

_ORDINAL_RE = re.compile(r'^[一二三四五六七八九十]+[、.．]')

# 正文区章节分类关键词(4字板块名)
_SECTION_KEYWORDS = {"党政要闻", "监管动态", "市场观察", "前沿观点", "同业动向"}


def check_toc_no_ordinal(paragraphs: list[str]) -> list["Finding"]:
    """检测目录项和正文区章节标题带'一、二、三'序号."""
    from .reviewer import Finding
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
    from .reviewer import Finding
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

def check_all_format_rules(paragraphs: list[str]) -> list["Finding"]:
    """运行所有格式类规则检测,合并结果."""
    from .reviewer import Finding
    all_findings: list = []
    all_findings.extend(check_quote_pair(paragraphs))
    all_findings.extend(check_num_unit(paragraphs))
    all_findings.extend(check_mixed_punct(paragraphs))
    all_findings.extend(check_consecutive_punct(paragraphs))
    all_findings.extend(check_toc_no_ordinal(paragraphs))
    all_findings.extend(check_toc_seq_skip(paragraphs))
    # 按段号排序
    all_findings.sort(key=lambda f: f.paragraph_index)
    return all_findings
