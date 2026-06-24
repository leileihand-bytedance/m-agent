# 审核两阶段拆分实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将审核拆分为两阶段——第一阶段发格式+基础内容结果，第二阶段追加内容质量结果。

**Architecture:** reviewer.py 拆分为 phase1/phase2 两个独立函数，各配独立 prompt；main.py 分两次发送；output_formatter.py 支持两套格式。

**Tech Stack:** Python asyncio, wecom-aibot-sdk, anthropic API

---

## 文件结构

```
app/review/
  reviewer.py      # 修改：拆分为 review_phase1 / review_phase2
  output_formatter.py  # 修改：新增 phase1/phase2 专用格式化函数
  main.py          # 修改：两阶段发送逻辑
  __init__.py      # 修改：导出新函数
tests/
  test_reviewer.py  # 修改：测试两阶段函数
```

---

## 任务清单

### Task 1: 定义阶段常量 & 拆分 reviewer.py

**Files:**
- Modify: `app/review/reviewer.py:1-75`

- [ ] **Step 1: 添加阶段常量**

在 `reviewer.py` 顶部（在 `SEMANTIC_RULE_IDS` 之后）添加：

```python
# 第一阶段规则（格式正则 + 基础内容LLM）
PHASE1_RULES = (
    "title-truncated",
    "content-mismatch",
    "content-incomplete",
)

# 第二阶段规则（深度内容LLM）
PHASE2_RULES = (
    "toc-mismatch",
    "content-out-of-scope",
    "content-wrong-section",
    "content-duplicate",
    "content-outdated",
)

# 阶段提示语（发给用户看）
PHASE1_PROMPT_SUFFIX = ""
PHASE2_PROMPT_SUFFIX = ""
```

- [ ] **Step 2: 新增 `_build_phase_prompt` 函数**

在 `_build_prompt` 函数之后新增：

```python
def _build_phase_prompt(
    rules_text: str,
    paragraphs: list[str],
    filename: str,
    phase: int,
) -> str:
    """构造指定阶段的 LLM prompt。

    phase=1: 基础内容（标题截断/错配/完整性）
    phase=2: 深度内容（目录错配/超范围/放错/重复/过时）
    """
    paras_text = "\n\n".join(
        f"[段 {i+1}]\n{p}" for i, p in enumerate(paragraphs)
    )

    if phase == 1:
        rule_section = """## 第一阶段：基础内容审核（标题 + 正文）

对正文区的每个段落，判断其类型（章节分类/新闻标题/正文/原文引用），然后执行：

**仅标题段：**
- title-truncated: 标题说的 ≠ 正文说的同一件事 → 标题截断
- content-mismatch: 标题和正文完全不同 → 错配

**仅正文段：**
- content-incomplete: 段末语义截断，缺宾语或结束语
"""
    else:
        rule_section = """## 第二阶段：深度内容审核（目录 + 质量）

**目录区域：**
- toc-mismatch: 目录与正文在章节名/标题/顺序上对不上

**内容质量：**
- content-out-of-scope: 跟银行经营/宏观经济完全无关
- content-wrong-section: 内容明显属于某板块但放到了别处
- content-duplicate: 同一件事出现两次以上
- content-outdated: 信息明显早于周报时间范围
"""

    prompt = f"""你是一位严谨的中文文档审核员。

# 审核规则清单

{rules_text}

# 待审文档

文件名:{filename}

{paras_text}

# 你的任务

{rule_section}

## 输出 JSON

**严格按以下格式输出，只输出 JSON，不要任何其他文字:**

```json
{{
  "reasoning": "简要分析思路（100字以内）",
  "issues": [
    {{"paragraph_index": 0, "rule_id": "xxx", "original_text": "该段完整原文", "description": "问题描述（50字以内）"}}
  ]
}}
```

**关键规则:**
- paragraph_index 从 0 开始
- rule_id 必须是阶段对应的规则之一
- original_text 必须是该段的完整原文，不要截断
- 不确定的问题不要写，宁可漏报不要误报
- 文档完全没问题 → {{"issues": []}}
"""
    return prompt
```

- [ ] **Step 3: 新增 `review_phase1` 函数**

在 `review_text` 函数之前新增：

```python
def review_phase1(
    paragraphs: list[str],
    rules_text: str,
    filename: str,
) -> ReviewResult:
    """第一阶段审核：格式正则 + 基础内容（标题/错配/完整性）。

    Returns:
        包含 format_findings 和 phase1 semantic findings 的合并结果
    """
    if not paragraphs:
        return ReviewResult(findings=[], total_rules=len(PHASE1_RULES), passed_rules=len(PHASE1_RULES), filename=filename)

    # 格式类规则（正则，秒级）
    format_findings = check_all_format_rules(paragraphs)

    # 基础内容 LLM
    semantic_findings: list[Finding] = []
    llm_errors: list[str] = []

    for attempt in range(3):
        try:
            client, model_name = _get_anthropic_client()
            prompt = _build_phase_prompt(rules_text, paragraphs, filename, phase=1)
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

            findings, reasoning = _parse_llm_output(output, paragraphs, PHASE1_RULES)
            semantic_findings.extend(findings)
            print(f"  Phase1 第 {attempt+1} 次: {len(findings)} 条, reasoning: {reasoning[:40]}...", flush=True)
        except Exception as exc:
            llm_errors.append(str(exc))
            print(f"  Phase1 第 {attempt+1} 次失败: {exc}", flush=True)

    if not semantic_findings and llm_errors:
        return ReviewResult(
            findings=[Finding(
                rule_id="__llm_error__",
                paragraph_index=0,
                line_number=1,
                original_text="(LLM 调用失败)",
                description=f"Phase1 LLM 失败:{' '.join(llm_errors)}",
            )],
            total_rules=len(PHASE1_RULES) + 5,  # 5条格式规则
            passed_rules=0,
            filename=filename,
        )

    # 合并去重
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
    passed_rules = (len(PHASE1_RULES) + 5) - len(hit_rule_ids)

    return ReviewResult(
        findings=all_findings,
        total_rules=len(PHASE1_RULES) + 5,
        passed_rules=max(0, passed_rules),
        filename=filename,
    )
```

- [ ] **Step 4: 新增 `review_phase2` 函数**

在 `review_phase1` 之后新增：

```python
def review_phase2(
    paragraphs: list[str],
    rules_text: str,
    filename: str,
) -> ReviewResult:
    """第二阶段审核：深度内容（目录/质量）。

    Returns:
        仅含 phase2 semantic findings 的结果
    """
    if not paragraphs:
        return ReviewResult(findings=[], total_rules=len(PHASE2_RULES), passed_rules=len(PHASE2_RULES), filename=filename)

    semantic_findings: list[Finding] = []
    llm_errors: list[str] = []

    for attempt in range(3):
        try:
            client, model_name = _get_anthropic_client()
            prompt = _build_phase_prompt(rules_text, paragraphs, filename, phase=2)
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

            findings, reasoning = _parse_llm_output(output, paragraphs, PHASE2_RULES)
            semantic_findings.extend(findings)
            print(f"  Phase2 第 {attempt+1} 次: {len(findings)} 条, reasoning: {reasoning[:40]}...", flush=True)
        except Exception as exc:
            llm_errors.append(str(exc))
            print(f"  Phase2 第 {attempt+1} 次失败: {exc}", flush=True)

    if not semantic_findings and llm_errors:
        return ReviewResult(
            findings=[Finding(
                rule_id="__llm_error__",
                paragraph_index=0,
                line_number=1,
                original_text="(LLM 调用失败)",
                description=f"Phase2 LLM 失败:{' '.join(llm_errors)}",
            )],
            total_rules=len(PHASE2_RULES),
            passed_rules=0,
            filename=filename,
        )

    # 合并去重
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
```

- [ ] **Step 5: 修改 `_parse_llm_output` 签名**

修改 `_parse_llm_output` 函数签名（第二个参数之后），新增 `allowed_rules` 参数：

```python
def _parse_llm_output(
    output: str,
    paragraphs: list[str],
    allowed_rules: tuple[str, ...],
) -> tuple[list[Finding], str]:
```

并在函数内部将原来的 `SEMANTIC_RULE_IDS` 检查改为 `allowed_rules`。

- [ ] **Step 6: Commit**

```bash
git add app/review/reviewer.py
git commit -m "feat(review): 拆分为 phase1/phase2 两阶段审核函数"
```

---

### Task 2: 修改 output_formatter.py 新增两阶段格式化

**Files:**
- Modify: `app/review/output_formatter.py`

- [ ] **Step 1: 新增 `format_phase1_result` 函数**

在 `format_review_result` 之后新增：

```python
def format_phase1_result(result: ReviewResult, max_findings: int = 20) -> str:
    """格式化第一阶段审核结果（发给用户的第一条消息）。

    格式：
    第一阶段审核完成（低级错误）

    格式检查 + 基础内容审核，共 N 条：
    错误1:【规则标签】问题描述
    所属段落：原文...
    ...

    第二阶段审核中，请稍候...
    """
    findings = result.findings
    total = len(findings)
    sorted_findings = sorted(findings, key=lambda f: f.paragraph_index)
    display = sorted_findings[:max_findings]

    lines = ["第一阶段审核完成（低级错误）", ""]
    lines.append(f"格式检查 + 基础内容审核，共 {total} 条：")

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


def format_phase2_result(result: ReviewResult, review_dir: str | None = None, max_findings: int = 20) -> str:
    """格式化第二阶段审核结果（追加发给用户的第二条消息）。

    格式：
    第二阶段审核完成（内容质量）

    深度内容审核，共 N 条：
    错误1:【规则标签】问题描述
    所属段落：原文...
    ...

    点击查看完整存档：data/reviews/<date-seq>
    """
    findings = result.findings
    total = len(findings)
    sorted_findings = sorted(findings, key=lambda f: f.paragraph_index)
    display = sorted_findings[:max_findings]

    lines = ["第二阶段审核完成（内容质量）", ""]
    lines.append(f"深度内容审核，共 {total} 条：")

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

    if review_dir:
        lines.append("")
        lines.append(f"点击查看完整存档：{review_dir}/report.md")

    return "\n".join(lines)
```

- [ ] **Step 2: Commit**

```bash
git add app/review/output_formatter.py
git commit -m "feat(output): 新增 phase1/phase2 专用格式化函数"
```

---

### Task 3: 修改 main.py 两阶段发送逻辑

**Files:**
- Modify: `app/review/main.py`

- [ ] **Step 1: 修改 on_file 中的审核逻辑**

找到 `on_file` 异步函数中"审核"部分（约第 393 行），将其改为两阶段：

将原来的：
```python
# 6. 审核(LLM 处理 65 段文档可能需要 1-2 分钟)
review_result = review_text(parsed.paragraphs, rules_text, filename)
```

替换为两阶段调用：

```python
# 6. 第一阶段审核（格式正则 + 基础内容 LLM）
from app.review.reviewer import review_phase1, review_phase2  # noqa: E402
from app.review.output_formatter import format_phase1_result, format_phase2_result  # noqa: E402

phase1_result = review_phase1(parsed.paragraphs, rules_text, filename)

# 立即发第一阶段结果
phase1_reply = format_phase1_result(phase1_result)
done_id_1 = generate_req_id("review-p1")
try:
    await asyncio.wait_for(
        ws_client.reply_stream(frame, done_id_1, phase1_reply, True),
        timeout=30.0,
    )
    print(f"✅ 第一阶段结果已发送", flush=True)
except asyncio.TimeoutError:
    print(f"⚠️ 第一阶段发送超时", flush=True)
except Exception as exc:
    print(f"⚠️ 第一阶段发送失败:{exc}", flush=True)

# 7. 第二阶段审核（深度内容 LLM）
phase2_result = review_phase2(parsed.paragraphs, rules_text, filename)

# 8. 存档
msgid = str(frame.get("body", {}).get("msgid", "") or frame.get("headers", {}).get("req_id", ""))
review_dir = None
try:
    review_dir = save_review(
        reviews_dir=config.reviews_dir,
        file_bytes=buffer,
        original_filename=filename,
        sender=sender,
        msgid=msgid,
        result=phase2_result,  # 完整结果存档
        parsed_paragraphs=parsed.paragraphs,
    )
except Exception as exc:
    print(f"⚠️ 存档失败:{exc}", flush=True)

# 9. 追加发送第二阶段结果
phase2_reply = format_phase2_result(phase2_result, str(review_dir) if review_dir else None)
done_id_2 = generate_req_id("review-p2")
try:
    await asyncio.wait_for(
        ws_client.reply_stream(frame, done_id_2, phase2_reply, True),
        timeout=30.0,
    )
    print(f"✅ 第二阶段结果已发送", flush=True)
except asyncio.TimeoutError:
    print(f"⚠️ 第二阶段发送超时", flush=True)
except Exception as exc:
    print(f"⚠️ 第二阶段发送失败:{exc}", flush=True)

return  # 后续旧代码不要了
```

- [ ] **Step 2: 修改 import**

将原来的：
```python
from app.review import load_rules, review_text, format_review_result  # noqa: E402
from app.review.reviewer import ReviewResult  # noqa: E402
```

替换为：
```python
from app.review import load_rules, format_review_result  # noqa: E402
from app.review.reviewer import ReviewResult  # noqa: E402
```

- [ ] **Step 3: Commit**

```bash
git add app/review/main.py
git commit -m "feat(main): 两阶段审核流程，第一阶段完成后立即发送"
```

---

### Task 4: 更新 tests/test_reviewer.py

**Files:**
- Modify: `tests/test_reviewer.py`

- [ ] **Step 1: 添加 phase1/phase2 单元测试**

```python
def test_phase1_rules_only():
    """Phase1 只返回格式规则和基础内容规则."""
    from app.review.reviewer import PHASE1_RULES, PHASE2_RULES
    assert "title-truncated" in PHASE1_RULES
    assert "content-mismatch" in PHASE1_RULES
    assert "content-incomplete" in PHASE1_RULES
    assert "toc-mismatch" not in PHASE1_RULES
    assert "content-out-of-scope" not in PHASE1_RULES

def test_phase2_rules_only():
    """Phase2 只返回深度内容规则."""
    from app.review.reviewer import PHASE1_RULES, PHASE2_RULES
    assert "toc-mismatch" in PHASE2_RULES
    assert "content-out-of-scope" in PHASE2_RULES
    assert "content-wrong-section" in PHASE2_RULES
    assert "content-duplicate" in PHASE2_RULES
    assert "content-outdated" in PHASE2_RULES
    assert "title-truncated" not in PHASE2_RULES
```

- [ ] **Step 2: Commit**

```bash
git add tests/test_reviewer.py
git commit -m "test: 添加 phase1/phase2 规则单元测试"
```

---

## 自检清单

- [ ] Spec 覆盖：两阶段拆分、消息格式、存档流程均有任务对应
- [ ] 无占位符：所有函数签名、代码块、命令均已填入
- [ ] 类型一致性：`ReviewResult`、`Finding`、`PHASE1_RULES`、`PHASE2_RULES` 在各任务间一致
- [ ] 流程完整性：格式正则秒级 → 第一阶段发 → 第二阶段 LLM → 第二阶段追加发
