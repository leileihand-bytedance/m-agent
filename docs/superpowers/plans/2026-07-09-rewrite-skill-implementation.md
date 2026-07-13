# Rewrite Skill Implementation Plan

> 状态：已实施。当前 v1 只支持直接粘贴文字，不支持链接、Word 或 PDF。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增独立 `rewrite` skill，用于直接粘贴文字润色，并支持继续对话式修改，同时与直报/简报改稿严格切割。

**Architecture:** 在 `skills/rewrite/` 新增独立 skill，运行时只允许使用 `llm_writer`。平台侧补一层新任务识别规则和输出展示规则，确保 `rewrite` 的回复格式、会话记录和旧稿续改边界都符合要求。

**Tech Stack:** Python、Pydantic、现有 PlatformRuntime、ConversationStore、pytest

---

### Task 1: 写失败测试覆盖新 skill 路由和展示

**Files:**
- Modify: `tests/test_platform_registry.py`
- Modify: `tests/test_platform_router.py`
- Modify: `tests/test_platform_wecom_gateway.py`
- Modify: `tests/test_platform_pydantic_runtime.py`

- [ ] 写 `rewrite` skill 注册测试
- [ ] 写“帮我润色这段：……”路由到 `rewrite` 的测试
- [ ] 写 `revision_note` 在文本回复中可见的测试
- [ ] 写 Pydantic runtime 支持 `rewrite` 自定义输出模型的测试

### Task 2: 写失败测试覆盖 workflow 和会话切割

**Files:**
- Create: `tests/test_rewrite_workflow.py`
- Modify: `tests/test_platform_intent.py`
- Modify: `tests/test_platform_app.py`

- [ ] 写直接贴文字即可润色的测试
- [ ] 写缺正文时追问的测试
- [ ] 写基于上一版继续润色的测试
- [ ] 写“已有直报活跃会话，但用户贴了新正文要求润色”应新开 `rewrite` 的测试

### Task 3: 新增 `rewrite` skill 最小实现

**Files:**
- Create: `skills/rewrite/SKILL.md`
- Create: `skills/rewrite/__init__.py`
- Create: `skills/rewrite/config.yaml`
- Create: `skills/rewrite/schema.py`
- Create: `skills/rewrite/workflow.py`
- Create: `skills/rewrite/prompts/draft.md`

- [ ] 增加 skill 配置、schema、prompt 和 workflow
- [ ] 只允许 `llm_writer`
- [ ] 实现直接贴文字解析、默认规则、追问和 revision 流程

### Task 4: 修改平台公共层

**Files:**
- Modify: `app/platform/router.py`
- Modify: `app/platform/intent.py`
- Modify: `app/platform/runtime.py`
- Modify: `app/platform/gateway/wecom.py`
- Modify: `app/platform/app.py`
- Modify: `app/writing/bot.py`

- [ ] 新增 `rewrite` 路由和澄清提示
- [ ] 增加“新润色任务”优先于旧稿续改的判定
- [ ] 在运行结果里透传 `revision_note`
- [ ] 在回复和日志里展示 `revision_note`
- [ ] 将写作 Bot 的 `rewrite` 标签改为“材料润色”

### Task 5: 更新文档并跑验证

**Files:**
- Modify: `docs/capabilities/README.md`
- Modify: `docs/development/README.md`

- [ ] 更新能力文档，写明 `rewrite` 第一版边界
- [ ] 更新开发总指南中的当前能力说明
- [ ] 在核心文档和 Git 提交中记录本次实现
- [ ] 运行相关 pytest 用例并检查输出
