# Direct Report Policy Research Implementation Plan

> 状态：阶段性方案已实施；随后由“共享政策研究层”扩展并部分取代。本文件保留为设计历史，不作为当前行为唯一依据。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 下线全项目 `wiki` 依赖，并为 `direct_report` 增加第一版“小微金融/科技创新”政策研究层。

**Architecture:** 平台层删除 `policy_wiki_materials` 和相关配置；`direct_report` 新增 skill 内部政策研究模块，优先从本地政策库筛选贴切政策，并把研究结论写入规划说明。找不到贴切政策时直接入题，不强挂政策。

**Tech Stack:** Python, pytest, ToolGateway, Pydantic AI skill runtime

---

### Task 1: 写失败测试，锁定 Wiki 下线范围

**Files:**
- Modify: `tests/test_platform_builtin_tools.py`
- Modify: `tests/test_platform_app.py`
- Modify: `tests/test_platform_cli.py`
- Modify: `tests/test_direct_report_workflow.py`
- Modify: `tests/test_brief_writer_workflows.py`
- Delete: `tests/test_policy_knowledge_wiki.py`

- [ ] **Step 1: 写失败测试**

补充和调整以下断言：

```python
assert "policy_wiki_materials" not in tools
assert "policy_wiki_vault_dir" not in report
assert [call[0] for call in calls] == ["policy_materials"]
```

- [ ] **Step 2: 跑定向测试，确认失败**

Run:

```bash
pytest tests/test_platform_builtin_tools.py tests/test_platform_app.py tests/test_platform_cli.py tests/test_direct_report_workflow.py tests/test_brief_writer_workflows.py -q
```

Expected: FAIL，失败点集中在 `policy_wiki_materials`、`policy_wiki_vault_dir` 仍存在。

### Task 2: 写失败测试，锁定直报政策研究层行为

**Files:**
- Modify: `tests/test_direct_report_workflow.py`
- Create: `tests/test_direct_report_policy_research.py`

- [ ] **Step 1: 写失败测试**

新增覆盖：

```python
assert research.use_policy is True
assert research.theme_id == "small_micro"
assert research.selected_policy["title"] == "关于提升小微企业金融服务质效的通知"
assert research.use_policy is False
assert research.reason == "no_qualified_policy"
```

- [ ] **Step 2: 跑定向测试，确认失败**

Run:

```bash
pytest tests/test_direct_report_policy_research.py tests/test_direct_report_workflow.py -q
```

Expected: FAIL，提示缺少政策研究模块或行为与新预期不一致。

### Task 3: 实现 Wiki 下线

**Files:**
- Modify: `app/platform/builtin_tools.py`
- Modify: `app/platform/config.py`
- Modify: `app/platform/app.py`
- Modify: `app/platform/cli.py`
- Modify: `app/policy_knowledge/cli.py`
- Modify: `app/config.example.env`
- Modify: `app/writing/bot.py`
- Modify: `skills/direct_report/config.yaml`
- Modify: `skills/writer1/config.yaml`
- Modify: `skills/writer2/config.yaml`
- Modify: `skills/direct_report/workflow.py`
- Modify: `skills/writer1/workflow.py`
- Delete: `app/policy_knowledge/wiki.py`

- [ ] **Step 1: 删除平台工具和配置入口**

删除 `policy_wiki_materials`、`policy_wiki_vault_dir` 和 `export-wiki` 相关代码。

- [ ] **Step 2: 删除 skill 调用**

让 `direct_report`、`writer1`、`writer2` 不再尝试调用 `policy_wiki_materials`。

- [ ] **Step 3: 跑测试确认变绿**

Run:

```bash
pytest tests/test_platform_builtin_tools.py tests/test_platform_app.py tests/test_platform_cli.py tests/test_direct_report_workflow.py tests/test_brief_writer_workflows.py -q
```

Expected: PASS

### Task 4: 实现直报政策研究层

**Files:**
- Create: `skills/direct_report/policy_research.py`
- Modify: `skills/direct_report/workflow.py`
- Modify: `skills/writing_planner.py`
- Modify: `skills/direct_report/prompts/draft.md`

- [ ] **Step 1: 最小实现政策研究模块**

模块至少提供：

```python
def research_direct_report_policy(
    *,
    instruction: str,
    materials: list[object],
    tools: ToolGateway,
) -> DirectReportPolicyResearch:
    ...
```

输出至少包括：

```python
theme_id: str | None
theme_label: str | None
use_policy: bool
reason: str
selected_policy: dict[str, object] | None
lead_guidance: str
bridge_guidance: str
closing_guidance: str
```

- [ ] **Step 2: 在 workflow 接入政策研究层**

替换原先“直接取 policy_materials[:1]”逻辑：

```python
research = research_direct_report_policy(...)
if research.use_policy and research.selected_policy:
    materials.append(research.selected_policy)
planning_note = build_direct_report_plan(..., policy_research=research)
```

- [ ] **Step 3: 在写作规划里写入研究结论**

规划说明中加入：

```python
"政策研究结论：..."
"开头衔接建议：..."
"政策转微众建议：..."
```

- [ ] **Step 4: 跑测试确认变绿**

Run:

```bash
pytest tests/test_direct_report_policy_research.py tests/test_direct_report_workflow.py tests/test_writing_planner.py -q
```

Expected: PASS

### Task 5: 更新文档并做回归验证

**Files:**
- Modify: `docs/knowledge/policy.md`
- Delete: `docs/development/policy-wiki-obsidian.md`
- Modify: `docs/development/README.md`
- Modify: `docs/development/architecture.md`
- Modify: `docs/development/TODO.md`
- Modify: `docs/capabilities/README.md`
- Modify: `docs/agent-platform/README.md`

- [ ] **Step 1: 更新文档**

明确：

1. `wiki` 已下线。
2. 直报新增第一版政策研究层。
3. 第一版只覆盖 `小微金融`、`科技创新`。
4. 没有贴切政策时直入主题。

- [ ] **Step 2: 跑本次相关回归**

Run:

```bash
pytest tests/test_platform_builtin_tools.py tests/test_platform_app.py tests/test_platform_cli.py tests/test_direct_report_policy_research.py tests/test_direct_report_workflow.py tests/test_writing_planner.py tests/test_brief_writer_workflows.py -q
```

Expected: PASS

- [ ] **Step 3: 记录结果**

把结果写入对应核心文档和 Git 提交说明：

1. `wiki` 已全项目下线。
2. `direct_report` 政策研究层第一版已落地。
3. 当前只支持 `小微金融`、`科技创新`。
