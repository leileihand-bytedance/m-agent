# Codex 与 Claude Code 开发工作流

本项目允许用户主要通过自然语言推动开发，但 AI 工具必须遵守相同的代码、测试、文档和交付流程。

## 开始前

1. 阅读 `AGENTS.md`。
2. 阅读 `docs/README.md`，确认哪份文档是本次变更的权威来源。
3. 阅读整体架构、当前 TODO 和相关模块/Skill 文档。
4. 在 `main` 运行 `task-status`，识别其他工具正在进行的任务。
5. 从已同步且干净的 `main` 创建任务工作区；不要直接在 `main` 修改文件。
6. 在计划中同时列出实现、测试和文档影响。

不要先修改代码再临时决定文档放在哪里。

## 任务分支生命周期

本项目只保留一个长期分支 `main`。Codex、Claude Code 和紧急修复使用短期任务分支，不建立长期 `develop`，同时最多 2 个任务工作区。

开始任务：

```bash
uv run --locked python scripts/project_docs.py task-status
uv run --locked python scripts/project_docs.py start-task codex/<task-name>
# Claude Code 使用 claude/<task-name>；紧急修复使用 hotfix/<task-name>
```

命令会确认 `main` 干净且与 `origin/main` 同步，在 `.worktrees/` 创建独立目录，并复用同一项目 `.venv`。它不会复制、链接或读取生产 `.env`。

开发和提交都在新工作区进行。任务完成后回到 `main` 主工作区执行：

```bash
uv run --locked python scripts/project_docs.py finish-task codex/<task-name> \
  --summary "完成了什么功能" \
  --impact "实际改变了什么能力" \
  --verification "做了哪些关键验证" \
  --next-step "当前边界或下一步"
```

`finish-task` 只接受干净任务工作区和包含最新 `main` 的分支；它执行文档检查、快进合并、受管推送和月度日志记录。远端成功后才删除任务工作区和本地分支。发生冲突、远端分叉或推送失败时停止自动清理，保留现场供人工处理。

任务分支不得推送到远端。`main` 的直接提交、非标准分支提交和非 `main` 远端推送均由 Git hook 拒绝。

## Bot 测试边界

- 离线单元测试、模拟企业微信和本地固定样本可在任务分支直接运行。
- 任务分支需要真实企业微信联调时，必须使用 `M_AGENT_RUNTIME_ENV=test`、专用测试 Bot 凭据和独立 `M_AGENT_TEST_DATA_DIR`。
- 任务工作区不得复制生产 `.env`；测试配置只保存在该工作区本机 `.env`，不进入 Git。
- 测试模式不回退生产凭据，运行路径越过测试数据根目录会在连接企业微信前失败。
- 生产 Bot 只能从 `main` 启动。任务分支的功能只有合并后，才能进入生产 Bot 小范围验收。

## 标准循环

```text
理解需求和边界
  -> 读取当前实现和权威文档
  -> 写失败测试或固定回归样本
  -> 做最小实现
  -> 运行相关回归
  -> 更新真正受影响的当前文档
  -> 清理临时文件
  -> 文档闸门
  -> 逻辑提交
  -> 受管推送
  -> 当月开发日志
```

一次开发节点应当能说明“完成了什么能力”，不能只说明改了哪些文件。

## 修改类型

### 新增或修改 Skill

优先处理：

```text
skills/<skill_id>/SKILL.md
skills/<skill_id>/config.yaml
skills/<skill_id>/schema.py
skills/<skill_id>/workflow.py
skills/<skill_id>/prompts/
tests/test_<skill_id>_*.py
```

业务规则以 `SKILL.md` 为唯一来源。只有用户可用范围、输入输出或入口边界变化时，才同步 `docs/capabilities/`。

### 修改公共底座

底座包括路由、权限、注册表、材料组装、文档服务、会话、任务、工具授权、模型运行、附件交付和运维。

要求：

- 不把具体写作或审核规则写入 `app/platform/`。
- 不放宽任务目录、URL、文档和工具安全边界。
- 补平台测试和跨入口保护测试。
- 更新 `docs/development/architecture.md` 或 `docs/agent-platform/README.md`。

### 修改企业微信入口

入口只负责 SDK 消息标准化、材料接收、即时回复、任务提交和结果交付。具体业务判断留在 Skill 或审核模块。

更新对应 `app/<module>/README.md`；启动、配置、心跳或故障处理变化时再更新 `docs/operations/`。

### 修改审核

先明确属于通用规则、类型专属规则、证据定位、任务执行还是入口交互，避免在多个审核器复制同一逻辑。

更新：

- 当前业务范围：`docs/capabilities/review.md`
- 技术入口：`app/review/README.md`
- 运行维护：`docs/operations/bots.md`
- 具体测试：`tests/test_review_*.py`

只更新真正发生变化的文档，不要求三份全部修改。

### 修改知识库

采集、数据结构、来源、更新和检索治理写入 `docs/knowledge/`。写作如何使用知识材料仍由对应 Skill 规定。

## 文档判断

| 变化 | 权威文档 |
|---|---|
| 当前架构和公共数据流 | `docs/development/architecture.md` |
| 底座接口和边界 | `docs/agent-platform/README.md` |
| Skill 业务规则 | `skills/<skill_id>/SKILL.md` |
| 用户能力范围 | `docs/capabilities/` |
| 模块运行入口 | `app/<module>/README.md` |
| Bot 运维 | `docs/operations/` |
| 知识库 | `docs/knowledge/` |
| 未完成路线 | `docs/development/TODO.md` |
| 测试和交付机制 | `docs/development/testing-and-delivery.md` |
| 文档目录和职责 | `docs/README.md`、`directory-standard.md` |

README 不追加日期进度、修复历史或测试数量。完成事项从 TODO 移出；完成过程由月度日志和 Git 保存。

## 测试

测试先行：

1. 写出能暴露问题或固定行为的测试。
2. 确认测试在实现前失败或确实覆盖缺口。
3. 完成最小实现。
4. 跑专项测试，再根据影响范围扩大回归。
5. 真实模型或企业微信测试单独说明网络、凭据和人工观察。

测试矩阵和命令统一查看 `docs/development/testing-and-delivery.md`，不在本文件复制长命令清单。

## 临时文件

- 一次性模型、网络、数据和接口脚本完成后删除。
- 有长期价值的逻辑转成 `tests/` 或 `scripts/` 正式资产。
- 不提交缓存、日志、用户材料、任务输出、真实权限、本机路径和临时截图。
- 遇到其他工具正在修改的文件时，先理解并兼容，不覆盖或回退用户改动。

## 交付

提交前：

```bash
uv run --locked python scripts/project_docs.py check
uv run --locked python scripts/project_docs.py check --staged
```

日常任务禁止直接推送，统一使用前述 `finish-task`。只有代码已经安全合并到 `main`、但上次推送因网络等原因中断的恢复场景，才直接使用：

```bash
uv run --locked python scripts/project_docs.py push \
  --summary "完成了什么功能" \
  --impact "实际改变了什么能力" \
  --verification "做了哪些关键验证" \
  --next-step "当前边界或下一步"
```

受管推送成功后，记录写入 `M-Agent-Files/runtime/development-logs/YYYY-MM.md`，根目录 `STATUS-REPORT.md` 只更新索引。推送后运行：

```bash
uv run --locked python scripts/project_docs.py check-sync
```

只有远端同步、月度日志写入和关键验证都完成后，才能表述为已交付。
