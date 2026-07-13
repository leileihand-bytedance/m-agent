# Review General Rules Fixes Implementation Plan

> 状态：已实施；后续又增加了长文复核、通篇逻辑检查和精确标注。本文件保留为阶段性计划。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复通用审核的“标题后无正文”误报，补上文字审核留痕，并新增附件编号/名称不一致的确定性规则。

**Architecture:** 继续沿用 `app/review/` 现有结构，不改主路由。误报修复和新规则都放进 `general_rule_checker.py`，文字审核留痕复用 `save_review` 存档能力，但为文字消息补一个轻量文本源文件。

**Tech Stack:** Python、pytest、现有 `app/review` 审核模块

---

### Task 1: 为规则修复写失败测试

**Files:**
- Modify: `tests/test_review_general_rules.py`
- Test: `tests/test_review_general_rules.py`

- [ ] **Step 1: 写“单行键值内容不应报标题后无正文”的失败测试**

```python
def test_check_general_document_rules_does_not_treat_key_value_line_as_empty_heading():
    paragraphs = [
        "1.会议时间：2029年7月15日",
        "2.会议地点：协会会议室",
    ]

    findings = check_general_document_rules(paragraphs)

    assert not any(f.rule_id == "general-heading-empty" for f in findings)
```

- [ ] **Step 2: 写“2.1/2.2 编号标题不应误报空标题”的失败测试**

```python
def test_check_general_document_rules_does_not_treat_decimal_heading_as_empty_when_inline_value_exists():
    paragraphs = [
        "2.1理事候选单位条件：符合协会章程要求",
        "2.2监事候选单位条件：具备独立监督能力",
    ]

    findings = check_general_document_rules(paragraphs)

    assert not any(f.rule_id == "general-heading-empty" for f in findings)
```

- [ ] **Step 3: 写“附件编号/名称不一致”的失败测试**

```python
def test_check_general_document_rules_finds_attachment_name_number_conflict():
    paragraphs = [
        "按照《福建XXX》办理，详见附件7：《广东XXX》。",
    ]

    findings = check_general_document_rules(paragraphs)

    assert any(f.rule_id == "general-attachment-name-mismatch" for f in findings)
```

- [ ] **Step 4: 运行规则测试并确认失败**

Run: `pytest tests/test_review_general_rules.py -q`
Expected: 新增测试失败，旧测试继续通过

### Task 2: 为文字审核留痕写失败测试

**Files:**
- Modify: `tests/test_review_bot.py`
- Test: `tests/test_review_bot.py`

- [ ] **Step 1: 写“文字审核也会保存 report/meta/source”的失败测试**

```python
def test_save_review_supports_text_message_archive(tmp_path: Path):
    result = ReviewResult(findings=[], total_rules=10, passed_rules=10, filename="文字消息")

    review_dir = save_review(
        reviews_dir=tmp_path / "reviews",
        file_bytes=None,
        original_filename="文字消息.txt",
        sender="u1",
        msgid="m1",
        result=result,
        parsed_paragraphs=["第一段文字。"],
        text_content="第一段文字。",
        doc_type=DocumentType.GENERAL,
    )

    assert (review_dir / "source" / "文字消息.txt").exists()
```

- [ ] **Step 2: 运行 Bot 测试并确认失败**

Run: `pytest tests/test_review_bot.py -q`
Expected: 新增存档测试失败，其余测试保持现状

### Task 3: 实现最小修复

**Files:**
- Modify: `app/review/general_rule_checker.py`
- Modify: `app/review/output_formatter.py`
- Modify: `app/review/main.py`

- [ ] **Step 1: 收窄“标题后无正文”判定**

实现方向：
- 单行里如果已经出现 `：` 且冒号后有有效内容，不再判定为空标题
- `2.1`、`2.2` 这类小数编号单独识别，避免被 `2.` 误判

- [ ] **Step 2: 新增附件编号/名称不一致规则**

实现方向：
- 只处理“附件N：名称”和正文中“附件N/附件名称”这类高确定性场景
- 当同一编号对应的名称不一致时，返回 `general-attachment-name-mismatch`

- [ ] **Step 3: 让文字审核也进入统一存档**

实现方向：
- `save_review` 支持 `file_bytes=None` + `text_content`
- 文字审核完成后保存 `report.md`、`meta.md`、`source/文字消息.txt`

- [ ] **Step 4: 为新规则补中文标签**

实现方向：
- 在 `output_formatter.py` 中增加 `general-attachment-name-mismatch`

### Task 4: 验证并更新文档

**Files:**
- Modify: `app/review/README.md`

- [ ] **Step 1: 运行相关测试**

Run: `pytest tests/test_review_general_rules.py tests/test_review_bot.py -q`
Expected: 全部通过

- [ ] **Step 2: 更新审核文档**

更新内容：
- 通用审核新增附件编号/名称一致性规则
- 文字审核现在也会生成存档，便于事后追溯

- [ ] **Step 3: 自检**

确认：
- 未改动内参/半月报主流程
- 未放大“时间逻辑”规则范围
- 新规则只覆盖高确定性场景
