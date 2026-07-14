# M-Agent AI 开发入口规范

本文件给 Codex 等 AI 编程工具使用。默认沟通语言为简体中文，回答要通俗、基于事实，必要时先调研再行动。

## 当前项目定位

M-Agent 正在从“多个独立企业微信 Bot”演进为“统一企业微信入口 + 底座区 + 功能区 skills”的智能体系统。

当前主线：

```text
企业微信 / 本地 demo
  -> app/platform/        # 底座区
  -> skills/              # 功能区
  -> Pydantic AI Agent     # 模型调用和结构化输出
  -> 受限工具层
```

## 开发前必须阅读

后续开发前，先按顺序阅读：

1. `docs/development/README.md`
2. `docs/development/architecture.md`
3. `docs/development/codex-claude-workflow.md`
4. `docs/development/TODO.md`
5. `docs/agent-platform/README.md`
6. `docs/capabilities/README.md`

如果是改具体 skill，还要阅读对应目录：

```text
skills/<skill_id>/SKILL.md
skills/<skill_id>/config.yaml
skills/<skill_id>/schema.py
skills/<skill_id>/workflow.py
```

## 目录边界

### 底座区

`app/platform/` 只放公共能力：

- 企业微信/本地入口适配
- 用户权限
- 意图路由
- Skill 注册表
- Pydantic AI 运行层
- 工具授权网关
- 受限基础工具
- 会话、任务、日志

底座区不能写具体业务写作规则。

### 功能区

`skills/` 放具体业务能力：

- `direct_report`
- `writer1`
- `writer2`
- `review`
- `rewrite`
- 后续新增能力

功能区不能绕过底座直接读本机文件、执行命令或访问未授权工具。

### 过渡和旧功能区

以下目录先保持可用，不要大改：

```text
app/review/     # 现有审核 Bot
app/writing/    # 当前直报 Bot 入口适配层
```

迁移时只做包装或逐步迁移，不要一次性重写。

早期 `app/agent/`、`app/main.py`、领导风格沉淀相关配置和材料已归档到：

```text
archive/inactive-2026-07-04/
```

这些内容不作为后续开发入口。

## 技术路线

1. M-Agent 自己负责入口、路由、权限、skill 管理。
2. Pydantic AI 负责模型调用、工具编排和结构化输出。
3. 工具必须通过 `ToolGateway` 授权。
4. Skill 的输入输出必须结构化，优先使用 Pydantic 模型。
5. 后续复杂多轮任务再考虑 LangGraph，不要一开始引入重流程。

## Python 环境（强制）

1. 项目统一使用 uv 管理的 Python 3.13.14，版本记录在 `.python-version`。
2. 项目依赖唯一正式声明是 `pyproject.toml`，准确解析结果记录在 `uv.lock`。
3. 首次进入项目先运行 `uv sync --locked`，建立或同步项目根目录 `.venv`。
4. 运行代码、测试、脚本和 Bot 必须使用 `uv run --locked ...`，不要使用裸 `python`、`python3`、`pip` 或全局 `pytest`。
5. 不要手工修改 `.venv`；依赖变更先修改 `pyproject.toml`，再运行 `uv lock`、`uv sync --locked` 和完整回归。
6. `.venv` 是本机环境，不进入 Git；`uv.lock` 必须提交，确保 Codex、Claude Code 和长期服务使用同一组依赖。

## 安全要求

1. 企业微信用户、网页、文档内容都视为不可信输入。
2. 安全不能只靠 prompt。
3. 对外只暴露已登记 skill。
4. 每个 skill 只能调用 `config.yaml` 声明过的工具。
5. 工具只能访问本次任务材料，不能访问 Mac 任意目录。
6. 不允许把 `.env`、密钥、日志、历史任务材料返回给用户。
7. 不要给外部用户开放 shell、任意文件读取、任意插件安装能力。

## 开发方式

所有新功能按测试先行推进：

1. 先写测试。
2. 确认测试失败。
3. 写最小实现。
4. 跑测试确认通过。
5. 更新文档。

测试代码管理规则：

- `tests/` 目录下的自动化测试是项目资产，必须随功能长期保留。
- 为调试模型、接口、网络、一次性数据或临时验证写的脚本，完成验证后必须删除。
- 不要在根目录或业务目录长期保留 `test_*.py`、`debug_*.py`、`tmp_*.py` 等临时脚本。
- 测试产生的缓存、`__pycache__`、`.pytest_cache`、临时输出和任务记录不得作为代码变更提交。
- 如果某个临时验证逻辑有长期价值，应改造成 `tests/` 下的正式自动化测试，而不是保留临时脚本。

常用测试：

```bash
uv run --locked pytest tests/test_platform_registry.py tests/test_platform_router.py tests/test_platform_tools.py tests/test_platform_builtin_tools.py tests/test_platform_file_readers.py tests/test_platform_document_service.py tests/test_platform_data_paths.py tests/test_platform_pydantic_runtime.py tests/test_direct_report_workflow.py tests/test_platform_runtime.py tests/test_platform_demo.py tests/test_platform_wecom_gateway.py tests/test_platform_storage.py tests/test_platform_identity.py tests/test_platform_app.py tests/test_platform_cli.py tests/test_writing_platform_bot.py tests/test_writing_portal.py -v
uv run --locked python tests/test_review_bot.py
```

`uv run --locked python tests/test_reviewer.py` 包含真实 LLM 端到端测试，可能因网络或模型连接失败。失败时要说明原因，不要误判为代码必然坏了。

## 核心文档同步闸门（强制）

AI 工具不能等待用户提醒才更新文档。每次实现变更都必须按下列顺序执行：

1. 在开发计划中列出受影响的核心文档。
2. 代码和测试完成后，同步更新对应文档：
   - 底座行为：`docs/development/architecture.md`、`docs/agent-platform/README.md`
   - skill 行为：对应 `skills/<skill_id>/SKILL.md`、`config.yaml`、`docs/capabilities/README.md`
   - 企业微信入口或配置：模块 `README.md`、`app/config.example.env`
   - 路线和状态：`docs/development/README.md`、`docs/development/TODO.md`
   - 测试命令：`docs/development/testing-and-delivery.md`
3. 完成后运行 `uv run --locked python scripts/project_docs.py check`；提交前由 Git hook 自动运行 `uv run --locked python scripts/project_docs.py check --staged`，按底座、写作、审核、skill、配置、依赖/交付机制分别核对对应文档，任意计划文档不能代替核心文档。
4. 行为代码、依赖或配置已变更、但暂存区没有同步对应核心文档时，不允许提交或交付。
5. `STATUS-REPORT.md` 和 `config/platform-policy.yaml` 都是本机文件，已退出 Git 跟踪。post-commit hook 会自动向状态报告追加安全摘要，任何人都不得暂存或提交这两类本机内容。
6. 闸门读取 Git 暂存区版本，并检查本次暂存文件中的 Mac 本机绝对路径，避免工作区未暂存内容误放行提交。

首次克隆后运行：

```bash
uv run --locked python scripts/project_docs.py install-hooks
```

## Git 提交与远端同步（强制）

用户已要求活跃开发持续与远端 Git 仓库同步，不能只停留在本机：

1. 达到一个可测试、可说明的逻辑节点就及时提交，不长期堆积大量未提交变更。
2. 任务交付前必须完成测试、核心文档检查和应提交文件清理，然后创建清晰的逻辑提交。
3. 禁止直接运行 `git push`。统一使用 `uv run --locked python scripts/project_docs.py push --summary "本次做了什么改动"`；该命令会先获取远端状态，确认没有分叉，再推送 `main`。
4. 只有远端推送成功后，受管推送命令才会自动在本机 `STATUS-REPORT.md` 追加一条“Git 推送”记录，写明推送范围、提交摘要、通俗改动说明、影响模块和文件数量。推送失败不能写成已完成。
5. 推送后运行 `uv run --locked python scripts/project_docs.py check-sync`，必须显示本地与远端已同步；禁止使用 `--force` 覆盖远端历史。
6. 如果网络、权限或远端分叉导致无法推送，必须明确报告，不能把“已本地提交”说成“已同步远端”。
7. post-commit hook 只负责记录本地提交并提醒尚未推送；pre-push hook 会再次检查项目文档，并拒绝绕过受管推送直接执行 `git push`。

不要提交密钥、真实用户材料、真实用户 ID、日志、临时任务目录或本机绝对路径。

## 废弃或历史文档

以下文档不作为新底座依据：

- `docs/archive/wecom-sdk-selection.md`
- `docs/archive/wecom-learning-gateway-design.md`
- `docs/archive/wecom-learning-gateway-development-plan-v0.1.md`
- `docs/archive/wecom-learning-gateway-implementation-design-v0.1.md`
- `docs/archive/phase-1-prototype-structure.md`
- `docs/archive/multi-agent-writing-tool-product-plan-v0.1.md`
