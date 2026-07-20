"""原文核对模块.

对有"原文引用"的段落做一致性核对：
1. 提取"原文:"后的原始文本
2. 调用 LLM 核对摘要是否准确反映原文
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model_config import build_anthropic_client
from .core.model_runtime import create_model_message

def _get_client() -> tuple[Any, str]:
    """获取审核模块使用的 API 客户端。"""
    return build_anthropic_client()


@dataclass
class VerificationResult:
    """原文核对结果。"""
    accurate: bool              # 摘要是否准确反映原文
    deviations: list[str]       # 偏差描述列表
    missing_key_points: list[str]  # 摘要遗漏的关键点


def extract_original_text(paragraph: str) -> str | None:
    """提取段落中的原文引用。

    支持多种格式：
    - "正文内容...原文:这是原始文本内容..."
    - "正文内容...原文 这是原始文本内容..."（无冒号）
    - "【原文】这是原始文本内容..."

    Args:
        paragraph: 完整段落文本

    Returns:
        原文文本，或 None（无原文引用）
    """
    # 优先找 "原文:" 格式
    if "原文:" in paragraph:
        return paragraph.split("原文:", 1)[1].strip()

    # 尝试 "原文 " 格式（无冒号）
    if "原文 " in paragraph:
        return paragraph.split("原文 ", 1)[1].strip()

    # 尝试 "【原文】" 格式
    if "【原文】" in paragraph:
        return paragraph.split("【原文】", 1)[1].strip()

    return None


def build_citation_prompt(summary: str, original: str) -> str:
    """构建原文核对的 prompt。

    要求 LLM：
    1. 提取原文关键信息（谁、何时、何事、关键数据）
    2. 检查摘要是否遗漏关键点或引入原文没有的信息
    3. 输出结构化结果
    """
    return f"""你是原文核对专家。请核对以下摘要是否准确反映了原文内容。

【摘要】
{summary}

【原文】
{original}

请按以下 JSON 格式输出（只输出 JSON，不要其他内容）：
{{
  "accurate": true或false，摘要是否准确反映原文
  "deviations": ["偏差1描述", "偏差2描述"]，如果 inaccurate
  "missing_key_points": ["遗漏关键点1", "遗漏关键点2"]，如果有任何遗漏
}}

判断标准：
- 摘要遗漏原文中的关键人物，会议名称、数据、时间点 → 不准确
- 摘要添加了原文没有的信息（原文未提及）→ 不准确
- 摘要与原文语义一致，只是简略 → accurate=True
"""


def verify_citation(summary: str, original: str) -> VerificationResult:
    """核对摘要是否准确反映原文。

    调用 LLM 做细粒度核对。

    Args:
        summary: 摘要/正文文本
        original: 原文引用文本

    Returns:
        VerificationResult: 核对结果
    """
    client, model_name = _get_client()
    prompt = build_citation_prompt(summary, original)

    message = create_model_message(
        client,
        metrics=None,
        stage="citation_verification",
        model=model_name,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        timeout=60.0,
    )

    text = message.content[0].text if message.content else ""

    # 解析 JSON
    import json, re
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if not json_match:
        return VerificationResult(accurate=True, deviations=[], missing_key_points=[])

    try:
        data = json.loads(json_match.group())
        return VerificationResult(
            accurate=data.get("accurate", True),
            deviations=data.get("deviations", []),
            missing_key_points=data.get("missing_key_points", []),
        )
    except json.JSONDecodeError:
        return VerificationResult(accurate=True, deviations=[], missing_key_points=[])
