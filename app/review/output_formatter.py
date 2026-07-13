"""审核意见格式化器.

输出原则:
  - 不要写"第几段",人在文档里无法数段数
  - 直接引用原文片段,方便在文档里搜索定位
  - 按规则分组,每条问题单独编号(错误1/错误2...)
"""

from __future__ import annotations

import re

from .reviewer import ReviewResult, Finding
from .document_type import DocumentType, document_type_label

# 敏感词列表（触发企业微信反垃圾检查的词）
_SPAM_SENSITIVE_PATTERNS = [
    re.compile(r"军事打击|武装冲突|空袭|导弹|核武器|生化|战争"),
    re.compile(r"哈梅内伊|内贾德|苏莱曼尼|伊朗领袖|伊朗总统"),
    re.compile(r"封锁霍尔木兹|石油运输受阻"),
]


def _sanitize_text(text: str) -> str:
    """移除内部段号并替换敏感词，生成用户可理解的文本."""
    text = re.sub(r"第\s*\d+\s*段", "文中另一处", text)
    text = re.sub(r"段落\s*\d+", "文中另一处", text)
    text = re.sub(r"paragraph\s*\d+", "文中另一处", text, flags=re.IGNORECASE)
    result = text
    for pattern in _SPAM_SENSITIVE_PATTERNS:
        result = pattern.sub("[敏感内容]", result)
    return result


def format_review_result(
    result: ReviewResult,
    filename: str,
    max_findings: int = 20,
    doc_type: DocumentType = DocumentType.NEI_CAN,
) -> str:
    """将审核结果格式化为纯文本."""
    findings = result.findings
    total = len(findings)
    type_label = document_type_label(doc_type)

    lines = [f"📄《{filename}》({type_label})审核完成", ""]

    if total == 0:
        lines.append("✅ 未发现低级错误。")
        return "\n".join(lines)

    # 按段落顺序排列(按出现先后顺序)
    sorted_findings = sorted(findings, key=lambda f: f.paragraph_index)
    display = sorted_findings[:max_findings]

    shown = 0
    for i, f in enumerate(display, 1):
        rule_label = _rule_label(f.rule_id)
        # 描述和原文都要脱敏
        safe_description = _sanitize_text(f.description)
        lines.append(f"错误{i}:【{rule_label}】{safe_description}")
        # 原文只显示前40字，且做敏感词替换
        original = _sanitize_text(f.original_text.replace("\n", " "))[:40]
        lines.append(f"所属段落：{original}...")
        if i < len(display):
            lines.append("")
        shown = i

    if shown < total:
        lines.append("")
        lines.append(f"... 还有 {total - shown} 处问题未显示")

    return "\n".join(lines)


def format_phase1_result(result: ReviewResult, max_findings: int = 20) -> str:
    """格式化第一阶段审核结果（发给用户的第一条消息）。

    格式：
    第一阶段审核结果如下：

    共 N 条：
    错误1:【规则标签】问题描述
    所属段落：原文...
    ...

    第二阶段审核中，请稍候...
    """
    findings = result.findings
    total = len(findings)
    sorted_findings = sorted(findings, key=lambda f: f.paragraph_index)
    display = sorted_findings[:max_findings]

    lines = ["第一阶段审核结果如下：", ""]
    lines.append(f"共 {total} 条：")

    shown = 0
    for i, f in enumerate(display, 1):
        rule_label = _rule_label(f.rule_id)
        safe_description = _sanitize_text(f.description)
        lines.append(f"错误{i}:【{rule_label}】{safe_description}")
        original = _sanitize_text(f.original_text.replace("\n", " "))[:40]
        lines.append(f"所属段落：{original}...")
        if i < len(display):
            lines.append("")
        shown = i

    if shown < total:
        lines.append("")
        lines.append(f"... 还有 {total - shown} 处问题未显示")

    lines.append("")
    lines.append("第二阶段审核中，请稍候...")

    return "\n".join(lines)


def format_phase2_result(result: ReviewResult, max_findings: int = 20) -> str:
    """格式化第二阶段审核结果（追加发给用户的第二条消息）。

    格式：
    第二阶段审核结果如下：

    共 N 条：
    错误1:【规则标签】问题描述
    所属段落：原文...
    ...
    """
    findings = result.findings
    total = len(findings)
    sorted_findings = sorted(findings, key=lambda f: f.paragraph_index)
    display = sorted_findings[:max_findings]

    lines = ["第二阶段审核结果如下：", ""]
    lines.append(f"共 {total} 条：")

    shown = 0
    for i, f in enumerate(display, 1):
        rule_label = _rule_label(f.rule_id)
        safe_description = _sanitize_text(f.description)
        lines.append(f"错误{i}:【{rule_label}】{safe_description}")
        original = _sanitize_text(f.original_text.replace("\n", " "))[:40]
        lines.append(f"所属段落：{original}...")
        if i < len(display):
            lines.append("")
        shown = i

    if shown < total:
        lines.append("")
        lines.append(f"... 还有 {total - shown} 处问题未显示")

    return "\n".join(lines)


def _rule_label(rule_id: str) -> str:
    """把 rule_id 转成人类可读的中文标签."""
    labels = {
        "title-truncated": "标题截断",
        "content-mismatch": "标题正文不匹配",
        "content-incomplete": "内容不完整",
        "quote-pair": "引号不成对",
        "toc-no-ordinal": "目录项带序号",
        "toc-seq-skip": "目录序号跳号",
        "toc-mismatch": "目录正文不匹配",
        "num-unit": "数字单位格式",
        "mixed-punct": "中英文标点混用",
        "consecutive-punct": "连续相同标点",
        "content-out-of-scope": "内容不在收录范围",
        "content-wrong-section": "内容放错板块",
        "content-duplicate": "重复内容",
        "content-outdated": "过时信息",
        "halfmonthly-date-mismatch": "半月报时间范围不符",
        "halfmonthly-section-order": "半月报板块顺序",
        "halfmonthly-section-mismatch": "半月报标题归属不符",
        "halfmonthly-leader-title": "半月报领导职务规范",
        "general-typo": "错别字",
        "general-name-error": "名称错误",
        "general-grammar": "语病",
        "general-punctuation": "标点错误",
        "general-incomplete": "内容不完整",
        "general-duplicate": "重复内容",
        "general-placeholder": "占位内容",
        "general-heading-seq-skip": "标题编号跳号",
        "general-heading-empty": "标题后无正文",
        "general-reference-missing": "引用悬空",
        "general-attachment-name-mismatch": "附件名称不一致",
        "general-invalid-date": "日期常识错误",
        "general-date-range-logic": "时间范围逻辑错误",
        "general-logic-inconsistency": "前后逻辑不一致",
        "general-term-variant": "术语写法",
    }
    return labels.get(rule_id, rule_id)


def build_report_markdown(result: ReviewResult, filename: str) -> str:
    """生成完整审核报告 (Markdown 格式,用于存档)."""
    lines = [
        f"# 审核报告: {filename}",
        "",
        f"- 总问题数: {len(result.findings)}",
        f"- 检查规则数: {result.total_rules}",
        f"- 通过规则数: {result.passed_rules}",
        "",
        "## 详细发现",
        "",
    ]
    for i, f in enumerate(result.findings, 1):
        rule_label = _rule_label(f.rule_id)
        quote = _sanitize_text(f.original_text[:80].replace("\n", " "))
        lines.append(f"### 错误{i}. [{rule_label}]")
        lines.append(f"- 原文: {quote}")
        lines.append(f"- 问题: {_sanitize_text(f.description)}")
        lines.append("")
    return "\n".join(lines)
