# M-Agent Claude Code 开发入口规范

本文件给 Claude Code 使用。规则与 `AGENTS.md` 保持一致。

## 默认沟通方式

- 默认使用简体中文。
- 面向非技术用户时，用通俗方式解释。
- 基于事实回答；不确定时先查代码、查文档或调研。
- 任何开发完成前必须跑测试。

## 当前架构方向

M-Agent 正在演进为：

```text
统一企业微信入口
  -> app/platform/ 底座区
  -> skills/ 功能区
  -> Pydantic AI Agent
  -> 受限工具层
```

Claude Code 后续开发前先阅读：

1. `docs/development/README.md`
2. `docs/development/architecture.md`
3. `docs/development/codex-claude-workflow.md`
4. `docs/development/TODO.md`
5. `docs/agent-platform/README.md`
6. `docs/capabilities/README.md`

## Python 环境（强制）

- 固定使用 uv 管理的 Python 3.13.14，版本见 `.python-version`。
- 首次进入项目先运行 `uv sync --locked`，项目依赖只从 `pyproject.toml` 和 `uv.lock` 同步到根目录 `.venv`。
- 所有代码、测试、脚本和 Bot 都通过 `uv run --locked ...` 启动，不使用裸 `python`、`python3`、`pip` 或全局 `pytest`。
- 依赖变更必须同步 `pyproject.toml` 与 `uv.lock`，完成回归和核心文档检查后再交付。

## 开发边界

- `app/platform/`：底座区，只写公共运行能力。
- `app/admin/`：本机管理后台，只做配置和观察。
- `skills/`：功能区，只写具体业务能力。
- `app/writing/`：当前直报 Bot 入口适配层。
- `app/review/`：旧审核 Bot，后续包装为 review skill。
- `archive/inactive-2026-07-04/`：已归档停滞模块，不作为新开发入口。

## 安全底线

- 外部用户只能调用已登记 skill。
- skill 只能用 `config.yaml` 声明的工具。
- 工具不能读本机任意目录。
- 不允许泄露 `.env`、密钥、日志、历史材料。
- 不要把 M-Agent 做成远程万能 Mac 助手。

## 测试要求

新增或修改功能必须补测试。常用命令：

```bash
uv run --locked pytest tests/test_platform_registry.py tests/test_platform_router.py tests/test_platform_tools.py tests/test_platform_builtin_tools.py tests/test_platform_file_readers.py tests/test_platform_document_service.py tests/test_platform_data_paths.py tests/test_platform_pydantic_runtime.py tests/test_direct_report_workflow.py tests/test_platform_runtime.py tests/test_platform_demo.py tests/test_platform_wecom_gateway.py tests/test_platform_storage.py tests/test_platform_identity.py tests/test_platform_app.py tests/test_platform_cli.py tests/test_writing_platform_bot.py tests/test_writing_portal.py -v
uv run --locked python tests/test_review_bot.py
```

涉及审核 LLM 端到端时再运行：

```bash
uv run --locked python tests/test_reviewer.py
```

该命令依赖真实模型连接，失败时要区分网络/模型问题和代码问题。

## 测试代码管理

- `tests/` 下的自动化测试要长期保留。
- 一次性接口调试、模型连通性验证、临时数据检查脚本，完成后必须删除。
- 不要在根目录或业务目录长期保留 `test_*.py`、`debug_*.py`、`tmp_*.py` 等临时脚本。
- 测试缓存、`__pycache__`、`.pytest_cache`、临时输出和任务记录不得作为代码变更提交。
- 临时验证逻辑如果有复用价值，应整理成 `tests/` 下的正式测试。

## 核心文档同步闸门（强制）

- 每个开发计划必须先列出受影响的核心文档，不能等用户提醒。
- 底座、skill、入口、配置、路线或测试命令发生变化时，必须同步更新对应架构文档、模块 README、SKILL、TODO 或测试交付文档。
- 完成后运行 `uv run --locked python scripts/project_docs.py check`；Git pre-commit hook 会基于暂存区版本按模块核对对应核心文档，并覆盖依赖和 hooks 变更。
- 首次克隆后运行 `uv run --locked python scripts/project_docs.py install-hooks` 启用仓库内 hooks。
- `STATUS-REPORT.md` 和含真实用户 ID 的 `config/platform-policy.yaml` 只在本机保留，两者都不得暂存或提交。状态报告只记录有业务含义的开发进展，不记录逐次提交清单。
- 行为已变更但核心文档未同步，属于禁止交付情形。

## STATUS-REPORT 开发日志规范（强制）

- `STATUS-REPORT.md` 用来说明完成了什么功能、解决了什么问题、实际改变了什么能力或用户体验，以及当前边界和下一步。
- 每个完成并验证的逻辑开发节点只写一条开发日志；必要时补充关键测试结论和剩余风险。
- 不得把文件列表、文件数量、提交摘要或推送范围当作日志主体。Git 哈希只可放在最后用于技术追溯。
- 不得写入用户材料、业务原文、真实用户 ID、密钥、错误堆栈或本机任务路径。
- post-commit 只提醒远端同步状态；只有受管推送成功后才自动生成一条开发日志，避免重复记录。

## Git 远端同步（强制）

- 活跃开发达到可测试的逻辑节点后及时提交，任务结束前不得遗留应提交变更。
- 禁止直接 `git push`；统一运行 `uv run --locked python scripts/project_docs.py push --summary "完成了什么功能" --impact "实际改变了什么能力" --next-step "当前边界或下一步"`。命令会先获取远端、阻止分叉和强推，成功后再写本机开发日志。
- 每次成功推送必须在本机 `STATUS-REPORT.md` 追加一条以功能、能力变化和下一步为主体的开发日志；失败不得记成成功。
- 推送后运行 `uv run --locked python scripts/project_docs.py check-sync` 确认差异为零。post-commit 只提醒未推送状态，pre-push 检查文档并阻止绕过受管流程；网络或权限失败必须如实报告。
