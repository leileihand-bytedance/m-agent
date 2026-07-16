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
2. 禁止直接运行 `git push`，统一执行 `uv run --locked python scripts/project_docs.py push --summary "完成了什么功能" --impact "实际改变了什么能力" --next-step "当前边界或下一步"`。命令会先获取远端并阻止分叉。
3. 受管推送成功后，自动向本机 `STATUS-REPORT.md` 写入一条开发日志，主体是完成功能、能力变化和下一步；Git 哈希只用于追溯。失败时不写成功记录。
4. 推送后再次运行 `uv run --locked python scripts/project_docs.py check-sync`，输出必须为“本地分支与远端已同步”；禁止强推覆盖远端历史。
5. 网络、凭据或分叉阻塞时，在交付说明中明确写出，不得省略。

post-commit hook 只在存在未推送提交时告警，不再写开发日志；pre-push hook 会执行核心文档检查，并拒绝绕过受管命令直接推送。钩子不会自动强推或在后台静默访问网络。

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
uv run --locked pytest tests/test_platform_registry.py tests/test_platform_router.py tests/test_platform_tools.py tests/test_platform_builtin_tools.py tests/test_platform_file_readers.py tests/test_platform_document_service.py tests/test_platform_document_enrichment.py tests/test_platform_data_paths.py tests/test_platform_intake.py tests/test_platform_intake_protocol.py tests/test_platform_task_execution.py tests/test_platform_task_status.py tests/test_platform_attachment_delivery.py tests/test_platform_pydantic_runtime.py tests/test_platform_runtime.py tests/test_platform_demo.py tests/test_platform_wecom_gateway.py tests/test_platform_storage.py tests/test_platform_conversation.py tests/test_platform_intent.py tests/test_platform_chat_log.py tests/test_platform_identity.py tests/test_platform_app.py tests/test_platform_cli.py tests/test_user_registry.py tests/test_ops_events.py tests/test_ops_report.py tests/test_ops_notifier.py tests/test_ops_config.py tests/test_ops_bot_state.py tests/test_ops_heartbeat.py -v
```

修改公共任务组装内核时，至少额外运行：

```bash
uv run --locked pytest tests/test_platform_intake.py tests/test_platform_intake_protocol.py tests/test_review_intake.py tests/test_writing_platform_bot.py tests/test_platform_app.py -v
```

重点验证入口和用户隔离、状态原子写入、重启恢复、TTL 清理、目录外文件引用拦截、数量/总大小限制、公共动作/材料/提交模型，以及写作和审核原有业务状态机行为不变。

修改持久化后台任务或附件交付时，至少额外运行：

```bash
uv run --locked pytest tests/test_platform_task_execution.py tests/test_platform_task_status.py tests/test_platform_attachment_delivery.py tests/test_writing_platform_bot.py tests/test_review_bot.py -v
```

单项审核已接入持久任务后，还要运行：

```bash
uv run --locked pytest tests/test_review_html.py tests/test_review_task_execution.py tests/test_review_general.py tests/test_review_general_rules.py tests/test_review_intake.py tests/test_review_bot.py -v
```

修改 PPTX 低级错误审核时，还要运行：

```bash
uv run --locked pytest tests/test_review_ppt_extractor.py tests/test_review_ppt_rules.py tests/test_review_ppt_reviewer.py tests/test_review_ppt_formatter.py tests/test_review_ppt_bot.py tests/test_review_task_execution.py tests/test_platform_document_service.py tests/test_review_bot.py -v
```

重点验证：只提取任务目录内 `.pptx` 的可编辑文本框、表格和可读图表内容；图片文字和备注不进入审核；序号、占位符和标点规则不跨对象拼接；模型伪造页码、对象或原文会被丢弃；不同时间或不同指标口径不报跨页矛盾；PPT 包不导入其他业务审核引擎；结果不含修改建议，长结果按编号分段；任务完成后不重复审核或发送。自动化通过后仍要用经授权真实 PPT 人工核对误报、漏报、页码、图表读取和企业微信展示。

直报、`writer1`、`writer2` 接入写作持久任务后，还要运行：

```bash
uv run --locked pytest tests/test_writing_task_execution.py tests/test_writing_platform_bot.py tests/test_platform_app.py tests/test_platform_conversation.py tests/test_direct_report_workflow.py tests/test_brief_writer_workflows.py -v
```

重点验证重复消息幂等、全局/单用户/成本并发、租约和 fencing token、心跳失效、重复取消、进程恢复、状态版本防乱序、凭据和文字正文不入库、任务目录和符号链接校验、动态超时、完整重试、约 50MB SDK 上限、“处理编号”兜底和运维事件脱敏；审核专项还要覆盖七类单项任务分派、处理与发送检查点、已完成任务不重复审核、单段/多段队列结果只发送一次、发送状态不确定时停止重发、worker 异常告警与自恢复、Word 单文件后追加格式审核，以及损坏检查点的安全失败。HTML 专项还要覆盖静态可见文字、显式及原生隐藏内容、表格顺序、真实 `meta` 编码声明、短文数据一致性、正文不进 SQLite 和不生成标记文件。PPT 专项按上文命令覆盖独立规则、双边证据、无建议格式和多段交付。执行器内核测试通过不代表其他具体 Bot 已切流，真实启用前仍需逐个验证 handler 可恢复性和外部发送幂等。

### 持久任务生产接入验收

持久化执行器按任务类型分批接入。当前已接入纯文字、单个通用 Word、内参、半月报、公文格式、单个静态 HTML 和单份 PPTX 七类单项审核，以及直报、`writer1`、`writer2`；审核和写作使用独立 SQLite。`research_synthesis` 和多文件联合审核仍走旧路径，不能因为共用入口就视为已切流。

每个任务类型接入时都必须完成：

1. 自动化验证同一企业微信消息重复投递只创建和执行一次任务。
2. 自动化验证全局、单用户和成本并发限制，排队期间 Bot 仍可受理其他消息。
3. 人工在真实企业微信中确认审核文件到达后只收到一次即时收件提示，单文件入队后不再追加队列或审核类型；分别用“格式审核”“帮我查一下格式”验证文件前置格式意图，再用“也做一下文字审核”“再做一下内容审核”验证文件后操作指令，确认这些指令本身都不建立纯文字审核任务。纯文字、HTML 和 PPTX 审核完成后必须通过主动 Markdown 消息收到结果；HTML/PPTX 结果只发消息、不回传标记文件。PPTX 还需确认不进入 Word 归集、不出现修改建议，旧版 `.ppt` 明确提示另存为 `.pptx`。正常提示不包含后台任务编号；模拟需要人工介入的异常时，用户提示包含“处理编号”。
4. 在生成过程中重启对应 Bot，确认任务恢复或安全失败，不会永久停在“处理中”。
5. 分别验证用户取消、模型或网络失败、正文已生成但附件发送失败，以及运维 Bot 告警。
6. 核对外部发送幂等：已经成功发送的结果不会因重试或重启再次发送，失败交付也不会触发重新生成两份稿件。

代码和离线测试完成后可标为“已接入、验收中”；只有以上真实验收完成，才能在文档和控制台中标为“稳定运行”。

### 2. Skill 测试

验证具体业务能力：

```bash
uv run --locked pytest tests/test_direct_report_workflow.py tests/test_direct_report_guardrails.py tests/test_direct_report_policy_gate.py tests/test_direct_report_quality_regression.py tests/test_writer_prompt_rules.py tests/test_brief_writer_workflows.py tests/test_research_synthesis_workflow.py tests/test_installed_writer_skills.py tests/test_rewrite_workflow.py tests/test_revision_support.py -v
```

后续新增 skill 后，新增对应测试。

### 3. 旧功能保护测试

验证旧审核 Bot 没被影响：

```bash
uv run --locked pytest tests/test_review_bot.py tests/test_official_format_review.py tests/test_review_ppt_bot.py -v
```

其中 `test_official_format_review.py` 验证独立格式审核不依赖 Word 样式名称、实际格式规则能识别典型错误，并能生成带定位批注的返回文档。

### 4. LLM 端到端测试

审核端到端测试：

```bash
uv run --locked python tests/test_reviewer.py
```

注意：这个命令依赖真实模型和网络，可能因为 `Connection error` 失败。报告时要说明失败原因。

通用审核真实文件质量基线：

```bash
uv run --locked pytest tests/test_review_quality_evaluation.py tests/test_review_main_flow_optimization.py tests/test_review_general.py tests/test_review_general_rules.py tests/test_error_marker.py -v
uv run --locked python scripts/review_quality.py run --limit 5 --run-id YYYYMMDD-baseline-v1
```

第二条命令会把经授权历史文件重新发送到当前审核模型。执行前必须向用户说明外部模型数据传输风险并取得本次明确授权；真实文件、原文、标注文档和人工评分只保存在 `M-Agent-Files/evaluations/review/<run_id>/`，不能进入 Git。

运行中断或样本被标记为 `failed` / `partial_failed` 时，使用同一命令追加 `--resume`。恢复会校验样本编号和文件 SHA，只跳过完整样本；不能用局部漏扫结果冒充完整基线。评分时逐条判断有效性、标注位置和建议质量，并在漏报表补充可搜索原文。误报率、定位率和漏报数必须基于人工评分，不能直接把模型候选数当成质量结论。

2026-07-15 首轮 5 份真实文件的人工填写结果为 34 条有效、11 条误报、1 条未评分，46 条标注位置均为准确；建议质量、重要程度和实际漏报尚未填写完整。8 条英文所有格弯撇号误报已用真实原文复核并补成离线回归，修复后均不再触发 `quote-pair`，真正缺失的中文引号仍会触发。后续基线比较必须把同根因重复误报单独归类，不能只看误报总条数。

同一批文件的确定性误报修复优先回放已保存的 `result.json` 和原文段落，不要求再次产生模型费用。当前已固定三类离线回归：明确写成“X 应为 Y”时 X 必须存在于原文；短小标签形态的纯英文段落不报正文残缺；标点后空格必须核对 DOCX 原始字符，并精确标记“标点 + 空格”。只有修改涉及模型召回、分块或通篇逻辑行为时，才需要重新运行真实模型基线。

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

重点确认：自然的“调研材料汇总”说法能进入正确流程；明确总提纲文件名和正文答复特征能排除部门反馈；证据不足时不按上传顺序猜提纲；追问期间原文件保留，用户回答后无需重新上传或再次发送“开始写”即可续跑；只有提纲而没有部门素材时追问；提纲和部门素材角色、规范化来源标签明确进入模型上下文；文件读取失败时不静默生成；第一阶段同时生成提纲类型、覆盖方式和证据台账，第二阶段只按验证后的台账成稿；问卷型逐项覆盖并补回遗漏必答主题，政策目录型只保留有可用证据的选定主题并过滤未选主题；`derived` 必须保留明确算式，`image_candidate`、`external_missing` 和无法匹配本次材料的来源必须不可用；台账外数字和缺失来源会标记人工核对；一级、二级标题统一为“一、”“（一）”，明确三级短标题允许保留 `1.`；来源长文件名被清理且多个来源合并到事实段末；图片提醒按部门核对总数并合并连续重复项；Word 不嵌入源图片，含开头结尾待补备注并通过现有公文格式检查；企业微信成功回传文件；现有 `writer1` / `writer2` 路由和多文件组装不受影响。真实上线前还要继续用经授权案例人工检查提纲分类、选材合理性、跨部门归并、安全合计、缺口和冲突标记，并逐页渲染检查 Word 版式；真实案例原文和数据不得进入自动化测试仓库。

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
uv run --locked pytest tests/test_platform_intake.py tests/test_platform_intake_protocol.py tests/test_platform_document_service.py tests/test_platform_document_enrichment.py tests/test_platform_file_readers.py tests/test_platform_data_paths.py tests/test_platform_app.py tests/test_writing_platform_bot.py tests/test_writing_portal.py tests/test_direct_report_workflow.py tests/test_brief_writer_workflows.py -v
```

重点确认：格式伪造、路径越界、异常压缩包和超限文件被拦截；DOCX/PDF/PPTX 完整解析结果写入任务 `work/`；长材料不会只取开头；扫描 PDF 只 OCR 标记页，失败仍保留待 OCR 位置；PPT 转换不复用旧 PDF；页面输出不无限累积；总时间、页数、像素和容量限制生效；待组装文件在 Bot 重启后可恢复且提交后会清理。真实文件和中间产物必须留在 `M-Agent-Files/`，不能进入仓库。

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
