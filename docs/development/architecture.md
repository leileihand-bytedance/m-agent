# M-Agent 整体架构

## 当前目标

M-Agent 的目标是让用户通过一个企业微信入口，自然表达需求，然后系统在已授权能力范围内自动调用对应 skill。

示例：

```text
用户：帮我根据这个链接写一篇直报
系统：识别为 direct_report -> 读取链接 -> 生成直报初稿

用户：这篇改短一点
系统：识别为上一稿改稿 -> 沿用 direct_report 上次输出 -> 修改上一稿

用户：看看这个文档有没有低级错误
系统：识别为 review -> 调用审核 skill
```

## Python 运行基线

M-Agent 的底座、skills、写作 Bot、审核 Bot、运维 Bot 和管理后台共用同一套项目环境：

```text
uv 管理的 CPython 3.13.14
  -> 项目根目录 .venv
  -> pyproject.toml 声明直接依赖
  -> uv.lock 固定全部依赖版本
  -> uv run --locked 启动所有入口
```

这套环境与终端默认的 pyenv、Homebrew Python 和 macOS Python 隔离。`.env` 仍只保存模型和 Bot 配置，与存放 Python 依赖的 `.venv` 无关。Pydantic AI 的 Anthropic 和 OpenAI 兼容通道都已作为正式依赖声明，避免依赖全局环境中碰巧存在的包。

## 架构总览

```text
企业微信 / 本地 demo
  ↓
app/platform/gateway/
  ↓
app/platform/app.py
  ↓
app/platform/router.py
  ↓
app/platform/registry.py
  ↓
app/platform/runtime.py
  ↓
skills/<skill_id>/workflow.py
  ↓
app/platform/tools.py
  ↓
app/platform/pydantic_runtime.py
  ↓
Pydantic AI Agent
  ↓
结构化输出
```

## 分层说明

### 1. 入口层

当前已有：

- `app/platform/demo.py`：本地 demo 入口。
- `app/platform/gateway/wecom.py`：企业微信文本消息处理核心，负责提取文本、调用平台 runner、格式化回复。
- `app/platform/intake.py`：写作和审核共用的任务暂存内核，统一原子状态、文件安全暂存、用户隔离、重启恢复、TTL 清理和数量/总大小校验，不包含具体写作或审核判断。
- `app/platform/task_execution.py`：SQLite 持久化后台任务内核，提供消息幂等、分级并发、worker 租约与 fencing token、跨进程执行锁、周期恢复、取消、待补充后恢复、状态迁移和安全失败分类；进程存活且同步线程未收敛时不会被第二个 worker 重领，进程退出后由操作系统释放锁。
- `app/platform/attachment_delivery.py`：公共附件交付，统一任务目录校验、串行上传、动态超时、完整重试、主动/被动媒体发送、交付状态、运维事件和任务编号兜底。
- `app/platform/documents/enrichment.py`：扫描 PDF 按需 OCR 以及 PDF/PPTX 逐页渲染；外部文档工具使用绝对命令、隔离环境和资源限制，失败时保留文本解析结果并记录结构化告警。
- `app/platform/cli.py`：新底座 CLI，可做配置检查和本地消息测试。
- `app/platform/app.py`：平台应用服务，把路由、权限、任务记录、runtime 串起来。
- `app/writing/bot.py`：当前写作 Bot 的真实企业微信入口适配层，已经调用 `PlatformApp`。
- `app/writing/intake.py`：写作 Bot 的短任务组装层，负责把用户分多条发送的意图、链接、文字和文件组装成一次结构化写作请求；当前可进入直报、单/多素材简报、综合调研整合或文字润色。待组装状态和文件持久化在 `M-Agent-Files/runtime/intake/`，有效期内可在 Bot 重启后恢复；结构化任务返回待澄清时不会提前清理，用户回答后沿原文件自动续跑。
- `app/review/main.py`：审核 Bot 独立入口；普通文件按原有内容审核分流，显式格式审核和多文件联合审核通过审核模块自己的持久化组装层处理。
- `app/review/intake.py`：审核 Bot 过渡期短任务组装。格式审核支持“指令在前或文件在前”；普通文件到达即确认，后台以可配置的短静默窗口自动把 1 份文件分流到单文件审核、把连续 2 至 5 份分流到联合审核。系统不按上传顺序默认正文，状态按入口和用户隔离并持久化。
- `app/review/multi_file_reviewer.py`：审核业务层的多文件规则。逐份复用原有审核引擎，再检查附件缺失、重复、未引用、编号名称错配和跨文件逻辑；模型问题必须同时命中两份文件中的真实原文证据。

当前公共化进度：

- 写作和审核已共同复用 `app/platform/intake.py` 的持久化与安全内核，原有 `app/writing/intake.py`、`app/review/intake.py` 只保留各自的意图、格式审核、主文件识别等业务状态机。
- 公共层已统一 `wait`、`submit`、`cancel`、`bypass` 动作，以及文字、URL、持久化文件引用和结构化任务提交模型；写作、审核适配层都可转换为该协议，具体业务判断继续留在各自模块。
- 写作和审核结果附件已统一经过 `AttachmentDelivery`，入口不再各自维护上传和媒体回复重试代码。
- 统一文档服务已接入按需 OCR；页面渲染作为显式能力提供给后续 PPT 审核等需要视觉页面的工作流，不在所有写作任务中默认生成图片。
- 后台任务执行器已完成生产竞态加固，但现有写作和审核长任务尚未整体切换到持久队列。切流前要为具体 handler 明确可恢复性和外部发送幂等，不能把“执行器已存在”误写成“所有 Bot 重启后都会自动续跑”。

当前交付边界必须分成两类理解：

- **已经接入现有入口**：公共任务暂存、跨消息材料组装、DOCX/PDF/PPTX 安全读取、扫描 PDF 按需 OCR、结果附件重试和运维告警。这些能力已经直接改善当前写作或审核流程。
- **底座已完成、业务待切流**：持久化任务排队、重复消息幂等、分级并发、取消和进程重启恢复。内核与竞态测试已经完成，但只有具体写作或审核 handler 接入并验证后，对应业务才真正获得这些能力。

因此，本轮 P1 的实际作用是把“收材料、跑任务、读文件、发结果”四类公共基础设施补齐。下一阶段是生产接入和故障验收，不是再增加一套任务框架。

后续接入：

- 第一批只接直报写作：企业微信回调快速登记任务并返回任务编号，后台执行生成和交付，重点验证重复消息、进程重启、取消、多用户排队和发送幂等。
- 直报稳定后依次接入 `writer1`、`writer2`、`research_synthesis`；可安全重跑的任务才允许恢复，不可安全重跑的任务在中断后明确失败并通知运维。
- 审核 Bot 继续保持独立入口和现有执行方式，待写作切流稳定后再单独评估，不在第一批范围内。
- 统一企业微信入口适配：长期再把更多 skill 接入同一个入口；当前审核 Bot 继续独立运行，不把入口统一作为近期前置条件。

入口层只负责接收消息、下载文件、组装本次任务和返回结果，不写“正文与附件是否一致”等具体业务审核规则。

### 2. 路由层

位置：

```text
app/platform/router.py
```

职责：

- 从用户自然语言中识别意图。
- 只能在已登记 skill 中选择。
- 不确定时追问。
- 超出能力范围时拒绝。

当前是关键词路由。后续可以升级为 Pydantic AI 意图分类器，但仍必须输出固定枚举，不允许自由发明能力。

当前已补充平台级“上一稿改稿”识别：用户没有发送新链接、新文件，也没有明确要求开启新任务时，`PlatformApp` 会先查看同一入口、同一用户是否存在活跃稿件。如果存在，且上一轮 skill 声明 `supports_revision: true`，系统会把本次请求路由回上一轮 skill，并只把上一稿标题、正文、来源作为本次改稿材料。

当前改稿上下文优先来自：

```text
app/platform/conversation.py
```

如果没有会话记录，再回退到任务存储里的最近一次成功输出。

改稿执行层还有两条硬约束：

- `previous_draft` 类型材料在模型 prompt 中保留更长文本，避免长简报只传入前半部分，导致模型看不到后续段落。
- `skills/revision_support.py` 会把用户改稿要求转成局部编辑约束，例如“只改标题”时不得拆分段落，“不要拆”时不得把一个板块拆成两个板块，“改变原文意思”时不能声称已经核对原始素材。

改稿/新任务判断由平台意图分类模块负责：

```text
app/platform/intent.py
```

当前输出固定枚举：

```text
revise_previous
new_task
clarify
out_of_scope
```

它负责识别“这版太像新闻稿”“开头太虚”“结尾不要这么写”“回到上一版”等口语化改稿需求，也负责保护“根据这篇材料写简报”“新链接/新素材”等新任务不被误判成改稿。

### 3. Skill 注册表

位置：

```text
app/platform/registry.py
skills/*/config.yaml
```

职责：

- 扫描 `skills/` 目录。
- 加载 skill 名称、触发词、允许工具、workflow。
- 只把已启用 skill 暴露给路由和运行层。

### 4. 运行层

位置：

```text
app/platform/runtime.py
```

职责：

- 根据路由结果找到 skill。
- 为 skill 创建受限工具网关。
- 调用对应 workflow。
- 返回统一结果。

### 5. 工具授权层

位置：

```text
app/platform/tools.py
```

核心类：

```text
ToolGateway
```

职责：

- skill 只能调用 `config.yaml` 声明的工具。
- 未授权工具直接拒绝。
- 后续所有基础工具都要经过这里。

### 6. 基础工具层

位置：

```text
app/platform/builtin_tools.py
```

当前已有：

- `read_web_page`：只读取公网 http/https 网页；拒绝 `file://`、localhost、内网 IP、云元数据地址和 DNS 解析到私网。请求关闭自动跳转，每一跳先校验目标再访问；已校验的公网 DNS 结果会固定到本次请求，并限制跳转次数和响应体大小。
- `search_web`：调用搜索 API，返回标题、摘要、链接和来源类型。
- `policy_research` / `policy_materials` / `policy_search`：共享政策挂靠判断、政策知识库材料包和底层检索。
- `bank_materials` / `bank_search`：微众银行信息库材料包和底层检索。
- `read_word_file`：读取当前任务目录内的 `.docx` 文件。
- `read_pdf_file`：读取当前任务目录内的 `.pdf` 文件。
- `read_document_file`：统一读取当前任务目录内的 `.docx`、`.pdf`、`.pptx`，经过格式、路径、宏和异常压缩包校验后，返回标准材料片段并把完整解析结果保存到任务 `work/`；旧 Word/PDF 工具保留兼容。
- `LLMWriter`：早期手写模型包装，保留测试和兼容用途。

写作工作流调用网页读取时必须做容错：单个链接读取失败不能让整个任务直接失败。对简报类 skill，只要出现链接读取失败，就先询问用户，是继续使用已读取素材写，还是粘贴失败链接正文后再一起写；如果所有链接都失败，应返回明确提示，让用户更换链接或直接粘贴素材正文。

后续新增：

- `task_storage`
- `review_engine`

### 7. Pydantic AI 运行层

位置：

```text
app/platform/pydantic_runtime.py
```

当前核心类：

```text
PydanticAIWriter
```

职责：

- 创建 Pydantic AI Agent。
- 使用项目 `.env` 中的模型配置。
- 读取 skill 规则和 prompt。
- 要求模型返回 Pydantic 结构化结果。

当前 direct_report 输出模型：

```text
skills/direct_report/schema.py
DirectReportResult
```

模型配置规则：

```text
MODEL_NAME
MODEL_BASE_URL
MODEL_API_KEY
M_AGENT_MODEL_MAX_TOKENS
```

是写作底座优先读取的标准配置。旧的：

```text
ANTHROPIC_BASE_URL
ANTHROPIC_API_KEY
```

只作为兼容兜底。

DeepSeek 写作模型当前实际使用 OpenAI 兼容通道，应配置：

```text
MODEL_BASE_URL=https://api.deepseek.com/v1
MODEL_NAME=deepseek-v4-flash
M_AGENT_MODEL_MAX_TOKENS=4096
```

`app/platform/pydantic_runtime.py` 只要识别到 `deepseek.com`，就会使用 `OpenAIChatModel`，并把实际请求地址固定为 `https://api.deepseek.com/v1`。审核 Bot 使用独立模型配置，仍可走 DeepSeek Anthropic 兼容通道；两条链路不要混写。

`M_AGENT_MODEL_MAX_TOKENS` 控制单次模型输出上限。默认值为 `4096`。此前写作底座硬编码为 `2048`，DeepSeek 在直报长 prompt、结构化输出和质量校验场景下可能在生成任何可用结果前触顶，导致企业微信侧返回“处理失败”。

`deepseek-v4-flash` 默认可能进入 thinking mode，而 Pydantic AI 结构化输出依赖 tool_choice。写作底座对 DeepSeek 模型应通过 `extra_body={"thinking": {"type": "disabled"}}` 关闭 thinking mode，不能只传布尔值 `thinking=False`。

综合调研整合给模型材料增加 `material_role`：`outline` 表示唯一提纲，`source` 表示部门素材；来源材料同时增加规范化 `source_label`。`PydanticAIWriter` 会把角色和来源标签都显式写入模型材料，避免模型只能从长文件名反推部门。提纲使用更大的均衡取样预算，避免普通上传材料的较短上下文预算截断提纲尾部。该标记只影响模型材料编排，不放宽文件访问范围。

综合调研工作流采用两阶段模型调用：第一阶段通过 `ResearchSynthesisPlan` 生成“提纲问题—综合事实—来源—缺口—冲突—图片位置”材料台账；第二阶段根据台账形成正文。正文生成后再做不依赖模型的确定性校正，包括统一一、二级标题编号、规范化并后置来源标签、移除原始文件名、补回遗漏的提纲一级主题、合并连续图片提醒和核对图片部门数量。这样可以降低一次调用按文件顺序堆叠、模型与格式检查共享同一误判规则的问题。

### 8. 任务存储层

位置：

```text
app/platform/storage.py
```

职责：

- 每次用户请求创建独立 job 目录。
- 固定分为 `input/`、`work/`、`output/`。
- 写入 `meta.json`、不含用户正文的 `status.json` 和 `output/result.json`。
- 保留按用户和入口查找最近一次成功输出的兜底能力。
- `meta.json` 同时保存 `sender_userid` 和 `sender_name`，便于把企业微信内部 ID 对应到真实使用者。
- 只在 `meta.json` 中保存截断后的消息预览，避免把长正文和敏感材料直接写入元信息。

默认任务目录（由 `M_AGENT_DATA_DIR` 派生）：

```text
../M-Agent-Files/tasks/writing/YYYY/MM/<job_id>/
```

运行数据根目录位于 Git 仓库之外；每个新任务固定包含 `input/`、`work/`、`output/`、`meta.json` 和 `status.json`。`status.json` 将处理状态分为 `processing`、`completed`、`needs_input`、`failed`、`incomplete`，并单独保存交付状态。生成结果不等于企业微信已成功回传，因此当前无法确认的历史和新任务交付状态统一记为 `unknown`，管理台不得把它展示为“已送达”。

历史任务状态通过 `scripts/backfill_task_status.py` 补齐。该脚本只在任务目录增加状态索引，不改原始材料和结果正文；正式执行前必须先预演，再加 `--apply`。管理台只读取 `status.json`、元信息文件是否存在和审核报告是否存在，不通过读取用户正文推断完成状态。

### 9. 开发期对话日志层

位置：

```text
app/platform/chat_log.py
../M-Agent-Files/runtime/chat-logs/
```

职责：

- 开发期记录每一轮写作对话，便于排查多轮改稿、路由误判、模型失败和质量问题。
- 记录完整用户输入、即时提示、最终回复、用户名、意图分类结果、路由 skill、输出 skill、job_id、上一稿 job_id、稿件版本、输出结构和错误摘要。
- 异常失败时也会写入日志，并保留安全错误摘要。
- 默认开启；后续运行稳定后可通过 `.env` 设置 `M_AGENT_CHAT_LOG_ENABLED=false` 关闭。
- 日志文本有长度上限，避免无限膨胀；真实密钥、环境变量不会写入日志。

默认日志目录：

```text
../M-Agent-Files/runtime/chat-logs/
```

该目录已加入 `.gitignore`，不要提交真实对话日志。

相关配置：

```text
M_AGENT_CHAT_LOG_ENABLED=true
M_AGENT_DATA_DIR=../M-Agent-Files
```

### 10. 运维事件和日报层

位置：

```text
app/platform/ops/
../M-Agent-Files/runtime/ops/events/
../M-Agent-Files/runtime/ops/state.json
../M-Agent-Files/runtime/ops/heartbeats/
```

职责：

- 写作 Bot 和审核 Bot 在运行异常时写入本地运维事件日志。
- 独立运维 Bot 通过 `uv run --locked python -m app.platform.ops.bot` 长期运行。
- 运维 Bot 使用独立企业微信机器人凭证，不依赖写作 Bot 或审核 Bot 的长连接。
- 运维 Bot 轮询当天的 `ops_events`，把未通知过的异常和提醒发送给管理员。
- 实时通知不回看昨天或前一个工作日，避免 Bot 重启、周一启动或运行数据迁移后逐条补发历史告警；历史事件统一进入工作日报。
- 同一天内来源、级别、主题和详情完全相同的事件只实时通知一次；重复次数仍保留在事件日志和工作日报中。
- 运维 Bot 每个工作日 9:00 汇总前一个工作日的写作入口请求、skill 分布、失败和运维事件。
- 写作 Bot 和审核 Bot 定期写入心跳文件，运维 Bot 监控心跳是否缺失或超时。
- 写作 Bot 和审核 Bot 的企业微信连接断开、连接错误会写入运维事件。
- 写作素材页后台处理失败会写入运维事件。
- 周一日报默认汇总上周五；暂不接入中国法定节假日日历。

相关配置：

```text
M_AGENT_OPS_BOT_ID=
M_AGENT_OPS_BOT_SECRET=
M_AGENT_OPS_ADMIN_USER_ID=
M_AGENT_DATA_DIR=../M-Agent-Files
M_AGENT_OPS_HEARTBEAT_MAX_AGE_SECONDS=180
M_AGENT_OPS_MONITORED_SERVICES=writing_bot,review_bot
M_AGENT_OPS_DAILY_REPORT_HOUR=9
M_AGENT_OPS_DAILY_REPORT_MINUTE=0
```

当前仍需注意的边界：

- 运维 Bot 自身如果进程退出，无法靠自己发送告警；生产阶段应使用 `launchd`、进程管理器或系统级守护来拉起。
- 管理后台、政策库更新、银行信息库导入目前主要是本机维护工具，不属于已接入实时告警的用户入口；后续如果改为定时任务，应同样写入 `ops_events`。

### 11. 会话状态层

位置：

```text
app/platform/conversation.py
../M-Agent-Files/runtime/conversations/
```

职责：

- 按“入口 + 用户”保存当前活跃稿件。
- 保存当前 skill、当前稿件、稿件版本链、用户修改要求和可读用户名。
- 成功生成新稿时开启新会话版本 `v1`。
- 基于上一稿改稿成功后追加 `v2`、`v3` 等版本。
- 支持用户指定版本，例如“回到上一版”“按第一版再改”。
- 追问、失败或无输出结果不覆盖当前活跃稿件。
- 为后续迁移 LangGraph 预留 state 结构：当前实现是文件化 `ConversationStore`，未来可以映射为 LangGraph thread state / checkpointer。

当前会话目录：

```text
../M-Agent-Files/runtime/conversations/
```

### 12. 用户名称映射层

位置：

```text
app/platform/user_registry.py
../M-Agent-Files/runtime/users/review_users.yaml
```

职责：

- 把企业微信 `userid` 映射为可读用户名，例如把 `user-001` 映射为“测试用户”。
- 写作 Bot 和审核 Bot 共用同一份本地映射表。
- 写作链路会把用户名写入 job、conversation、chat log 和运行输出。
- 审核 Bot 保留原导入路径 `app/review/user_registry.py`，但实际实现已迁移到底座，避免影响现有审核入口。

配置项：

```text
M_AGENT_DATA_DIR=../M-Agent-Files
```

用户名表属于本机运行数据，包含企业微信用户 ID，保存在 Git 仓库之外，不要复制或提交到仓库。

### 13. 统一非 Git 数据根目录

位置：

```text
app/platform/data_paths.py
../M-Agent-Files/
```

职责：

- 用 `M_AGENT_DATA_DIR` 统一派生写作任务、审核任务、知识库、日志、会话和运维路径。
- 默认使用项目同级的桌面 `M-Agent-Files/`，目录权限设为仅当前用户可访问。
- 写作和审核任务按 `YYYY/MM/<task_id>` 分层，避免单目录无限增长。
- 审核原文件保存在 `input/`，标注文档和审核报告保存在 `output/`。
- 尚未明确用途或尚未开始处理的上传文件暂存在 `runtime/intake/`；任务开始后转入对应任务的 `input/`。
- 待组装状态和文件通过 `app/platform/intake.py` 采用原子写入，按入口和用户隔离；目录名使用入口与用户的哈希摘要，不暴露用户 ID；Bot 重启后会恢复有效期内的会话，伪造的目录外文件引用会被拒绝，过期或已提交的暂存文件会清理。
- 审核 Bot 当前把格式/联合审核状态保存在 `runtime/intake/review/`：格式请求可以关联最近一份有效 Word；联合审核不会按发送顺序决定主文件，证据不足时先要求用户确认。
- DOCX/PDF/PPTX 的完整标准解析 JSON 已统一保存在任务 `work/documents/`；PPT 提取图片也保存在对应 `assets/`。后续逐页渲染图片、OCR 结果和其他中间产物继续进入 `work/`；需要返回给用户的报告、标注或生成文件保存在 `output/`。
- 用户上传文件、系统生成文件和文档处理中间产物都不得写入 Git 仓库；后续新增格式也继续使用该数据根目录，不另建分散的运行文件目录。
- `scripts/migrate_runtime_data.py` 负责预演、复制、冲突拦截、SHA-256 校验和迁移清单记录；迁移不会自动删除旧数据。
- 首次切换后，旧目录可整体移入 `legacy/pre-migration-source/` 保留回滚能力；确认稳定前不得直接删除。
- 审核公共日志和单用户日志按天记录，单文件默认上限 `20MB`，超限后继续生成 `part-002` 等分片；系统心跳和 SDK 日志不在用户目录重复保存。
- 用户日志处理器限制同时打开的文件数量，避免用户规模增长后长期占用过多文件句柄。

### 14. 身份和权限层

位置：

```text
app/platform/identity.py
config/platform-policy.example.yaml
```

职责：

- 控制企业微信用户 ID 可以使用哪些 skill。
- 未授权用户不能调用对应 skill。
- 本地开发如果没有配置权限文件，默认允许使用当前启用的 skill，便于测试。

生产使用时应在 `.env` 中配置：

```bash
cp config/platform-policy.example.yaml config/platform-policy.yaml
```

在本机的 `config/platform-policy.yaml` 填入实际权限后配置：

```text
M_AGENT_PLATFORM_POLICY=config/platform-policy.yaml
```

`config/platform-policy.yaml` 包含真实企业微信用户 ID，只保留在本机并已加入 `.gitignore`；仓库只提交脱敏的 example 文件。

### 15. 功能区

位置：

```text
skills/
```

当前已有：

```text
skills/direct_report/
skills/writer1/
skills/writer2/
skills/rewrite/
skills/research_synthesis/
```

其中 `writer1` / `writer2` 已启用，已补齐 workflow、schema 和业务测试，并已接入 `bank_materials` 微众银行材料包和共享 `policy_research` 政策判断层；兼容层仍保留 `policy_materials` 作为低层政策材料包工具。当前简报真实口径已明确为面向深圳市金融办、南山区、前海管理局、深圳人行、深圳金监局等地方政府和监管部门，重点展示微众银行近期动态及成果，篇幅正常控制在 `1000` 字左右、最长不超过 `1200` 字。当前已新增简报共享质量层：写作前会注入隐藏规划，并按经授权样本识别综合成果型、机制成果型、产品工具型、平台合作型、标准引领型、能力建设型、外部认可型、活动亮相型、专项治理型等类型；多素材会先判断是否弱关联，生成后会做确定性规则检查和语义审查；后续仍需继续通过经授权样本收敛成稿质量和多素材整合表达。

`research_synthesis` 与 `writer2` 分开：`writer2` 从多素材中自行提炼简报主题和结构；`research_synthesis` 把用户现成提纲作为不可随意改变的结构骨架，将各部门材料归入对应章节。当前只做忠实整合、去重、必要衔接以及缺口/冲突标记，不联网补事实，不包含宣传化润色；输出为采用现有公文基本格式的 Word。源 DOCX 图片不进入成稿，解析器在原位置生成部门图片提醒；开头和结尾暂由用户补充，系统只在 Word 中保留备注。超长材料逐条穷尽和第二阶段润色仍由 `TODO-026` 后续推进。

每个 skill 自己负责业务流程、prompt 和结构化 schema，但不能绕过底座权限。

### 16. 本机项目控制台

位置：

```text
app/admin/services.py
app/admin/server.py
```

职责：

- 从 Skill 配置、`TODO.md`、任务目录、知识库、运维心跳和本地 Git 元信息生成项目总览。
- 用五层交互式关系图展示用户入口、通用底座、业务功能、工具知识库、运维数据、能力依赖及各能力建设状态，并保留状态清单视图。
- 展示底座、写作、审核、知识库、入口运维和管理后台六个板块的当前情况、最新提交和首要待办；写作统计拆分为成稿、待补充、失败、处理中或中断，审核统计兼容旧 `meta.md` 与新 `meta.json` 归档，并以 `output/report.md` 作为历史任务已生成审核报告的事实依据。
- 保留 Skill 开关、用户权限和最近写作任务摘要管理。

能力地图把建设成熟度和运行在线状态分开：成熟度由版本化的架构节点、Skill 开关、TODO 状态和对应代码证据生成，在线状态只来自 Bot 心跳；不调用模型自由判断进度。节点关系同样由代码内明确关系表维护，关系端点必须经过自动化测试确认存在。交互画布使用仓库内固定版本的 `vis-network 10.1.0`，不依赖 CDN 或 React 构建链；节点详情和状态清单仍由服务端生成。控制台只监听 `127.0.0.1`。Git 查询是代码内固定的只读命令，不接受页面输入、不自动联网；任务和知识库在总览中只统计数量，不读取用户正文。项目状态仍以代码、核心文档和 Git 为唯一事实来源，控制台不维护第二套状态文件。

## Pydantic AI 在系统里的位置

Pydantic AI 不是可视化平台。它是 Python 代码框架。

在 M-Agent 中：

```text
M-Agent 自己负责：
- 企业微信入口
- 用户权限
- 意图路由
- skill 注册
- 工具授权

Pydantic AI 负责：
- 模型调用
- Agent 执行
- 结构化输出
- 后续工具编排
```

## 安全模型

M-Agent 不做远程万能 Mac 助手。

外部用户只能接触：

```text
企业微信入口 -> 已登记 skill -> 已授权工具 -> 当前任务材料
```

外部用户不能接触：

```text
.env
Mac 任意目录
shell
历史任务材料
未授权插件
未登记能力
```

安全靠代码边界，不靠 prompt 承诺。

## 当前真实最小闭环

已验证链路：

```text
uv run --locked python -m app.platform.demo "帮我根据这个链接写直报：https://..."
```

执行过程：

1. `demo.py` 读取 `.env`。
2. `router.py` 识别为 `direct_report`。
3. `registry.py` 加载 `skills/direct_report/config.yaml`。
4. `runtime.py` 创建工具网关。
5. `workflow.py` 调用 `web_reader` 和 `llm_writer`。
6. `web_reader` 读取用户 URL。
7. `PydanticAIWriter` 调用 Pydantic AI Agent。
8. Agent 返回 `DirectReportResult`。
9. demo 输出标题、正文、来源。

## 当前企业微信入口状态

已完成：

```text
企业微信文本消息 frame
  -> app/platform/gateway/wecom.py
  -> app/platform/app.py
  -> route_message
  -> AccessPolicy
  -> JobStore
  -> PlatformRuntime
  -> PlatformResult
  -> 企业微信回复文本
```

当前 `gateway/wecom.py` 是纯 Python 核心，已经有单元测试。写作 Bot 的真实企业微信入口目前由 `app/writing/bot.py` 适配，并已经调用 `PlatformApp`。

写作 Bot 已新增短任务组装 v1：

```text
用户先发材料
  -> app/writing/intake.py 暂存链接/文字/文件
  -> 用户后续说明“写简报/写直报/改写”
  -> PlatformApp.handle_structured_request(...)

用户先说“帮我写简报”
  -> app/writing/intake.py 暂存意图
  -> 用户连续发送一个或多个材料
  -> 用户回复“开始写”
  -> 根据材料数量进入 writer1 或 writer2

用户先说“帮我做综合调研整合”
  -> app/writing/intake.py 暂存 1 份调研提纲和若干部门素材
  -> 用户回复“开始写”
  -> research_synthesis 先按明确文件名和正文答复特征识别提纲
  -> 仍有歧义时保留原任务并追问，用户回答后自动续跑
  -> 按唯一提纲归集材料
```

该能力目前是写作入口内的持久化短任务会话，状态和待组装文件保存在 `M-Agent-Files/runtime/intake/`，默认 1800 秒过期，可用 `M_AGENT_WRITING_INTAKE_TTL` 调整；Bot 重启后可恢复有效会话。它解决的是短任务连续消息场景，不替代 `ConversationStore` 的上一稿改稿能力；公共动作和提交协议已下沉，具体业务状态机继续由写作、审核模块分别维护。

审核 Bot 同样具备模块内持久化组装 v1：格式审核可先发文件后补充指令；内容审核默认不要求任何前后指令，文件到达后立即确认，再通过 `M_AGENT_REVIEW_AUTO_BATCH_SECONDS` 控制的后台静默窗口（默认 8 秒）自动区分单文件和 2 至 5 份联合审核。主文件综合文件名和正文引用识别，不按上传顺序决定；证据不足时才请求用户确认。待组装状态使用 `M_AGENT_REVIEW_INTAKE_TTL`，默认 1800 秒。该实现通过公共任务动作、材料引用和提交模型与写作入口对齐；格式审核、主文件识别和静默窗口仍是审核自己的业务规则。

尚未完成：

- 现有写作、审核重任务向持久化执行器的分批切流和真实运行验收；执行器内核已完成，不能在未声明 handler 可恢复性时直接全量启用。
- 一个覆盖直报、简报、审核等能力的统一企业微信 Bot。
- 群权限。
- 复杂多轮会话存储。

已完成 v1：

- 同一用户在同一入口下，基于上一轮 M-Agent 生成的直报/简报初稿继续提出修改要求。
- 写作入口支持“先发材料后说用途”“先说用途后发材料”“发完回复开始写”的短任务组装，待组装文件可在 Bot 重启后恢复。
- 写作入口支持综合调研多文件组装；优先使用用户点名或名称明确的总提纲，并用正文答复特征排除部门反馈。证据仍不唯一时 skill 追问而不按上传顺序猜测，原文件保留并在用户回答后自动续跑。
- 综合调研成功后把生成的 `.docx` 保存在当前任务 `output/`，写作 Bot 只允许回传该能力生成、位于 `output/`、不超过入口上限的 Word 文件；企业微信文字消息只返回简短完成提示，不暴露本机路径。
- 写作入口每次任务最多接收 10 份文件，文件总大小仍不超过 20MB；数量和总大小分别在任务组装阶段校验。
- 审核入口支持格式审核前后置触发，以及 `.docx` 单/多文件自动分流、可选显式开始、主文件确认、持久化恢复和任务隔离。
- 统一文档服务 v1 支持 DOCX/PDF/PPTX 安全解析、标准结构、页码或幻灯片定位和完整结果落盘；DOCX 内嵌图片会提取到任务 `work/` 并在正文原位置生成未读取提醒，长材料均衡抽样强制保留这些提醒；扫描 PDF 会标记为待 OCR。
- 扫描 PDF 只对 `ocr_required` 页面按需 OCR，macOS 可使用本机 Vision；PDF/PPTX 可显式逐页渲染，已通过真实扫描 PDF 和真实 PPT 烟测。页面图片限制最长边 2400 像素、单图 25MB、总计 250MB，默认最多渲染 200 页、OCR 50 页、总处理预算 300 秒。
- 公共附件交付已接入写作和审核 Bot：默认支持企业微信 SDK 的 100 个 512KB 分片上限，统一动态超时、重试和安全运维事件；超限或重试失败时返回任务编号供管理员人工取回。当前不自动压缩用户图片，因此不会静默改变文档画质。
- 后台任务执行内核已支持幂等键、SQLite 持久化、全局/单用户/成本并发、租约 token、心跳、跨进程执行锁、周期恢复、取消、待补充后恢复、schema 迁移和状态版本防乱序；具体 Bot 切流仍需逐任务验收。

## 后续演进顺序

建议顺序：

1. 先建立审核真实样本质量基线，不在效果未稳定时继续铺开审核类型。
2. 统一文档服务 OCR 和页面渲染已完成；继续用真实复杂文件扩充回归，HTML 文件支持暂缓。
3. 用真实“正文 + 多附件”材料验收审核 Bot 已实现的 `.docx` 联合审核 v1，收敛误报、漏报、定位、耗时和回传问题。
4. 公共组装协议、持久化执行器和附件交付内核已完成；下一步按任务类型逐步切流并观察幂等、恢复、耗时和告警。
5. 页面渲染和公共附件回传已具备后再增加 PPT 专项审核；能读取或渲染 PPT 不等于已经能审核 PPT 版式。
6. 直报、简报和改稿保持样本驱动迭代；统一企业微信入口和 `review` skill 包装暂不作为近期前置条件。
7. 需要复杂长任务时再引入 LangGraph。
