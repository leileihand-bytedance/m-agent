# 板块归位检测器（content-wrong-section）代码化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建 `section_entities.py` 板块主体关键词库 + `check_section_mismatch()` 检测函数，集成到 `review_phase2` 中，对 `content-wrong-section` 实施"代码关键词匹配 + LLM 兜底"的 Hybrid 检测。

**Architecture:** 新建 `app/review/section_entities.py` 存放三个 frozenset 关键词集合（REGULATORY_ENTITIES / PARTY_GOV_ENTITIES / BANKING_ENTITIES）；新建 `check_section_mismatch()` 函数实现代码化板块检测；修改 `review_phase2()` 在 LLM 调用前先用代码预检测，两路结果合并去重。

**Tech Stack:** Python 3.11, regex, frozenset, 不引入新依赖。

---

## 文件结构

```
app/review/section_entities.py   # 新建：板块主体关键词库
app/review/reviewer.py           # 修改：review_phase2 集成代码检测
tests/test_reviewer.py           # 修改：新增测试用例
```

---

## Task 1: 新建 section_entities.py — 板块主体关键词库

**Files:**
- Create: `app/review/section_entities.py`

- [ ] **Step 1: 创建文件，写入关键词库**

```python
"""板块主体关键词库.

用于 content-wrong-section 的代码化检测。
一行一局一会 → 监管动态
党和国家领导人 + 国务院各部委 → 党政要闻
民营/数字银行 → 同业动向
"""

from __future__ import annotations

# 一行一局一会 → 监管动态
REGULATORY_ENTITIES: frozenset[str] = frozenset([
    "中国人民银行", "人民银行", "央行", "PBOC",
    "国家金融监督管理总局", "金融监管总局",
    "中国证券监督管理委员会", "证监会", "CSRC",
    "国家外汇管理局", "外汇管理局", "外汇局",
])

# 党和国家领导人 + 国务院各部委 → 党政要闻
PARTY_GOV_ENTITIES: frozenset[str] = frozenset([
    # 党和国家领导人
    "习近平", "李强", "丁薛祥", "何立峰", "张国清",
    # 国务院
    "国务院", "国务院党组", "国务院常务会议", "国务院办公厅",
    # 国务院组成部门
    "商务部", "外交部", "国防部", "公安部", "民政部", "司法部",
    "财政部", "人力资源社会保障部",
    "自然资源部", "生态环境部", "住房城乡建设部", "交通运输部",
    "水利部", "农业农村部", "文化和旅游部", "国家卫生健康委",
    "应急管理部", "审计署",
    "国家发改委", "国家能源局", "工信部", "科学技术部",
    "教育部", "科技部", "国家广电总局", "体育总局", "统计局",
    "国家市场监管总局",
    "中央纪委国家监委", "中央纪委", "中纪委",
])

# 民营/数字银行 → 同业动向
BANKING_ENTITIES: frozenset[str] = frozenset([
    "微众银行", "网商银行", "富民银行", "金城银行",
    "蓝海银行", "振兴银行", "民营银行", "数字银行",
])
```

- [ ] **Step 2: 验证语法**

Run: `python3.11 -c "from app.review.section_entities import REGULATORY_ENTITIES, PARTY_GOV_ENTITIES, BANKING_ENTITIES; print('OK', len(REGULATORY_ENTITIES), len(PARTY_GOV_ENTITIES), len(BANKING_ENTITIES))"`
Expected: `OK 8 39 9`

- [ ] **Step 3: Commit**

```bash
git add app/review/section_entities.py
git commit -m "feat: 新建section_entities.py板块主体关键词库"
```

---

## Task 2: 新建 check_section_mismatch() — 代码化板块归位检测

**Files:**
- Create: `tests/test_section_classifier.py`
- Modify: `app/review/reviewer.py`（在末尾添加函数，不改现有代码结构）

- [ ] **Step 1: 写测试**

```python
"""板块归位检测器测试."""
from app.review.section_entities import (
    REGULATORY_ENTITIES, PARTY_GOV_ENTITIES, BANKING_ENTITIES
)
from app.review.reviewer import check_section_mismatch


def test_regulatory_in_wrong_section():
    """金融监管总局会议放在党政要闻 → 应报错."""
    paragraphs = [
        "党政要闻",
        "金融监管总局部署2026年从严治党工作",  # 标题
        "1月15日，国家金融监管总局召开2026年监管工作会议...",  # 正文
    ]
    findings = check_section_mismatch(paragraphs)
    rule_ids = [f.rule_id for f in findings]
    assert "content-wrong-section" in rule_ids, f"应为content-wrong-section，实际{rule_ids}"


def test_state_council_in_regulatory():
    """国务院会议放在监管动态 → 应报错."""
    paragraphs = [
        "监管动态",
        "国务院总理李强主持召开国务院常务会议",  # 标题
        "1月14日，国务院总理李强主持召开国务院党组会议...",  # 正文
    ]
    findings = check_section_mismatch(paragraphs)
    rule_ids = [f.rule_id for f in findings]
    assert "content-wrong-section" in rule_ids, f"应为content-wrong-section，实际{rule_ids}"


def test_regulatory_in_correct_section():
    """金融监管总局会议放在监管动态 → 不报错."""
    paragraphs = [
        "监管动态",
        "金融监管总局部署2026年从严治党工作",
        "1月15日，国家金融监管总局召开2026年监管工作会议...",
    ]
    findings = check_section_mismatch(paragraphs)
    wrong_section = [f for f in findings if f.rule_id == "content-wrong-section"]
    assert len(wrong_section) == 0, f"不应报错，实际报了{len(wrong_section)}条"


def test_no_match_no_error():
    """无关键词匹配（市场观察兜底）→ 不报错."""
    paragraphs = [
        "监管动态",
        "本周A股主要指数集体走强",
        "本周A股主要指数集体走强，延续跨年强势行情...",
    ]
    findings = check_section_mismatch(paragraphs)
    wrong_section = [f for f in findings if f.rule_id == "content-wrong-section"]
    assert len(wrong_section) == 0


def test_pbc_in_regulatory():
    """人民银行会议放在监管动态 → 不报错."""
    paragraphs = [
        "监管动态",
        "人民银行公布2025年金融统计数据报告",
        "近日，中国人民银行官网公布2025年金融统计数据报告...",
    ]
    findings = check_section_mismatch(paragraphs)
    wrong_section = [f for f in findings if f.rule_id == "content-wrong-section"]
    assert len(wrong_section) == 0


def test_csrc_in_regulatory():
    """证监会会议放在监管动态 → 不报错."""
    paragraphs = [
        "监管动态",
        "证监会发布科创板股票做市交易规则",
        "证监会近日发布《科创板股票做市交易规则》...",
    ]
    findings = check_section_mismatch(paragraphs)
    wrong_section = [f for f in findings if f.rule_id == "content-wrong-section"]
    assert len(wrong_section) == 0
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3.11 -m pytest tests/test_section_classifier.py -v`
Expected: `ERROR — function not defined`

- [ ] **Step 3: 实现 check_section_mismatch()**

在 `reviewer.py` 末尾添加：

```python
def check_section_mismatch(paragraphs: list[str]) -> list["Finding"]:
    """检测内容放错板块（content-wrong-section）。

    识别流程：
    1. 定位每个新闻段落所属板块（往前找最近的板块分类标题）
    2. 从标题+正文提取内容主体
    3. 关键词匹配判断期望板块
    4. 期望板块与实际板块不一致 → 报错
    """
    from .reviewer import Finding

    # 板块分类标题关键词
    SECTION_KEYWORDS = {
        "党政要闻": "党政要闻",
        "监管动态": "监管动态",
        "同业动向": "同业动向",
        "市场观察": "市场观察",
        "前沿观点": "前沿观点",
    }

    findings = []
    # 当前所属板块
    current_section = None

    for idx, para in enumerate(paragraphs):
        stripped = para.strip()

        # 识别板块分类标题
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
        # 前沿观点 → 不检测
        if current_section == "前沿观点":
            continue

        # 跳过纯正文段落的板块分类（用于判断当前新闻所属板块）
        # 提取识别文本：标题段+后续正文段
        text_to_check = stripped[:180]

        # 优先级：REGULATORY > PARTY_GOV > BANKING
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

        # 未匹配任何已知主体 → 兜底，不报错
        if not matched_entity:
            continue

        # 判断是否放错板块
        if current_section != expected_section:
            findings.append(Finding(
                rule_id="content-wrong-section",
                paragraph_index=idx,
                line_number=idx + 1,
                original_text=para,
                description=f"内容主体'{matched_entity}'应归入{expected_section}，却放在了{current_section}",
            ))

    return findings
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python3.11 -m pytest tests/test_section_classifier.py -v`
Expected: `PASS`（6个测试全部通过）

- [ ] **Step 5: Commit**

```bash
git add tests/test_section_classifier.py app/review/reviewer.py
git commit -m "feat: 新增check_section_mismatch代码化板块归位检测"
```

---

## Task 3: 集成到 review_phase2

**Files:**
- Modify: `app/review/reviewer.py`

- [ ] **Step 1: 确认 check_section_mismatch 已导入**

检查 reviewer.py 顶部是否有 `from .section_entities import ...`，没有则添加。

- [ ] **Step 2: 修改 review_phase2，在 LLM 调用前插入代码检测**

找到 review_phase2 函数中 `results = await asyncio.gather(...)` 之后的代码，在 `semantic_findings.extend(findings_part)` 之前插入：

```python
    # ===== 代码化预检测（确定性高）=====
    from .section_entities import (
        REGULATORY_ENTITIES, PARTY_GOV_ENTITIES, BANKING_ENTITIES,
    )
    code_findings = check_section_mismatch(paragraphs)
    semantic_findings.extend(code_findings)
    # =====================================
```

- [ ] **Step 3: 验证语法**

Run: `python3.11 -c "from app.review.reviewer import review_phase1, review_phase2; print('OK')"`
Expected: `OK`

- [ ] **Step 4: 用真实文档测试**

Run:
```bash
python3.11 -c "
from app.review.parser import parse_docx
from app.review.reviewer import review_phase2
from pathlib import Path

docx_path = Path('data/reviews/20260625-001/source/微众银行信息内参周报2026年第3期.docx')
parsed = parse_docx(docx_path)
with open('app/data/rules.md') as f:
    rules_text = f.read()

import asyncio
result = asyncio.run(review_phase2(parsed.paragraphs, rules_text, 'test.docx'))
for f in result.findings:
    if f.rule_id == 'content-wrong-section':
        print(f'段{f.paragraph_index}: {f.description}')
"
```
Expected: 输出 1 条 content-wrong-section（段37 国务院会议在监管动态）

- [ ] **Step 5: Commit**

```bash
git add app/review/reviewer.py
git commit -m "feat: review_phase2集成check_section_mismatch代码化预检测"
```

---

## Task 4: 同步更新 rules.md

**Files:**
- Modify: `app/data/rules.md`

- [ ] **Step 1: 在 content-wrong-section 规则描述中加一行说明**

在"严重程度:error"后加：
> **检测方式**：代码关键词匹配（确定性高）+ LLM 兜底。关键词库见 `app/review/section_entities.py`。

- [ ] **Step 2: Commit**

```bash
git add app/data/rules.md
git commit -m "docs: rules.md标注content-wrong-section检测方式"
```

---

## 验证总览

1. `python3.11 -m pytest tests/test_section_classifier.py -v` — 6个测试全 PASS
2. 用 20260625-001（微众银行信息内参周报2026年第3期）验证：段37 国务院会议在监管动态被检出
3. Bot 重启后发送新文档测试，企业微信收到包含 content-wrong-section 的 Phase 2 结果

---

## 风险

- 关键词表可能不全（如新机构简称），需后续补充
- LLM 和代码同时检测同一段落 → 去重逻辑按 (rule_id, paragraph_index) 合并，description 保留更长的
