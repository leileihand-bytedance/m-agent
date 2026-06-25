"""审核引擎.

两层架构:
  - 格式类规则(引号/数字单位/标点/目录序号) → format_checker.py 正则检测
  - 语义类规则(截断/错配/完整性/内容质量) → LLM CoT + 结构化输出

rules.md 是给 LLM 读的"审核清单",LLM 只处理语义类规则。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .format_checker import check_all_format_rules


@dataclass(frozen=True)
class Finding:
    """一条审核发现."""
    rule_id: str            # 规则 ID(如 "dupe-char-check")
    paragraph_index: int    # 段号(0-indexed)
    line_number: int        # 估算行号(段号 + 1)
    original_text: str      # 该段原始内容
    description: str        # 问题描述


@dataclass(frozen=True)
class ReviewResult:
    """完整审核结果."""
    findings: list[Finding]
    total_rules: int
    passed_rules: int
    filename: str


# ============================================================
# LLM 调用
# ============================================================

def _get_anthropic_client():
    """从环境变量或 .env 构造 anthropic client."""
    import anthropic
    # 优先从环境变量读,否则从 .env 读
    api_key = os.environ.get("ANTHROPIC_API_KEY") or ""
    if not api_key:
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break
    base_url = os.environ.get("ANTHROPIC_BASE_URL") or "https://api.minimaxi.com/anthropic"
    model_name = os.environ.get("MODEL_NAME") or "MiniMax-M2.7"
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY 未设置(请在 .env 或环境变量中配置)")
    return anthropic.Anthropic(api_key=api_key, base_url=base_url), model_name


# 语义类规则 ID 列表(由 LLM 处理)
SEMANTIC_RULE_IDS = (
    "title-truncated",
    "content-mismatch",
    "content-incomplete",
    "toc-mismatch",
    "content-out-of-scope",
    "content-wrong-section",
    "content-duplicate",
    "content-outdated",
)

# 第一阶段规则（格式正则 + 基础内容LLM）
PHASE1_RULES = (
    "title-truncated",
    "content-mismatch",
    "content-incomplete",
    "toc-mismatch",
)

# 第二阶段规则（深度内容LLM）
PHASE2_RULES = (
    "content-out-of-scope",
    "content-wrong-section",
    "content-duplicate",
    "content-outdated",
)


def _build_prompt(rules_text: str, paragraphs: list[str], filename: str) -> str:
    """构造发给 LLM 的 prompt(仅语义类规则)。"""
    paras_text = "\n\n".join(
        f"[段 {i+1}]\n{p}" for i, p in enumerate(paragraphs)
    )

    prompt = f"""你是一位严谨的中文文档审核员。

# 审核规则清单(仅语义类规则,格式类已由代码检测)

{rules_text}

# 待审文档

文件名:{filename}

{paras_text}

# 你的任务

按以下步骤思考并输出:

## 步骤 1:识别文档结构

用几行文字描述:
- 文档头:哪几段
- 目录区:从哪段到哪段
- 页脚:哪段
- 正文区:其余部分

## 步骤 2:逐段落分析

对正文区的每个段落,判断其类型(章节分类/新闻标题/正文/原文引用),然后执行语义类检查:

**仅标题段需要检查:**
- title-truncated: 标题说的 ≠ 正文说的同一件事 → 标题截断
- content-mismatch: 标题和正文完全不同 → 错配

**仅正文段需要检查:**
- content-incomplete: 段末语义截断,缺宾语或结束语

**目录区域检查:**
- toc-mismatch: 目录与正文在章节名/标题/顺序上对不上

**内容质量检查:**
- content-out-of-scope: 跟银行经营/宏观经济完全无关
- content-wrong-section: 内容明显属于某板块但放到了别处
- content-duplicate: 同一件事出现两次以上
- content-outdated: 信息明显早于周报时间范围

## 步骤 3:输出 JSON

**严格按以下格式输出,只输出 JSON,不要任何其他文字:**

```json
{{
  "reasoning": "简要分析思路(段落分类、每条规则的检查结论,100字以内)",
  "issues": [
    {{"paragraph_index": 0, "rule_id": "xxx", "original_text": "该段完整原文", "description": "问题描述"}}
  ]
}}
```

**关键规则:**
- paragraph_index 从 0 开始
- rule_id 必须是以下之一:{", ".join(SEMANTIC_RULE_IDS)}
- original_text 必须是该段的**完整原文**,不要截断
- **不确定的问题不要写,宁可漏报不要误报**
- 文档完全没问题 → `{{"issues": []}}`
- 每条 issue 的 description 要简洁,不超过50字
"""
    return prompt


def _build_phase_prompt(
    rules_text: str,
    paragraphs: list[str],
    filename: str,
    phase: int,
) -> str:
    """构造发给 LLM 的分阶段 prompt。

    phase=1: 基础语义检查（title-truncated/content-mismatch/content-incomplete）
    phase=2: 深度内容检查（toc-mismatch/content-out-of-scope/...）

    仅将对应阶段的规则子集传给 LLM，减少单次调用的 token 消耗。
    """
    paras_text = "\n\n".join(
        f"[段 {i+1}]\n{p}" for i, p in enumerate(paragraphs)
    )

    if phase == 1:
        target_rules = PHASE1_RULES
        phase_desc = "基础语义检查（标题完整性、标题-正文匹配性、正文完整性、目录匹配性）"
    else:
        target_rules = PHASE2_RULES
        phase_desc = "深度内容检查（内容相关性、内容归位、重复检测、信息时效性）"

    prompt = f"""你是一位严谨的中文文档审核员。

# 审核规则清单（{phase_desc}）

{rules_text}

# 待审文档

文件名:{filename}

{paras_text}

# 你的任务

按以下步骤思考并输出:

## 步骤 1:识别文档结构

用几行文字描述:
- 文档头:哪几段
- 目录区:从哪段到哪段
- 页脚:哪段
- 正文区:其余部分

## 步骤 2:逐段落分析

对正文区的每个段落,判断其类型(章节分类/新闻标题/正文/原文引用),然后执行语义类检查:

**阶段1规则（仅标题段需要检查）:**
- title-truncated: 标题说的 ≠ 正文说的同一件事 → 标题截断
- content-mismatch: 标题和正文完全不同 → 错配

**阶段1规则（仅正文段需要检查）:**
- content-incomplete: 段末语义截断,缺宾语或结束语

**阶段1规则（目录区域检查）:**
- toc-mismatch: 目录与正文在章节名/标题/顺序上对不上

**阶段2规则（内容质量检查）:**
- content-out-of-scope: 跟银行经营/宏观经济完全无关
- content-wrong-section: 内容明显属于某板块但放到了别处(如央行政策放在市场观察)
- content-duplicate: 同一件事出现两次以上
- content-outdated: 信息明显早于周报时间范围

## 步骤 3:输出 JSON

**严格按以下格式输出,只输出 JSON,不要任何其他文字:**

```json
{{
  "reasoning": "简要分析思路(段落分类、每条规则的检查结论,100字以内)",
  "issues": [
    {{"paragraph_index": 0, "rule_id": "xxx", "original_text": "该段完整原文", "description": "问题描述"}}
  ]
}}
```

**关键规则:**
- paragraph_index 从 0 开始
- rule_id 必须是以下之一:{", ".join(target_rules)}
- original_text 必须是该段的**完整原文**,不要截断
- **不确定的问题不要写,宁可漏报不要误报**
- 文档完全没问题 → `{{"issues": []}}`
- 每条 issue 的 description 要简洁,不超过50字
"""
    return prompt


def _parse_llm_output(
    output: str,
    paragraphs: list[str],
    allowed_rules: tuple[str, ...] = SEMANTIC_RULE_IDS,
) -> tuple[list[Finding], str]:
    """从 LLM 输出中解析 JSON,转成 Finding 列表.

    Returns:
        (findings, reasoning) — findings 列表和 reasoning 文字
    """
    text = output.strip()

    # 1. 去掉可能的 ```json 包裹
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # 2. 找 JSON 主体
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]

    # 3. 解析
    reasoning = ""
    try:
        data = json.loads(text)
        reasoning = str(data.get("reasoning", ""))[:200]
    except json.JSONDecodeError:
        return [], ""

    issues = data.get("issues", [])
    if not isinstance(issues, list):
        return [], reasoning

    findings = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        rule_id = str(issue.get("rule_id", ""))
        # 只接受允许的规则 ID
        if rule_id not in allowed_rules:
            continue
        try:
            idx = int(issue.get("paragraph_index", -1))
        except (ValueError, TypeError):
            continue
        if idx < 0 or idx >= len(paragraphs):
            continue
        findings.append(Finding(
            rule_id=rule_id,
            paragraph_index=idx,
            line_number=idx + 1,
            original_text=str(issue.get("original_text", paragraphs[idx])),
            description=str(issue.get("description", ""))[:100],
        ))

    return findings, reasoning


async def review_phase1(
    paragraphs: list[str],
    rules_text: str,
    filename: str,
) -> ReviewResult:
    """第一阶段审核：格式正则 + 基础语义（title-truncated/content-mismatch/content-incomplete/toc-mismatch）。

    Returns:
        ReviewResult（含格式类 findings + phase1 语义 findings）
    """
    if not paragraphs:
        return ReviewResult(findings=[], total_rules=len(PHASE1_RULES) + 6, passed_rules=len(PHASE1_RULES) + 6, filename=filename)

    # 格式类规则（正则，秒级）
    format_findings = check_all_format_rules(paragraphs)

    # 基础语义 LLM（2次取并集）
    semantic_findings: list[Finding] = []
    llm_errors: list[str] = []

    async def _call_phase1_once(attempt: int) -> tuple[list[Finding], str, str | None]:
        """单次 LLM 调用（异步执行）。"""
        try:
            client, model_name = _get_anthropic_client()
            prompt = _build_phase_prompt(rules_text, paragraphs, filename, phase=1)
            message = await asyncio.to_thread(
                client.messages.create,
                model=model_name,
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
                timeout=180.0,
            )
            text_parts = []
            for block in message.content:
                if hasattr(block, "text") and block.text:
                    text_parts.append(block.text)
            output = "\n".join(text_parts)
            findings_part, reasoning = _parse_llm_output(output, paragraphs, PHASE1_RULES)
            reason_preview = reasoning[:40] if reasoning else "(无)"
            print(f"  phase1 第 {attempt+1} 次: {len(findings_part)} 条, reasoning: {reason_preview}...", flush=True)
            return findings_part, reasoning, None
        except Exception as exc:
            print(f"  phase1 第 {attempt+1} 次失败: {exc}", flush=True)
            return [], "", str(exc)

    # 并行执行两次 LLM 调用
    results = await asyncio.gather(
        _call_phase1_once(0),
        _call_phase1_once(1),
    )
    for findings_part, reasoning, err in results:
        if err:
            llm_errors.append(err)
        semantic_findings.extend(findings_part)

    # 全部失败
    if not semantic_findings and llm_errors:
        return ReviewResult(
            findings=[Finding(
                rule_id="__llm_error__",
                paragraph_index=0,
                line_number=1,
                original_text="(LLM 调用失败)",
                description=f"LLM phase1 调用失败:{'; '.join(llm_errors)}",
            )],
            total_rules=len(PHASE1_RULES) + 6,
            passed_rules=0,
            filename=filename,
        )

    # 语义 findings 去重
    merged: dict[tuple[str, int], Finding] = {}
    for f in semantic_findings:
        key = (f.rule_id, f.paragraph_index)
        if key not in merged or len(f.description) > len(merged[key].description):
            merged[key] = f
    semantic_findings = list(merged.values())

    # 格式类 + 语义类
    all_findings = list(semantic_findings)
    all_findings.extend(format_findings)
    all_findings.sort(key=lambda f: f.paragraph_index)

    # 计算通过规则数
    hit_rule_ids = {f.rule_id for f in all_findings if not f.rule_id.startswith("__")}
    passed_rules = (len(PHASE1_RULES) + 6) - len(hit_rule_ids)

    return ReviewResult(
        findings=all_findings,
        total_rules=len(PHASE1_RULES) + 6,
        passed_rules=max(0, passed_rules),
        filename=filename,
    )


async def review_phase2(
    paragraphs: list[str],
    rules_text: str,
    filename: str,
) -> ReviewResult:
    """第二阶段审核：深度内容（content-out-of-scope/content-duplicate/content-outdated）。

    Returns:
        ReviewResult（含 phase2 语义 findings）
    """
    if not paragraphs:
        return ReviewResult(findings=[], total_rules=len(PHASE2_RULES), passed_rules=len(PHASE2_RULES), filename=filename)

    semantic_findings: list[Finding] = []
    llm_errors: list[str] = []

    async def _call_phase2_once(attempt: int) -> tuple[list[Finding], str, str | None]:
        """单次 LLM 调用（异步执行）。"""
        try:
            client, model_name = _get_anthropic_client()
            prompt = _build_phase_prompt(rules_text, paragraphs, filename, phase=2)
            message = await asyncio.to_thread(
                client.messages.create,
                model=model_name,
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
                timeout=180.0,
            )
            text_parts = []
            for block in message.content:
                if hasattr(block, "text") and block.text:
                    text_parts.append(block.text)
            output = "\n".join(text_parts)
            findings_part, reasoning = _parse_llm_output(output, paragraphs, PHASE2_RULES)
            reason_preview = reasoning[:40] if reasoning else "(无)"
            print(f"  phase2 第 {attempt+1} 次: {len(findings_part)} 条, reasoning: {reason_preview}...", flush=True)
            return findings_part, reasoning, None
        except Exception as exc:
            print(f"  phase2 第 {attempt+1} 次失败: {exc}", flush=True)
            return [], "", str(exc)

    results = await asyncio.gather(
        _call_phase2_once(0),
        _call_phase2_once(1),
    )
    for findings_part, reasoning, err in results:
        if err:
            llm_errors.append(err)
        semantic_findings.extend(findings_part)

    # ===== 代码化预检测（确定性高）=====
    from .section_entities import (
        REGULATORY_ENTITIES, PARTY_GOV_ENTITIES, BANKING_ENTITIES,
    )
    code_findings = check_section_mismatch(paragraphs)
    semantic_findings.extend(code_findings)
    # =====================================

    # 全部失败
    if not semantic_findings and llm_errors:
        return ReviewResult(
            findings=[Finding(
                rule_id="__llm_error__",
                paragraph_index=0,
                line_number=1,
                original_text="(LLM 调用失败)",
                description=f"LLM phase2 调用失败:{'; '.join(llm_errors)}",
            )],
            total_rules=len(PHASE2_RULES),
            passed_rules=0,
            filename=filename,
        )

    # 去重
    merged: dict[tuple[str, int], Finding] = {}
    for f in semantic_findings:
        key = (f.rule_id, f.paragraph_index)
        if key not in merged or len(f.description) > len(merged[key].description):
            merged[key] = f
    semantic_findings = list(merged.values())
    semantic_findings.sort(key=lambda f: f.paragraph_index)

    # 计算通过规则数
    hit_rule_ids = {f.rule_id for f in semantic_findings if not f.rule_id.startswith("__")}
    passed_rules = len(PHASE2_RULES) - len(hit_rule_ids)

    return ReviewResult(
        findings=semantic_findings,
        total_rules=len(PHASE2_RULES),
        passed_rules=max(0, passed_rules),
        filename=filename,
    )


def review_text(
    paragraphs: list[str],
    rules_text: str,
    filename: str,
    total_rules: int = 13,
    passed_rules_hint: int | None = None,
) -> ReviewResult:
    """对段落列表做审核.

    两层架构:
    1. 格式类规则 → format_checker.py 正则检测(稳定)
    2. 语义类规则 → LLM CoT 3次调用取并集

    Args:
        paragraphs: 文档段落列表
        rules_text: 规则库文本(给 LLM 看)
        filename: 文件名(用于显示)
        total_rules: 规则总数(显示用),默认13
        passed_rules_hint: 强制指定的"通过规则数"(测试用)
    """
    if not paragraphs:
        return ReviewResult(
            findings=[],
            total_rules=total_rules,
            passed_rules=total_rules,
            filename=filename,
        )

    # ========== 1. 格式类规则:代码检测 ==========
    format_findings = check_all_format_rules(paragraphs)
    print(f"  格式类检测: {len(format_findings)} 条", flush=True)

    # ========== 2. 语义类规则:LLM 3次调用取并集 ==========
    semantic_findings: list[Finding] = []
    llm_errors: list[str] = []

    for attempt in range(3):
        try:
            client, model_name = _get_anthropic_client()
            prompt = _build_prompt(rules_text, paragraphs, filename)
            message = client.messages.create(
                model=model_name,
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
                timeout=180.0,
            )
            text_parts = []
            for block in message.content:
                if hasattr(block, "text") and block.text:
                    text_parts.append(block.text)
            output = "\n".join(text_parts)

            findings, reasoning = _parse_llm_output(output, paragraphs)
            semantic_findings.extend(findings)
            reason_preview = reasoning[:40] if reasoning else "(无)"
            print(f"  第 {attempt+1} 次调用: {len(findings)} 条, reasoning: {reason_preview}...", flush=True)
        except Exception as exc:
            llm_errors.append(str(exc))
            print(f"  第 {attempt+1} 次调用失败: {exc}", flush=True)

    # 如果 3 次全部失败
    if not semantic_findings and llm_errors:
        return ReviewResult(
            findings=[Finding(
                rule_id="__llm_error__",
                paragraph_index=0,
                line_number=1,
                original_text="(LLM 调用失败)",
                description=f"LLM 调用失败:{'; '.join(llm_errors)}",
            )],
            total_rules=total_rules,
            passed_rules=0,
            filename=filename,
        )

    # ========== 3. 合并:去重
    # 同 (rule_id, paragraph_index) 只保留一条,优先保留 description 最长的(信息最丰富)
    merged: dict[tuple[str, int], Finding] = {}
    for f in semantic_findings:
        key = (f.rule_id, f.paragraph_index)
        if key not in merged or len(f.description) > len(merged[key].description):
            merged[key] = f

    semantic_findings = list(merged.values())

    # ========== 4. 合并格式类 + 语义类 ==========
    all_findings = list(semantic_findings)
    all_findings.extend(format_findings)
    all_findings.sort(key=lambda f: f.paragraph_index)

    # ========== 5. 计算通过规则数 ==========
    hit_rule_ids = {f.rule_id for f in all_findings if not f.rule_id.startswith("__")}
    passed_rules = total_rules - len(hit_rule_ids)
    if passed_rules_hint is not None:
        passed_rules = passed_rules_hint

    return ReviewResult(
        findings=all_findings,
        total_rules=total_rules,
        passed_rules=max(0, passed_rules),
        filename=filename,
    )


def _compute_line_number(paragraphs: list[str], paragraph_index: int) -> int:
    """估算段落对应的行号(从1开始).保留以兼容旧调用."""
    return paragraph_index + 1


def check_section_mismatch(paragraphs: list[str]) -> list["Finding"]:
    """检测内容放错板块（content-wrong-section）。

    识别流程：
    1. 定位每个新闻段落所属板块（往前找最近的板块分类标题）
    2. 从标题+正文提取内容主体
    3. 关键词匹配判断期望板块
    4. 期望板块与实际板块不一致 → 报错
    """
    from .reviewer import Finding
    from .section_entities import (
        REGULATORY_ENTITIES, PARTY_GOV_ENTITIES, BANKING_ENTITIES,
    )

    SECTION_KEYWORDS = {
        "党政要闻": "党政要闻",
        "监管动态": "监管动态",
        "同业动向": "同业动向",
        "市场观察": "市场观察",
        "前沿观点": "前沿观点",
    }

    findings = []
    current_section = None

    for idx, para in enumerate(paragraphs):
        stripped = para.strip()

        is_section_title = False
        for kw in SECTION_KEYWORDS:
            if stripped == kw:
                current_section = kw
                is_section_title = True
                break

        if is_section_title:
            continue
        if current_section is None:
            continue
        if current_section == "前沿观点":
            continue

        text_to_check = stripped[:180]

        matched_entity = None
        expected_section = None

        for kw in REGULATORY_ENTITIES:
            if kw in text_to_check:
                matched_entity = kw
                expected_section = "监管动态"
                break
        if not matched_entity:
            for kw in PARTY_GOV_ENTITIES:
                if kw in text_to_check:
                    matched_entity = kw
                    expected_section = "党政要闻"
                    break
        if not matched_entity:
            for kw in BANKING_ENTITIES:
                if kw in text_to_check:
                    matched_entity = kw
                    expected_section = "同业动向"
                    break

        if not matched_entity:
            continue

        if current_section != expected_section:
            findings.append(Finding(
                rule_id="content-wrong-section",
                paragraph_index=idx,
                line_number=idx + 1,
                original_text=para,
                description=f"内容主体'{matched_entity}'应归入{expected_section}，却放在了{current_section}",
            ))

    return findings