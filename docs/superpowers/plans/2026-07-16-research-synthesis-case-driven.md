# 综合调研案例驱动改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax (`- [ ]`) for tracking.

**Goal:** 把综合调研从“机械套提纲”升级为“先判断提纲约束、再建立可追溯证据台账、最后生成可核对 Word 初稿”，并用 3 组脱敏案例固定关键行为。

**Architecture:** 保持两次模型调用。第一次模型调用同时完成提纲分类和证据台账，Pydantic schema 对来源类型与可用性做确定性约束；第二次模型调用只基于验证后的台账成稿。工作流在模型输出后继续执行标题层级、提纲覆盖、来源标签、数字溯源和图片提醒的确定性校验。

**Tech Stack:** Python 3.13.14、Pydantic v2、pytest、python-docx、项目现有 ToolGateway 与受管文档/推送脚本。

## Global Constraints

- 不读取或猜测 DOCX 内嵌图片内容，只保留“请评估是否需要”的位置提醒。
- 不引入外部资料，不把案例终稿中的不可追溯事实写回规则。
- 不保存真实案例原文、真实用户 ID、日志或本机材料路径。
- 所有功能变更先有失败测试，再写最小实现。
- 输出仍为 Word，开头和结尾保留用户补充备注。

---

### Task 1: 扩展提纲分类和证据台账 schema

**Files:**
- Modify: `tests/test_research_synthesis_workflow.py`
- Modify: `skills/research_synthesis/schema.py`

- [ ] **Step 1: 写 schema 失败测试**

新增测试，明确：

```python
def test_research_evidence_disables_unverified_kinds():
    image = ResearchEvidencePoint(
        content="图片表格可能含数据",
        source_labels=["业务部"],
        evidence_kind="image_candidate",
        usable=True,
    )
    derived_without_formula = ResearchEvidencePoint(
        content="合计约2.2万户",
        source_labels=["甲部", "乙部"],
        evidence_kind="derived",
        usable=True,
    )

    assert image.usable is False
    assert derived_without_formula.usable is False
```

另加计划模型测试，覆盖 `outline_type`、`coverage_mode`、`classification_reason`、`required_headings`、`selected_headings` 和 `omitted_outline_items`。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `uv run --locked pytest tests/test_research_synthesis_workflow.py -k "evidence_disables or plan_classification" -v`

Expected: 因字段或校验器不存在而失败。

- [ ] **Step 3: 写最小 schema 实现**

在 `schema.py` 增加 Literal 类型和模型校验：

```python
OutlineType = Literal["questionnaire", "policy_catalog", "report_skeleton", "unknown"]
CoverageMode = Literal["exhaustive", "selective"]
EvidenceKind = Literal["source_text", "derived", "image_candidate", "external_missing"]

@model_validator(mode="after")
def protect_unverified_evidence(self):
    if self.evidence_kind in {"image_candidate", "external_missing"}:
        self.usable = False
    if self.evidence_kind == "derived" and not self.derivation_note.strip():
        self.usable = False
    return self
```

`outline_type`、`coverage_mode`、`classification_reason` 设为必填，避免模型漏返回时静默套用默认分类；证据扩展字段保留安全默认值。

- [ ] **Step 4: 运行 schema 测试并确认 GREEN**

Run: `uv run --locked pytest tests/test_research_synthesis_workflow.py -k "evidence_disables or plan_classification" -v`

Expected: PASS。

### Task 2: 区分“逐项覆盖”和“选择性覆盖”

**Files:**
- Modify: `tests/test_research_synthesis_workflow.py`
- Modify: `skills/research_synthesis/workflow.py`

- [ ] **Step 1: 写两类提纲的失败测试**

新增两个脱敏场景：

1. 政策目录型提纲，`coverage_mode="selective"`，只选“延期还本”和“信用贷款”，断言不强插原始目录中未选的宏观标题。
2. 问卷型提纲，`coverage_mode="exhaustive"`，模型漏写一个问题，断言按原顺序补回标题和“材料待补充”提醒。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `uv run --locked pytest tests/test_research_synthesis_workflow.py -k "selective_policy_catalog or exhaustive_questionnaire" -v`

Expected: 选择性场景因当前工作流机械补全全部原始标题而失败。

- [ ] **Step 3: 实现计划驱动的标题覆盖**

把计划传入正文归一化：

```python
body = _normalize_draft_body(
    str(draft.get("body", "") or ""),
    outline=outline,
    sources=source_materials,
    plan=plan,
)
```

新增 `_expected_top_headings(plan, outline)`：

- `exhaustive`：以提纲原始一级问题为主，缺失项必须补回；
- `selective`：只使用 `selected_headings`，并过滤没有可用证据的小节；
- 新字段缺失时保持原有逐项覆盖行为。

- [ ] **Step 4: 运行两类提纲测试并确认 GREEN**

Run: `uv run --locked pytest tests/test_research_synthesis_workflow.py -k "selective_policy_catalog or exhaustive_questionnaire" -v`

Expected: PASS。

### Task 3: 固化证据类型、数字溯源和来源标签

**Files:**
- Modify: `tests/test_research_synthesis_workflow.py`
- Modify: `skills/research_synthesis/workflow.py`

- [ ] **Step 1: 写第三组脱敏案例失败测试**

场景同时包含：

- 两个部门原始数字；
- `derived` 证据，`derivation_note` 中有完整算式和约数；
- `image_candidate` 证据，明确不可进入正文；
- 模型正文额外写入一个台账没有的数字；
- 一段事实缺少来源标签。

断言：

```python
assert "约2.2万户" in result.body
assert "【来源待核对：该段包含材料台账未登记的数据，请人工核对。】" in result.body
assert "【来源：待核对】" in result.body
assert "【图片提醒：业务部本节素材包含1张图片，请评估是否需要】" in result.body
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `uv run --locked pytest tests/test_research_synthesis_workflow.py -k "derived_image_and_untraceable" -v`

Expected: 当前工作流不会对无台账数字和缺失来源做确定性标记，因此失败。

- [ ] **Step 3: 实现证据可用性和正文守卫**

新增小型辅助函数：

```python
def _usable_evidence_points(plan): ...
def _allowed_numeric_tokens(plan): ...
def _ensure_fact_attribution(line, allowed_labels): ...
def _mark_untraceable_numbers(line, allowed_numbers): ...
```

规则：

- `source_text` 可直接使用；
- `derived` 只有在有来源标签和明确推导说明时可用；
- `image_candidate`、`external_missing` 永远不可作为正文事实；
- 正文事实段没有规范来源标签时追加 `【来源：待核对】`；
- 数字未出现在可用证据正文或推导说明中时追加人工核对标记，不擅自删除或改写原句。

- [ ] **Step 4: 运行第三组案例并确认 GREEN**

Run: `uv run --locked pytest tests/test_research_synthesis_workflow.py -k "derived_image_and_untraceable" -v`

Expected: PASS。

### Task 4: 保留三级阿拉伯数字标题语义

**Files:**
- Modify: `tests/test_research_synthesis_workflow.py`
- Modify: `skills/research_synthesis/workflow.py`

- [ ] **Step 1: 写失败测试**

新增测试：正文中的 `1.具体情况` 在不匹配一级计划标题时仍为 `1.具体情况`，不能自动改为 `一是具体情况`。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `uv run --locked pytest tests/test_research_synthesis_workflow.py -k "keeps_tertiary_arabic_heading" -v`

Expected: 当前 `_normalize_heading_line` 会改成“一是”，测试失败。

- [ ] **Step 3: 修改标题归一化最小逻辑**

只有与期望一级标题匹配的 `1.`、`2.` 才转为“一、”“二、”；其他短标题保留阿拉伯数字层级。括号数字仍按既有规则处理为二级标题或正文列举。

- [ ] **Step 4: 运行标题测试并确认 GREEN**

Run: `uv run --locked pytest tests/test_research_synthesis_workflow.py -k "keeps_tertiary_arabic_heading" -v`

Expected: PASS。

### Task 5: 改写计划提示词、成稿提示词和 SKILL.md

**Files:**
- Modify: `tests/test_research_synthesis_workflow.py`
- Modify: `skills/research_synthesis/prompts/plan.md`
- Modify: `skills/research_synthesis/prompts/draft.md`
- Modify: `skills/research_synthesis/SKILL.md`
- Modify: `skills/research_synthesis/config.yaml`

- [ ] **Step 1: 先写提示契约测试**

在网关调用测试中断言计划提示备注包含“先判断提纲类型和覆盖方式”，成稿提示备注包含“不可使用 usable=false 的证据”，并断言计划 JSON 会传给第二次调用。

- [ ] **Step 2: 运行契约测试并确认 RED**

Run: `uv run --locked pytest tests/test_research_synthesis_workflow.py -k "preserves_material_roles" -v`

Expected: 新提示语不存在，测试失败。

- [ ] **Step 3: 写最小有效指令**

`plan.md` 明确输出顺序：

1. 分类 `outline_type`；
2. 决定 `coverage_mode`；
3. 生成 required/selected/omitted 清单；
4. 建立逐条证据台账；
5. 标记来源、位置、时间、口径、单位、推导和可用性。

`draft.md` 明确正文只能使用 `usable=true` 的证据，选择性目录不能机械复刻全部目录，问卷型必须保留所有必答项。

`SKILL.md` 改成简洁的判断与执行契约，保留文件上限 10、Word 输出、图片提醒、开头结尾备注等既有约束；`config.yaml` 只同步能力描述，不改触发词和工具权限。

- [ ] **Step 4: 运行契约测试并确认 GREEN**

Run: `uv run --locked pytest tests/test_research_synthesis_workflow.py -k "preserves_material_roles" -v`

Expected: PASS。

### Task 6: 全量回归、核心文档和受管交付

**Files:**
- Modify: `docs/capabilities/README.md`
- Modify: `docs/development/TODO.md`
- Modify: `docs/development/architecture.md`
- Modify: `docs/development/testing-and-delivery.md`
- Modify: `skills/research_synthesis/config.yaml`

- [ ] **Step 1: 跑综合调研完整测试**

Run: `uv run --locked pytest tests/test_research_synthesis_workflow.py tests/test_installed_writer_skills.py -v`

Expected: PASS。

- [ ] **Step 2: 跑受影响的平台和 Word 相关回归**

Run: `uv run --locked pytest tests/test_platform_document_service.py tests/test_platform_file_readers.py tests/test_platform_registry.py tests/test_platform_router.py tests/test_platform_runtime.py -v`

Expected: PASS。

- [ ] **Step 3: 更新核心文档**

记录以下真实变化：

- 提纲不再一律视为固定骨架；
- 支持 exhaustive/selective 两种覆盖方式；
- 台账区分原文、推导、图片候选和外部缺失；
- 图片只提醒不识别；
- 数字和事实来源有确定性复核标记；
- 3 组脱敏案例成为长期回归资产。

- [ ] **Step 4: 运行文档闸门和全量相关测试**

Run: `uv run --locked python scripts/project_docs.py check`

Run: `uv run --locked pytest tests/test_research_synthesis_workflow.py tests/test_installed_writer_skills.py tests/test_platform_document_service.py tests/test_platform_file_readers.py tests/test_platform_registry.py tests/test_platform_router.py tests/test_platform_runtime.py -v`

Expected: 全部通过。

- [ ] **Step 5: 检查 diff，暂存准确文件并运行 staged 闸门**

Run: `git diff --check`

Run: `git diff -- skills/research_synthesis tests/test_research_synthesis_workflow.py docs/capabilities/README.md docs/development/TODO.md docs/development/architecture.md docs/development/testing-and-delivery.md docs/superpowers/plans/2026-07-16-research-synthesis-case-driven.md`

Run: `uv run --locked python scripts/project_docs.py check --staged`

Expected: 无意外文件、无真实案例内容、闸门通过。

- [ ] **Step 6: 创建逻辑提交、受管推送并核对同步**

Run: `git commit -m "feat: make research synthesis case-driven"`

Run: `uv run --locked python scripts/project_docs.py push --summary "综合调研按案例规律升级为提纲分类和证据台账驱动" --impact "问卷型提纲逐项覆盖，政策目录型提纲按证据选择主题，数字、来源和图片均增加可回溯校验" --next-step "继续用用户新增案例做前向测试，暂不识别图片内容或引入外部事实"`

Run: `uv run --locked python scripts/project_docs.py check-sync`

Expected: 本地 `main` 与远端同步。
