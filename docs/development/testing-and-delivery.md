# M-Agent 测试和交付规范

## 基本原则

任何开发或修改都必须经过测试验证。不能只说“应该可以”。

核心文档与代码同属交付物。AI 工具必须在计划阶段列出文档影响，完成后运行：

```bash
uv run --locked python scripts/project_docs.py check
```

首次克隆运行 `uv run --locked python scripts/project_docs.py install-hooks`。提交前 hook 会读取暂存区版本，按模块检查代码、依赖、hooks、配置与对应核心文档；对 `.docx`、`.pdf`、`.pptx`、图片等二进制资产文件仅校验是否暂存，不检查文本内容。无关计划文档不能代替模块文档。根目录 `STATUS-REPORT.md` 只做本机索引，完整开发日志按月写入 `M-Agent-Files/runtime/development-logs/`；权限文件 `config/platform-policy.yaml` 同样只保留在本机。这些本机文件都不进入 Git。

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

1. 从干净且已同步的 `main` 使用 `start-task` 创建短期任务工作区；`main` 不直接提交。
2. 每个可测试的逻辑节点在 `codex/`、`claude/` 或 `hotfix/` 任务分支及时提交；同时最多 2 个活跃任务。
3. 完成后从 `main` 使用 `finish-task`。命令只做快进合并，随后受管推送 `main`；任务分支不得推送到远端。
4. 受管推送成功后，自动向当月开发日志写入一条记录，并刷新根目录索引。失败时不写成功记录，也不清理任务工作区。
5. 推送后再次运行 `uv run --locked python scripts/project_docs.py check-sync`，输出必须为“本地分支与远端已同步”；禁止强推覆盖远端历史。
6. 网络、凭据、冲突或分叉阻塞时，在交付说明中明确写出，不得省略。

post-commit hook 只在存在未推送提交时告警，不再写开发日志；pre-push hook 会执行核心文档检查，并拒绝绕过受管命令直接推送。钩子不会自动强推或在后台静默访问网络。

## 生产与测试 Bot 分离

测试分三层，不得混用：

| 层级 | 运行位置 | 企业微信和数据 |
|---|---|---|
| 离线自动化 | 任务分支 | 模拟对象和临时目录，不连接真实 Bot |
| 测试联调 | 任务分支 | 专用测试 Bot + `M_AGENT_TEST_DATA_DIR` |
| 生产验收 | `main` | 生产 Bot + `M_AGENT_DATA_DIR`，只在合并后执行 |

任务分支禁止使用生产 Bot。测试联调配置必须包含 `M_AGENT_RUNTIME_ENV=test`、对应入口的 `M_AGENT_TEST_*_BOT_ID/SECRET` 和独立 `M_AGENT_TEST_DATA_DIR`。缺少任一项、测试目录与生产目录相同、或任何任务/会话/队列/日志路径越过测试根目录时，Bot 在连接企业微信前停止。测试模式绝不回退生产凭据。

生产配置默认 `M_AGENT_RUNTIME_ENV=production`。生产 Bot 若从非 `main` 分支启动，同样在连接前停止。`--check-config` 也执行这套校验，用它确认当前分支、运行环境和数据根目录无误。

修改运行环境守卫、Bot 配置或数据路径时，至少运行：

```bash
uv run --locked pytest tests/test_platform_runtime_environment.py tests/test_platform_data_paths.py tests/test_writing_platform_bot.py tests/test_review_bot.py tests/test_rewrite_bot.py tests/test_ops_config.py -v
```

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

重点验证五层架构完整性、关系端点全部属于已登记能力、仓库内每个已安装 Skill 均映射到架构节点、功能状态随 TODO/Skill/代码证据变化、写作/审核/材料润色/运维四个 Bot 的心跳降级、本地 `vis-network` 版本和许可证存在、页面不依赖 CDN、写作统计只读 `status.json`、审核统计兼容 `meta.md`/`meta.json`/`output/report.md`、Git 查询固定且只读、动态文字全部转义，以及页面不展示密钥或材料正文。涉及关系图布局时，还要用浏览器分别检查桌面和手机尺寸，确认画布非空、筛选和双视图切换正常、文字与控件不重叠。

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

修改审核共享核心、规则目录或静态profile时，还要运行：

```bash
uv run --locked pytest tests/test_review_shared_core.py tests/test_review_general.py tests/test_review_general_rules.py tests/test_review_html.py tests/test_review_halfmonthly.py tests/test_review_multi_file.py tests/test_official_format_review.py tests/test_review_ppt_rules.py tests/test_review_ppt_reviewer.py tests/test_review_task_execution.py tests/test_review_bot.py -v
```

重点验证旧 `Finding` 和输出兼容、专属规则隔离、单点与双边证据、相同输入的模型调用预算、失败和降级指标，以及未迁移审核器不受影响。任务分支不读取生产 `.env`；真实模型测试只能按生产与测试Bot隔离要求单独执行。

修改 PPTX 低级错误审核时，还要运行：

```bash
uv run --locked pytest tests/test_review_ppt_extractor.py tests/test_review_ppt_rules.py tests/test_review_ppt_reviewer.py tests/test_review_ppt_formatter.py tests/test_review_ppt_bot.py tests/test_review_task_execution.py tests/test_platform_document_service.py tests/test_review_bot.py -v
```

重点验证：只提取任务目录内 `.pptx` 的可编辑文本框、表格和可读图表内容；图片文字和备注不进入审核；序号按对象、段落层级和连续列表分组，小数不当作序号；模型伪造页码、对象或原文会被丢弃；即使模型声称同口径，明确不同年份、时间范围、单位或目标/实际状态也不报跨页矛盾；同一首侧证据与不同第二侧证据不会被错误合并；PPT 包不导入其他业务审核引擎；用户可见问题说明不透传任何模型自由描述，“建议修改、推荐改成、最好写成、宜改成、需要改成、可考虑”等表达均不能进入结果；图表读取失败会显示页码提示；长结果按编号分段；常规语言批次不超过约 3000 字符（单个超长对象除外），`stop_reason=max_tokens` 会触发按对象拆半和结果合并，单对象仍超限时必须停止；普通网络或 JSON 错误不能误进拆分分支；处理中断后已完成顶层模型批次不重复调用，发送完成后不重复发送。首份经授权真实 PPT 已完成端到端实测；后续每轮仍要人工核对误报、漏报、页码、图表读取和企业微信展示，不能只依据自动化通过下结论。

直报、`writer1`、`writer2` 接入写作持久任务后，还要运行：

```bash
uv run --locked pytest tests/test_writing_task_execution.py tests/test_writing_platform_bot.py tests/test_platform_app.py tests/test_platform_conversation.py tests/test_direct_report_workflow.py tests/test_brief_writer_workflows.py -v
```

重点验证重复消息幂等、全局/单用户/成本并发、租约和 fencing token、心跳失效、重复取消、进程恢复、状态版本防乱序、凭据和文字正文不入库、任务目录和符号链接校验、动态超时、完整重试、约 50MB SDK 上限、“处理编号”兜底和运维事件脱敏；审核专项还要覆盖七类单项任务分派、处理与发送检查点、已完成任务不重复审核、单段/多段队列结果只发送一次、发送状态不确定时停止重发、worker 异常告警与自恢复、Word 单文件后追加格式审核，以及损坏检查点的安全失败。HTML 专项还要覆盖静态可见文字、显式及原生隐藏内容、表格顺序、真实 `meta` 编码声明、可见/隐藏/嵌套/未闭合 slide 页码映射、普通 HTML 段落兜底、消息与报告定位、短文数据一致性、正文不进 SQLite 和不生成标记文件。PPT 专项按上文命令覆盖独立规则、双边证据、无建议格式、名称单边猜测与相同脚注拦截、同名异写保留、反向双边去重、模型输出超限识别、按对象拆半合并、单对象停止条件和多段交付。执行器内核测试通过不代表其他具体 Bot 已切流，真实启用前仍需逐个验证 handler 可恢复性和外部发送幂等。

修改独立材料润色 Bot 或入口级 Skill 白名单时，还要运行：

```bash
uv run --locked pytest tests/test_rewrite_bot.py tests/test_rewrite_workflow.py tests/test_platform_registry.py tests/test_platform_router.py tests/test_platform_app.py -v
uv run --locked python -m app.rewrite_bot --check-config
```

重点验证入口注册表中只有 `rewrite`，直报、简报等请求不能被执行；文件和网页链接被明确拒绝；只有原文时不调用模型并追问修改方向，用户补充要求后按“原文 + 要求”执行，Bot 重启后可恢复待确认原文，成功或取消后清理；同条消息含原文和要求、上一版续改仍可直接处理；任务、待确认原文和会话目录与原写作 Bot 隔离；配置检查只显示遮罩后的 Bot ID，不输出 Secret。

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
uv run --locked pytest tests/test_direct_report_workflow.py tests/test_direct_report_guardrails.py tests/test_direct_report_policy_gate.py tests/test_direct_report_quality_regression.py tests/test_writer_prompt_rules.py tests/test_brief_writer_workflows.py tests/test_research_synthesis_workflow.py tests/test_shenyinxie_news_*.py tests/test_internal_weekly_*.py tests/test_installed_writer_skills.py tests/test_rewrite_workflow.py tests/test_revision_support.py -v
```

深银协动态还要重点验证：明确指定月份及上/下半月时使用对应发布日期范围；未明确时在任何搜索调用前追问；后台恢复追问后仍保持 `shenyinxie_news` 意图并合并用户回答；查询包含精确日期和成果主题；最终入选仍以原文页面发布日期、白名单和正文核验为准。人民日报旧版页面回归必须覆盖固定错误 `publishdate`，只有页面内期号路径双重印证时才采用真实期号日期；同稿去重必须忽略媒体站点追加的短标题尾缀。

内参周报还要重点验证：出版日固定为周一、统计期固定为上一自然周；周一 15:30 前不调用搜索；五类板块顺序和分类规则独立于审核模块；资本市场综述固定为市场观察首项，完整包含 `weekly_a`、`monday_a`、`weekly_hk`、`weekly_us` 四组必填指数，涨跌幅只由代码计算；研报摘录逐字存在于来源页面；核对稿和溯源清单的 `draft_version` 一致，每个来源都有 URL、证据原句和正文哈希；第一阶段不得产生 `.docx`。真实联网验收要分别记录普通内容、行情和研报来源的公开标题与域名，不记录抓取全文；公开页面无法稳定返回历史行情时，应保留待核事项，不能把模拟测试通过解释为真实数据源已经稳定。

后续新增 skill 后，新增对应测试。

DeepSeek 原生联网搜索适配还需运行：

```bash
uv run --locked pytest tests/test_platform_builtin_tools.py tests/test_platform_app.py -k "search or build_platform_tools" -v
```

该组测试使用模拟响应固定 `/anthropic/v1/messages`、`web_search_20250305`、模型名透传和搜索结果结构，不消耗真实 API；真实联调单独执行，日志不得输出密钥或全文。

深银协专项回归还必须覆盖：标题含“微众”的多银行综合稿不能直接作为专题全文；完整专题稿不足时才启用扩展白名单；模型只可返回结构化全文/摘编/拒绝判断；摘编段落必须按原顺序逐字来自原文；Word 中摘编稿必须显示原报道标题、原文链接和摘编说明。模板回归还要校验 A4 页面和页边距、标题/动态标题/正文角色样式、逐段正文、未使用块删除、网页页头噪声清理、中文发布日期、外部超链接关系以及无真实案例正文残留；结构测试通过后必须逐页渲染检查重叠、截断和异常空页。真实联调只记录标题、域名和内容模式，不记录抓取正文。

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

当前通用审核质量基线已经冻结，后续比较必须把同根因重复误报单独归类，不能只看误报总条数。漏报补充为空只能表述为“该批次无已确认补录漏报”，不能声称漏报率为零；未评分项和未填写字段必须在报告中如实说明。

真实材料暴露的错误应转成脱敏等价回归或已保存结果回放，不能把真实内容写入仓库。错别字回归必须同时验证上下文误报被删除、明确错误经复核后保留、复核不可用时高风险意见不交付；真实模型波动与离线确定性回归必须分开报告。

PPT 名称问题必须提供两处真实、不同且归一后同名的证据。单边名称、不同名称、相同脚注、脚注标记兼容变体、常见注释语句和反向重复候选不得交付；含符号结尾的真实名称仍应保留。模型候选修复优先使用脱敏样本或已保存结果回放验证。

同一批文件的确定性误报修复优先回放已保存的 `result.json` 和原文段落，不要求再次产生模型费用。当前已固定四类离线回归：明确写成“X 应为 Y”时 X 必须存在于原文；短小标签形态的纯英文段落不报正文残缺；标点后空格必须核对 DOCX 原始字符，并精确标记“标点 + 空格”；错别字候选必须结合完整句法和前后文复核，不能仅凭相邻短片段命中。只有修改涉及模型召回、分块或通篇逻辑行为时，才需要重新运行真实模型基线。

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
docs/capabilities/direct-report/production-test.md
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

如果是为了评估真实成稿质量，还要按 `docs/capabilities/direct-report/quality-regression.md` 跑固定样本。真实任务编号和逐篇观察只保存在 `M-Agent-Files/evaluations/` 或任务目录，当前文档只保留可泛化规则。

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

重点确认：格式伪造、路径越界、异常压缩包、真实 VBA 项目、VBA 关系、其他 `macroEnabled` 部件和超限文件被拦截；主部件声明必须使用标准命名空间、规范路径且保持唯一。只允许 PPTX 中 `ppt/embeddings/*.xlsb` 使用标准 XLSB 类型例外；PPT 独立审核不会单独解析该工作簿或把其内容送入模型。DOCX/PDF/PPTX 完整解析结果写入任务 `work/`；长材料不会只取开头；扫描 PDF 只 OCR 标记页，失败仍保留待 OCR 位置；PPT 转换不复用旧 PDF；页面输出不无限累积；总时间、页数、像素和容量限制生效；待组装文件在 Bot 重启后可恢复且提交后会清理。真实文件和中间产物必须留在 `M-Agent-Files/`，不能进入仓库。

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
- `STATUS-REPORT.md`、月度开发日志、真实用户 ID 或本机绝对路径进入暂存区。
