# M-Agent 测试和交付规范

## 基本原则

任何开发或修改都必须经过测试验证。不能只说“应该可以”。

核心文档与代码同属交付物。AI 工具必须在计划阶段列出文档影响，完成后运行：

```bash
uv run --locked python scripts/project_docs.py check
```

首次克隆运行 `uv run --locked python scripts/project_docs.py install-hooks`。提交前 hook 会读取暂存区版本，按模块检查代码、依赖、hooks、配置与对应核心文档；无关计划文档不能代替模块文档。`STATUS-REPORT.md` 由提交后 hook 写入本机，权限文件 `config/platform-policy.yaml` 同样只保留在本机，两者都不进入 Git。

## 统一 Python 环境

M-Agent 固定使用 uv 管理的 Python 3.13.14 和项目根目录 `.venv`。新建或同步开发环境时运行：

```bash
uv sync --locked
```

依赖管理规则：

- `pyproject.toml` 是直接依赖唯一正式声明，正式依赖和 `dev` 测试依赖分组维护。
- `uv.lock` 固定所有直接和间接依赖的准确版本，必须进入 Git。
- `.python-version` 固定 Python 3.13.14；`.venv` 只在本机生成，不进入 Git。
- 不使用裸 `python`、`python3`、`pip` 或全局 `pytest`，避免误用 pyenv、Homebrew 或 macOS 环境。
- 不直接在 `.venv` 里执行 `pip install`。新增或升级依赖时修改 `pyproject.toml`，运行 `uv lock` 和 `uv sync --locked`，再执行完整回归。
- `python-docx`、Pydantic AI 的 Anthropic/OpenAI 模型通道和 `pytest` 都必须显式声明，不能依赖共享环境碰巧已安装。

环境检查：

```bash
uv lock --check
uv sync --locked
uv run --locked python -c "import sys; print(sys.executable); print(sys.version)"
uv run --locked python -c "from pydantic_ai.models.openai import OpenAIChatModel; from pydantic_ai.models.anthropic import AnthropicModel; print('providers ok')"
```

## Git 提交和远端同步

活跃开发不能长期堆积在工作区，也不能在本地提交后就宣称交付完成：

1. 每个可测试的逻辑节点及时提交。
2. 禁止直接运行 `git push`，统一执行 `uv run --locked python scripts/project_docs.py push --summary "本次做了什么改动"`。命令会先获取远端并阻止分叉。
3. 受管推送成功后，自动向本机 `STATUS-REPORT.md` 写入推送范围、提交摘要、改动说明、影响模块和文件数量；失败时不写成功记录。
4. 推送后再次运行 `uv run --locked python scripts/project_docs.py check-sync`，输出必须为“本地分支与远端已同步”；禁止强推覆盖远端历史。
5. 网络、凭据或分叉阻塞时，在交付说明中明确写出，不得省略。

post-commit hook 会记录本地提交并在存在未推送提交时告警；pre-push hook 会执行核心文档检查，并拒绝绕过受管命令直接推送。钩子不会自动强推或在后台静默访问网络。

## 测试分层

运行数据目录或迁移逻辑变更时，至少运行：

```bash
uv run --locked pytest tests/test_platform_data_paths.py tests/test_runtime_data_migration.py tests/test_task_status_backfill.py tests/test_platform_storage.py tests/test_review_bot.py tests/test_admin_services.py -v
```

真实迁移必须先运行 `uv run --locked python scripts/migrate_runtime_data.py` 预演，再加 `--apply` 执行；迁移工具只复制和校验，不自动删除旧数据。

历史任务状态补齐必须先运行 `uv run --locked python scripts/backfill_task_status.py` 预演，再加 `--apply` 执行。重点确认重复执行时 `planned` 和 `written` 均为 0，且状态文件不含用户材料正文。

修改审核日志切分、文件命名或日志配置时，至少运行：

```bash
uv run --locked pytest tests/test_bot_logging.py tests/test_review_bot.py -v
```

重点验证按天切换、单文件大小分片、系统日志去重和用户文件句柄上限。

修改本机项目控制台时，至少运行：

```bash
uv run --locked pytest tests/test_admin_services.py tests/test_admin_server.py -v
uv run --locked python scripts/project_docs.py check
```

重点验证五层架构完整性、关系端点全部属于已登记能力、功能状态随 TODO/Skill/代码证据变化、Bot 心跳降级、本地 `vis-network` 版本和许可证存在、页面不依赖 CDN、写作统计只读 `status.json`、审核统计兼容 `meta.md`/`meta.json`/`output/report.md`、Git 查询固定且只读、动态文字全部转义，以及页面不展示密钥或材料正文。涉及关系图布局时，还要用浏览器分别检查桌面和手机尺寸，确认画布非空、筛选和双视图切换正常、文字与控件不重叠。

### 1. 平台单元测试

验证底座区：

```bash
uv run --locked pytest tests/test_platform_registry.py tests/test_platform_router.py tests/test_platform_tools.py tests/test_platform_builtin_tools.py tests/test_platform_file_readers.py tests/test_platform_document_service.py tests/test_platform_data_paths.py tests/test_platform_pydantic_runtime.py tests/test_platform_runtime.py tests/test_platform_demo.py tests/test_platform_wecom_gateway.py tests/test_platform_storage.py tests/test_platform_conversation.py tests/test_platform_intent.py tests/test_platform_chat_log.py tests/test_platform_identity.py tests/test_platform_app.py tests/test_platform_cli.py tests/test_user_registry.py tests/test_ops_events.py tests/test_ops_report.py tests/test_ops_notifier.py tests/test_ops_config.py tests/test_ops_bot_state.py tests/test_ops_heartbeat.py -v
```

### 2. Skill 测试

验证具体业务能力：

```bash
uv run --locked pytest tests/test_direct_report_workflow.py tests/test_direct_report_guardrails.py tests/test_direct_report_policy_gate.py tests/test_direct_report_quality_regression.py tests/test_writer_prompt_rules.py tests/test_brief_writer_workflows.py tests/test_research_synthesis_workflow.py tests/test_installed_writer_skills.py tests/test_rewrite_workflow.py tests/test_revision_support.py -v
```

后续新增 skill 后，新增对应测试。

### 3. 旧功能保护测试

验证旧审核 Bot 没被影响：

```bash
uv run --locked pytest tests/test_review_bot.py tests/test_official_format_review.py -v
```

其中 `test_official_format_review.py` 验证独立格式审核不依赖 Word 样式名称、实际格式规则能识别典型错误，并能生成带定位批注的返回文档。

### 4. LLM 端到端测试

审核端到端测试：

```bash
uv run --locked python tests/test_reviewer.py
```

注意：这个命令依赖真实模型和网络，可能因为 `Connection error` 失败。报告时要说明失败原因。

### 5. 真实 demo 测试

验证本地真实链路：

```bash
uv run --locked python -m app.platform.demo "帮我根据这个链接写直报：https://..."
```

需要：

- `.env` 配置模型 API
- 网络访问网页
- 网络访问模型服务

如果沙箱 DNS 失败，需要在允许的情况下提升权限运行。

### 6. 直报 Bot 生产级测试

当使用现有直报 Bot 作为入口、`direct_report` 作为 skill 时，按以下文档执行：

```text
docs/development/direct-report-production-test.md
```

自动化测试至少运行：

```bash
uv run --locked pytest tests/test_writing_platform_bot.py tests/test_writing_portal.py tests/test_platform_document_service.py tests/test_platform_app.py tests/test_platform_wecom_gateway.py tests/test_direct_report_workflow.py tests/test_brief_writer_workflows.py -v
uv run --locked python tests/test_review_bot.py
```

### 7. 运维 Bot 生产检查

运维 Bot 是独立长期进程，用于异常通知和工作日日报。

实时通知只处理当天事件；前一工作日事件只进入工作日报。同一天内来源、级别、主题和详情完全相同的事件只通知一次。相关状态测试必须覆盖“周一不补发上周五旧事件”和“同一天相同连接异常不重复通知”，防止重启、数据迁移或网络抖动后告警刷屏。

配置检查：

```bash
uv run --locked python -m app.platform.ops.bot --check-config
```

启动：

```bash
uv run --locked python -m app.platform.ops.bot
```

相关自动化测试：

```bash
uv run --locked pytest tests/test_ops_events.py tests/test_ops_report.py tests/test_ops_notifier.py tests/test_ops_config.py tests/test_ops_bot_state.py tests/test_ops_heartbeat.py tests/test_writing_platform_bot.py tests/test_writing_portal.py tests/test_notification.py -v
```

## 每类变更要跑什么

### 修改 skill 文档或 prompt

至少跑：

```bash
uv run --locked pytest tests/test_direct_report_workflow.py tests/test_direct_report_guardrails.py tests/test_direct_report_policy_gate.py tests/test_brief_quality.py tests/test_writer_prompt_rules.py tests/test_brief_writer_workflows.py tests/test_platform_pydantic_runtime.py -v
```

如果影响真实效果，再跑 demo。

### 修改直报质量规则或回归样本

至少跑：

```bash
uv run --locked pytest tests/test_direct_report_guardrails.py tests/test_direct_report_policy_gate.py tests/test_direct_report_quality_regression.py tests/test_direct_report_workflow.py -v
```

如果是为了评估真实成稿质量，还要按 `docs/development/direct-report-quality-regression-v1.md` 跑固定样本，并把 `job_id` 和人工观察写回文档。

直报质量规则里有两类测试：

- 自动化测试：检查称谓、标题、字数、小标题、政策挂接闸门等稳定规则。
- 人工回归：用固定 4 个链接看初稿是否像直报、政策是否自然、案件细节是否压缩得当。

只改 prompt 或规则时，可以先跑离线自动化测试，不需要每次都连接企业微信 Bot。准备上线前再做真实 Bot 手动验证。

### 修改综合调研整合 skill

至少跑：

```bash
uv run --locked pytest tests/test_research_synthesis_workflow.py tests/test_platform_registry.py tests/test_platform_router.py tests/test_platform_pydantic_runtime.py tests/test_platform_runtime.py tests/test_platform_app.py tests/test_writing_platform_bot.py tests/test_platform_document_service.py -v
```

重点确认：提纲能唯一识别；不按上传顺序猜提纲；只有提纲而没有部门素材时追问；提纲和部门素材角色明确进入模型上下文；文件读取失败时不静默生成；现有 `writer1` / `writer2` 路由和多文件组装不受影响。真实上线前还要用经授权脱敏样本人工检查提纲章节保留、材料归位、重复合并、缺口标记和冲突标记。

写作入口文件数量上限当前为 10 份，总大小上限仍为 20MB。测试必须覆盖前 10 份可接收、第 11 份被拒绝，以及自定义更小上限仍生效。

### 修改路由

至少跑：

```bash
uv run --locked pytest tests/test_platform_router.py tests/test_platform_runtime.py tests/test_platform_demo.py tests/test_platform_conversation.py tests/test_platform_intent.py tests/test_platform_app.py -v
```

### 修改多轮改稿或会话状态

至少跑：

```bash
uv run --locked pytest tests/test_platform_conversation.py tests/test_platform_intent.py tests/test_platform_chat_log.py tests/test_platform_app.py tests/test_platform_storage.py tests/test_platform_router.py tests/test_writing_platform_bot.py tests/test_direct_report_workflow.py tests/test_brief_writer_workflows.py -v
```

重点确认：

- 直报、单素材简报、多素材简报共用底座会话能力。
- 用户换说法仍能改当前稿。
- 中间一次追问或失败不会覆盖当前稿。
- 用户发新链接、新文件或明确要求新写时，不会误入改稿。
- 用户说“回到上一版”“按第一版再改”时，能从版本链选择正确稿件。
- 每轮写作对话能落入开发期日志，异常失败也能记录错误摘要。

### 修改工具授权

至少跑：

```bash
uv run --locked pytest tests/test_platform_tools.py tests/test_platform_builtin_tools.py tests/test_platform_file_readers.py tests/test_platform_document_service.py -v
```

### 修改文件接收或统一文档服务

至少跑：

```bash
uv run --locked pytest tests/test_platform_document_service.py tests/test_platform_file_readers.py tests/test_platform_data_paths.py tests/test_platform_app.py tests/test_writing_platform_bot.py tests/test_writing_portal.py tests/test_direct_report_workflow.py tests/test_brief_writer_workflows.py -v
```

重点确认：格式伪造、路径越界、异常压缩包和超限文件被拦截；DOCX/PDF/PPTX 完整解析结果写入任务 `work/`；长材料不会只取开头；扫描 PDF 明确记录待 OCR；待组装文件在 Bot 重启后可恢复且提交后会清理。真实文件和中间产物必须留在 `M-Agent-Files/`，不能进入仓库。

审核 Bot 的格式指令衔接或多文件联合审核另需跑：

```bash
uv run --locked pytest tests/test_review_intake.py tests/test_review_multi_file.py tests/test_review_bot.py tests/test_error_marker.py tests/test_official_format_review.py -v
```

重点确认：格式审核支持“指令在前”和“文件在前”，且单文件内容审核启动后仍可追加格式审核；文件到达即确认，1 份自动走单文件、连续 2 至 5 份无需前后指令自动走联合审核；联合审核不按上传顺序默认正文，优先结合文件名和正文引用识别，歧义时要求用户指定；待组装文件按入口和用户隔离并可从磁盘恢复；跨文件模型意见必须同时命中两份文件的真实原文；任务归档、摘要和标注文档均保留已确认主文件信息。

### 修改 Pydantic AI 执行层

至少跑：

```bash
uv run --locked pytest tests/test_platform_pydantic_runtime.py tests/test_platform_demo.py -v
```

### 修改企业微信入口

至少跑：

```bash
uv run --locked pytest tests/test_platform_registry.py tests/test_platform_router.py tests/test_platform_runtime.py tests/test_platform_demo.py tests/test_platform_wecom_gateway.py tests/test_platform_app.py tests/test_platform_storage.py tests/test_platform_identity.py tests/test_user_registry.py tests/test_writing_platform_bot.py tests/test_writing_portal.py -v
uv run --locked python tests/test_review_bot.py
```

并进行企业微信手动验证。

如果只改了 `app/platform/gateway/wecom.py` 这种纯消息处理核心，还没有接真实 SDK，先跑单元测试即可；接入真实 SDK 后再进行企业微信手动验证。

## 命令说明

统一使用：

```bash
uv run --locked pytest ...
```

`uv run --locked` 会使用项目 `.venv` 并拒绝锁文件过期。直接使用系统 `python` 或全局 `pytest` 可能落到另一套解释器，导致依赖缺失、版本漂移或项目之间互相影响。

## 交付说明必须包含

每次完成后，回复用户时至少说明：

1. 改了什么。
2. 没改什么。
3. 跑了哪些测试。
4. 哪些测试失败以及原因。
5. 下一步建议。

## 不允许交付的情况

- 新代码没有测试。
- 没跑相关测试。
- 改动绕过 `ToolGateway`。
- skill 直接读取 `.env`。
- 新增功能影响旧 Bot 但未说明。
- 真实密钥、用户材料、日志被写入仓库。
- 行为代码或配置已变化，但对应架构、模块、skill、TODO 或测试文档没有同步。
- `uv run --locked python scripts/project_docs.py check` 未通过。
- `STATUS-REPORT.md`、真实用户 ID 或本机绝对路径进入暂存区。
