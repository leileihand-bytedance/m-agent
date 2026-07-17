# M-Agent 开发入口

本文档用于让 Codex、Claude Code 和开发者快速进入项目。文档职责和完整导航见 [文档地图与治理规则](../README.md)。

## 开发前阅读

按顺序阅读：

1. 根目录 `AGENTS.md` 或 `CLAUDE.md`
2. [文档地图与治理规则](../README.md)
3. [整体架构](architecture.md)
4. [目录规范](directory-standard.md)
5. [Codex / Claude Code 工作流](codex-claude-workflow.md)
6. [当前待办](TODO.md)
7. [测试和交付规范](testing-and-delivery.md)

修改具体模块时，再阅读：

- 底座：`docs/agent-platform/README.md`、`app/platform/README.md`
- 企业微信入口：对应 `app/<module>/README.md`
- 业务能力：`docs/capabilities/` 和对应 `skills/<skill_id>/SKILL.md`
- 知识库：`docs/knowledge/`
- 运维和控制台：`docs/operations/`

## 技术基线

```text
Python 3.13.14
uv + 项目根目录 .venv
Pydantic AI
文件化 Skill Registry
受限 ToolGateway
企业微信 Bot 入口
仓库外 M-Agent-Files 运行数据目录
```

首次进入项目：

```bash
uv sync --locked
uv run --locked python scripts/project_docs.py install-hooks
uv run --locked python -m app.platform.cli --check-config
```

所有代码、测试和脚本必须通过 `uv run --locked ...` 执行。

## 目录边界

```text
app/platform/       # 公共运行底座，不写业务规则
app/writing/        # 写作入口适配，不写成稿规则
app/review/         # 独立审核入口和审核实现
app/rewrite_bot/    # 材料润色入口适配
app/admin/          # 本机管理面
skills/             # 业务规则、结构化输入输出和工作流
tests/              # 长期自动化测试
docs/               # 当前事实、规范、计划和历史资料
scripts/            # 可重复使用的维护工具
archive/            # 已退出运行的历史代码
```

真实用户材料、任务结果、会话、日志、知识库和队列统一位于项目外部的 `M-Agent-Files/`。

## 日常开发流程

1. 确认 Git 状态和当前权威文档。
2. 在计划中列出代码、测试和文档影响。
3. 先写或更新测试，确认能暴露问题。
4. 做最小范围实现，不跨模块顺手重构。
5. 运行相关测试和文档检查。
6. 更新真正受影响的当前事实文档，不向 README 追加开发流水。
7. 清理临时脚本、缓存和输出。
8. 创建逻辑提交并通过受管命令推送。

详细流程见 [Codex / Claude Code 工作流](codex-claude-workflow.md)。

## 文档同步原则

- README 只在定位、入口、接口或导航变化时更新。
- 底座当前行为写入架构或底座说明。
- Skill 业务规则以对应 `SKILL.md` 为唯一来源。
- 能力范围和用户使用边界写入 `docs/capabilities/`。
- 未完成工作写入 `TODO.md`；完成后移出。
- 开发过程由本机月度日志完整记录，不进入 Git。
- 详细文件变化由 Git 保存，不复制到项目文档。

提交前运行：

```bash
uv run --locked python scripts/project_docs.py check
uv run --locked python scripts/project_docs.py check --staged
```

## 常用验证

```bash
# 全仓离线回归
uv run --locked pytest tests -q

# 审核专项保护
uv run --locked python tests/test_review_bot.py

# 底座配置
uv run --locked python -m app.platform.cli --check-config

# 文档体系
uv run --locked pytest tests/test_project_documentation.py -q
uv run --locked python scripts/project_docs.py check
```

真实模型、企业微信、附件和故障恢复测试必须按 [测试和交付规范](testing-and-delivery.md) 单独执行和说明。

## 当前工作入口

当前路线和优先级只看 [TODO.md](TODO.md)，项目建设状态可在本机控制台查看。已经完成的功能以代码、测试、架构、模块 README 和 Skill 文档为准；历史设计和实施过程位于 `docs/history/`，不作为当前开发依据。
