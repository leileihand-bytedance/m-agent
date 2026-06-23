"""审核意见格式化器.

输出原则:
  - 不要写"第几段",人在文档里无法数段数
  - 直接引用原文片段,方便在文档里搜索定位
  - 按规则分组,每条问题单独编号(错误1/错误2...)
"""

from __future__ import annotations

from .reviewer import ReviewResult, Finding


def format_review_result(
    result: ReviewResult,
    filename: str,
    max_findings: int = 20,
) -> str:
    """将审核结果格式化为纯文本."""
    findings = result.findings
    total = len(findings)

    lines = [f"📄《{filename}》审核完成", ""]

    if total == 0:
        lines.append("✅ 未发现低级错误。")
        return "\n".join(lines)

    # 按段落顺序排列(按出现先后顺序)
    sorted_findings = sorted(findings, key=lambda f: f.paragraph_index)
    display = sorted_findings[:max_findings]

    shown = 0
    for i, f in enumerate(display, 1):
        rule_label = _rule_label(f.rule_id)
        lines.append(f"错误{i}:【{rule_label}】{f.description}")
        lines.append(f"所属段落：{f.original_text}")
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
        "content-out-of-scope": "内容不在收录范围",
        "content-wrong-section": "内容放错板块",
        "content-duplicate": "重复内容",
        "content-outdated": "过时信息",
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
        quote = f.original_text[:80].replace("\n", " ")
        lines.append(f"### 错误{i}. [{rule_label}]")
        lines.append(f"- 原文: {quote}")
        lines.append(f"- 问题: {f.description}")
        lines.append("")
    return "\n".join(lines)
