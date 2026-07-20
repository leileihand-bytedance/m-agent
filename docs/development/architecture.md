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

### 代码与运行环境隔离

`main` 主工作区是唯一生产运行目录；开发使用 `.worktrees/` 下的短期任务分支，多个任务共用同一项目 `.venv`，但不共享 `.env` 和运行数据。`app/platform/runtime_environment.py` 在所有企业微信 Bot 连接前统一执行以下硬校验：

- `production` 只允许从 `main` 启动，并使用生产 Bot 凭据和 `M_AGENT_DATA_DIR`。
- `test` 只读取对应的 `M_AGENT_TEST_*_BOT_ID/SECRET`，不回退生产凭据。
- 测试模式必须显式设置与生产目录不同的 `M_AGENT_TEST_DATA_DIR`。
- 测试任务、会话、队列、日志、用户名表、知识库和运维状态的实际路径都必须位于测试数据根目录。

这层校验位于入口启动和配置层，不依赖 prompt，也不会因为某个 Skill 写错而绕过。离线自动化测试仍使用临时目录和模拟对象，不需要真实 Bot。

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
- `app/platform/attachment_delivery.py`：公共附件交付，统一任务目录校验、串行上传、动态超时、完整重试、主动/被动媒体发送、交付状态、运维事件和“处理编号”兜底。
- `app/platform/documents/enrichment.py`：扫描 PDF 按需 OCR 以及 PDF/PPTX 逐页渲染；外部文档工具使用绝对命令、隔离环境和资源限制，失败时保留文本解析结果并记录结构化告警。
- `app/platform/cli.py`：新底座 CLI，可做配置检查和本地消息测试。
- `app/platform/runtime_environment.py`：生产/测试运行模式、Bot 凭据选择、Git 分支和数据目录边界的公共启动守卫。
- `app/platform/app.py`：平台应用服务，把路由、权限、任务记录、runtime 串起来。
- `app/writing/bot.py`：当前写作 Bot 的真实企业微信入口适配层，已经调用 `PlatformApp`。
- `app/rewrite_bot/bot.py`：独立材料润色 Bot 的轻量企业微信入口；从注册表层只加载 `rewrite`，只接收直接粘贴文字，并使用独立任务和会话目录，不复制润色业务规则。
- `app/rewrite_bot/intake.py`：独立润色入口的纯文字需求确认层。只有原文时先持久化并追问修改方向，收到要求后才提交 `rewrite`；同条消息含原文和要求、或上一版续改时旁路该追问。状态与其他写作任务隔离。
- `app/writing/task_execution.py`：直报、`writer1`、`writer2` 的持久任务适配层；入队前创建正式写作 job 和文件快照，后台按“处理、会话收尾、主动发送”三个检查点恢复。
- `app/writing/intake.py`：写作 Bot 的短任务组装层，负责把用户分多条发送的意图、链接、文字和文件组装成一次结构化写作请求；当前可进入直报、单/多素材简报、综合调研整合、深银协动态、内参周报或文字润色。内参周报和深银协动态无需用户上传材料；“今天/今日资本市场综述”也路由到 `internal_weekly`，由该 Skill 只生成当日更新块。待组装状态和文件持久化在 `M-Agent-Files/runtime/intake/`，有效期内可在 Bot 重启后恢复；结构化任务返回待澄清时不会提前清理，用户回答后沿原文件自动续跑。
- `app/review/main.py`：审核 Bot 独立入口；Word 按原有内容审核分流，单个 `.html/.htm` 直接进入静态文字审核，显式格式审核和多文件联合审核通过审核模块自己的持久化组装层处理。确认任务后提交审核专用后台队列；联合审核在入队前固化 2 至 5 份 DOCX、主文件和补充要求，完成后按“摘要 + 各标注文档”逐项交付。
- `app/review/html_parser.py`：只提取上传 HTML 的静态可见标题、段落、列表和表格行；忽略脚本、样式、注释、显式隐藏元素、关闭的 `dialog` 和折叠 `details` 的非摘要正文，只从真实 `meta` 读取编码声明；网页 PPT 按 class 精确包含 `slide` 的可见容器顺序保留段落页码，普通 HTML 保留段落位置；不执行 JavaScript、不联网加载资源，也不解释复杂 CSS 选择器或计算浏览器打印分页。
- `app/review/intake.py`：审核 Bot 过渡期短任务组装。格式审核支持“指令在前或文件在前”；普通文件到达即确认，后台以可配置的短静默窗口自动把 1 份文件分流到单文件审核、把连续 2 至 5 份分流到联合审核。系统不按上传顺序默认正文，状态按入口和用户隔离并持久化。
- `app/review/multi_file_reviewer.py`：审核业务层的多文件规则。逐份复用原有审核引擎，再检查附件缺失、重复、未引用、编号名称错配和跨文件逻辑；模型问题必须同时命中两份文件中的真实原文证据。
- `app/review/capabilities.py` 和 `app/review/observability.py`：把审核对外一个能力拆成八个稳定内部子能力，统一任务类型映射、日志维度和不含正文的处理/交付统计；不参与意图识别或业务规则选择。
- `app/review/core/`：审核业务域内的格式无关核心，统一问题与证据合同、模型结果解析、调用和固定预算重试、逐字证据、去重及阶段指标；不包含内参、半月报、PPT或公文格式业务判断。
- `app/review/rules/`：活动规则ID和规则族的权威索引，以及各审核类型的静态profile。纯文字、通用Word、静态HTML、内参和半月报已经显式接入各自profile；profile只选择规则，不按规则增加模型调用。内参和半月报复用共享模型运行、逐字证据、去重和阶段指标，但专属提示词与结构判断仍在原模块；PPT继续保留页码、对象证据和独立提示词，只复用共享模型调用与阶段指标。

当前公共化进度：

- 写作和审核已共同复用 `app/platform/intake.py` 的持久化与安全内核，原有 `app/writing/intake.py`、`app/review/intake.py` 只保留各自的意图、格式审核、主文件识别等业务状态机。
- 公共层已统一 `wait`、`submit`、`cancel`、`bypass` 动作，以及文字、URL、持久化文件引用和结构化任务提交模型；写作、审核适配层都可转换为该协议，具体业务判断继续留在各自模块。
- 写作和审核结果附件已统一经过 `AttachmentDelivery`，入口不再各自维护上传和媒体回复重试代码。
- 统一文档服务已接入按需 OCR；页面渲染作为显式能力按工作流需要调用，不在所有写作任务中默认生成图片。
- 后台任务执行器已完成生产竞态加固。纯文字、单个通用 Word、内参、半月报、公文格式、单个静态 HTML、单份 PPTX 和多文件联合审核接入审核专用持久队列；直报、`writer1`、`writer2` 和 `shenyinxie_news` 接入独立写作队列。`research_synthesis` 和 `internal_weekly` 仍为实时执行，必须按具体 handler 区分生效范围。
- 审核八类子能力共用审核 Bot、任务目录和共享核心，但拥有独立能力 ID、按日分片日志和管理台统计。处理、模型、降级和交付日志绑定同一任务上下文；多文件联合审核按摘要和各结果附件分别保存交付检查点，任一已确认发送的结果不会因重启重复发送。统计拆分不增加模型调用，也不改变审核规则。

当前交付边界必须分成两类理解：

- **已经接入现有入口**：公共任务暂存、跨消息材料组装、DOCX/PDF/PPTX 安全读取、扫描 PDF 按需 OCR、结果附件重试和运维告警。这些能力已经直接改善当前写作或审核流程。
- **已经进入切流验收**：审核八类能力使用审核专用 SQLite；直报、两类简报和深银协动态使用写作专用 SQLite。十二类任务都支持快速受理、消息幂等、后台执行、检查点恢复和主动交付；发送状态无法确认时停止自动重发并告警，worker 意外退出会告警并自动重启。
- **底座已完成、其他业务待切流**：持久化任务排队、重复消息幂等、分级并发、取消和进程重启恢复。`research_synthesis` 和 `internal_weekly` 尚未获得这套生产行为，不能根据公共内核存在就标为已接入。

当前入口接入边界：

- 审核八类能力使用审核专用队列，直报、writer1、writer2 和 shenyinxie_news 使用写作专用队列。
- `research_synthesis` 和 `internal_weekly` 尚未获得同等持久恢复能力；`rewrite` 属于即时多轮交互，不作为当前后台初稿队列任务。
- 审核 Bot 保持独立入口；统一企业微信入口不是当前前置条件。

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

业务 Skill 路由当前以已登记触发词和固定规则为主；改稿、新任务、追问和越界判断由固定枚举意图分类辅助，路由结果不能自由发明能力。

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
- 所有基础工具都必须经过这里。

### 6. 基础工具层

位置：

```text
app/platform/builtin_tools.py
```

当前已有：

- `read_web_page`：只读取公网 http/https 网页；拒绝 `file://`、localhost、内网 IP、云元数据地址和 DNS 解析到私网。请求关闭自动跳转，每一跳先校验目标再访问；已校验的公网 DNS 结果会固定到本次请求，并限制跳转次数和响应体大小。标题优先采用网页 `og:title` 或 `twitter:title`，避免把站点栏目和媒体名带入成稿；发布日期在清理脚本前先读取 JSON-LD 和常见新闻元数据，也识别研究报告页面常用的 `citation_publication_date`、`DC.date` 以及 `<time>` 中的可见点分日期，供 Skill 做日期硬校验。部分权威站点模板会在公共页头后提前输出 `</html>`，使 lxml 丢弃后续正文；当原始页面明确含有段落而 lxml 未解析到任何段落时，工具使用标准库解析器回退，并从文章容器恢复正文和可见发布日期。公开 HTML 列表页会返回受限数量的带日期链接；公开 JSON 列表兼容顶层数组和 `data.rows`、`data.results` 等嵌套结构，归一标题、URL、发布日期，并以受限标量记录支持由 Skill 解释已登记的官方接口字段；公开 JSON 原文会归一标题、日期和 HTML/纯文本正文。工具不会自动跟随列表链接，业务 Skill 仍须逐条执行自己的域名、日期和内容校验。
- `search_web`：按供应商调用联网搜索并统一返回标题、摘要、链接和来源类型。DeepSeek 使用 Anthropic Messages `/anthropic/v1/messages` 的服务器工具 `web_search_20250305`，MiniMax 旧通道保留 `/v1/coding_plan/search` 兼容；未知供应商明确拒绝，不能把任意模型地址拼成 MiniMax 搜索路径。
- `policy_research` / `policy_materials` / `policy_search`：共享政策挂靠判断、政策知识库材料包和底层检索。
- `bank_materials` / `bank_search`：微众银行信息库材料包和底层检索。
- `read_word_file`：读取当前任务目录内的 `.docx` 文件。
- `read_pdf_file`：读取当前任务目录内的 `.pdf` 文件。
- `read_document_file`：统一读取当前任务目录内的 `.docx`、`.pdf`、`.pptx`，经过格式、路径、宏和异常压缩包校验后，返回标准材料片段并把完整解析结果保存到任务 `work/`。安全校验仍拒绝主文档宏格式、VBA 项目、其他 `macroEnabled` 部件、VBA 关系及非规范或重复的主部件声明；唯一兼容例外是 PPTX 中位于 `ppt/embeddings/`、且精确声明为标准 XLSB 类型的 `.xlsb` 图表数据。PPT 独立审核以 `render_pages=False` 读取可编辑内容，不单独打开这类嵌入工作簿，也不把其内容作为审核正文。旧 Word/PDF 工具保留兼容。
- `LLMWriter`：早期手写模型包装，保留测试和兼容用途。

写作工作流调用网页读取时必须做容错：单个链接读取失败不能让整个任务直接失败。对简报类 skill，只要出现链接读取失败，就先询问用户，是继续使用已读取素材写，还是粘贴失败链接正文后再一起写；如果所有链接都失败，应返回明确提示，让用户更换链接或直接粘贴素材正文。

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

`app/platform/pydantic_runtime.py` 只要识别到 `deepseek.com`，就会使用 `OpenAIChatModel`，并把写作生成请求地址固定为 `https://api.deepseek.com/v1`。公共 `search_web` 是另一条协议链：同一 DeepSeek Key 和模型通过 `https://api.deepseek.com/anthropic/v1/messages` 声明 `web_search_20250305`，解析服务器返回的搜索结果，再交给 `web_reader` 读取原文。审核 Bot 使用独立模型配置；写作生成、公共搜索和审核三条链路不要混写。

`M_AGENT_MODEL_MAX_TOKENS` 控制单次模型输出上限。默认值为 `4096`。此前写作底座硬编码为 `2048`，DeepSeek 在直报长 prompt、结构化输出和质量校验场景下可能在生成任何可用结果前触顶，导致企业微信侧返回“处理失败”。

`deepseek-v4-flash` 默认可能进入 thinking mode，而 Pydantic AI 结构化输出依赖 tool_choice。写作底座对 DeepSeek 模型应通过 `extra_body={"thinking": {"type": "disabled"}}` 关闭 thinking mode，不能只传布尔值 `thinking=False`。

综合调研整合给模型材料增加 `material_role`：`outline` 表示唯一提纲，`source` 表示部门素材；来源材料同时增加规范化 `source_label`。`PydanticAIWriter` 会把角色和来源标签都显式写入模型材料，避免模型只能从长文件名反推部门。提纲使用更大的均衡取样预算，避免普通上传材料的较短上下文预算截断提纲尾部。该标记只影响模型材料编排，不放宽文件访问范围。

综合调研工作流保持两次模型调用，但逻辑上分为“提纲解释—证据台账—正文编排”三阶段。第一阶段通过 `ResearchSynthesisPlan` 同时返回提纲类型、`exhaustive/selective` 覆盖方式、必答/选定/省略主题，以及“综合事实—来源—位置—口径—缺口—冲突—图片位置”证据台账；第二阶段只根据验证后的台账形成正文。台账中的 `source_text` 可直接使用，`derived` 必须有来源和明确算式，`image_candidate` 与 `external_missing` 强制不可用；来源标签还会与本次上传材料做匹配。正文生成后执行不依赖模型的确定性校正：逐项模式补回遗漏必答主题，选择模式过滤未选主题，规范化并后置来源标签，标记台账外数字，保留三级阿拉伯数字标题语义，并合并、核对图片提醒。这样既降低按文件顺序堆叠，也避免模型和提示词单点失误让无来源事实静默进入初稿。

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
- 默认开启；可通过 `.env` 设置 `M_AGENT_CHAT_LOG_ENABLED=false` 关闭。
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
- 管理后台、政策库更新、银行信息库导入目前是本机维护工具，不属于已接入实时告警的用户入口。

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
- 独立润色 Bot 的待确认原文保存在 `runtime/intake/rewrite-bot/`，默认 1800 秒有效；只存本次纯文字原文，不存其他 Skill 材料，提交成功或用户取消后清除。
- 审核 Bot 当前把格式/联合审核状态保存在 `runtime/intake/review/`：格式请求可以关联最近一份有效 Word；联合审核不会按发送顺序决定主文件，证据不足时先要求用户确认。
- DOCX/PDF/PPTX 的完整标准解析 JSON 已统一保存在任务 `work/documents/`；PPT 提取图片保存在对应 `assets/`。逐页渲染图片、OCR 结果和其他中间产物进入 `work/`；需要返回给用户的报告、标注或生成文件保存在 `output/`。
- 用户上传文件、系统生成文件和文档处理中间产物都不得写入 Git 仓库；新增格式也必须继续使用该数据根目录，不另建分散的运行文件目录。
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

当前登记的业务能力包括直报、单素材简报、多素材简报、材料润色、综合调研整合、深银协动态和内参周报。每个 Skill 自己维护业务规则、提示词、结构化模型和 workflow，但只能调用底座授权工具。内参周报内容生产与 `app/review/` 的内参审核、半月报审核相互独立；分类规则复制到 Skill 自有目录后维护，运行时不依赖审核模块。

各能力的当前范围和边界统一见 `docs/capabilities/`；详细业务规则以对应 `skills/<skill_id>/SKILL.md` 为准。架构文档不复制文种写法、字数、称谓、媒体白名单或模板细节。

### 16. 本机项目控制台

位置：

```text
app/admin/services.py
app/admin/server.py
```

职责：

- 从 Skill 配置、`TODO.md`、任务目录、知识库、运维心跳和本地 Git 元信息生成项目总览。
- 用“业务运行面 + 管理与治理面”的交互式关系图展示真实调用、数据供给和治理关系，并用独立状态清单展示各功能和模块的建设状态。
- 展示底座、写作、审核、知识库、入口运维和管理后台六个板块的当前情况、最新提交和首要待办；写作统计拆分为成稿、待补充、失败、处理中或中断，审核统计兼容旧 `meta.md` 与新 `meta.json` 归档，并以 `output/report.md` 作为历史任务已生成审核报告的事实依据。
- 保留 Skill 开关、用户权限和最近写作任务摘要管理。

项目总览采用两套互补但不混用的颗粒度：

1. 高层架构图说明系统如何工作。业务运行面由业务入口、智能体底座、业务能力、共享工具服务、知识资产和结果交付组成；管理与治理面由管理台、运维可观测性、运行数据治理、研发交付治理和知识库治理组成。写作侧展开直报、简报、材料润色、专题内容生产，审核侧展开通用内容审核、专类材料审核、公文格式审核、多文件联合审核，使架构图既能说明框架，也能看见核心能力。
2. 功能与模块状态清单说明每一项当前怎么样。清单按业务入口、智能体底座、业务能力、领域公共组件、共享工具服务、知识资产、管理与治理七组展示，并继续逐项列出具体 Skill 和八类审核子能力。审核共享核心属于领域公共组件，不算第九类审核功能。

逻辑分层不等同于物理目录。`app/platform/` 当前同时承载底座实现、受限共享服务和运维适配，但项目架构中只把接入权限、路由编排、Agent 运行、会话任务和状态存储定义为智能体底座；文档解析、网页检索、结果交付定义为共享工具服务；运维告警和观测定义为管理与治理。政策库和微众银行信息库是知识资产，政策可视化维护是知识资产的治理入口，不能归入“运维与数据”。

状态清单把建设成熟度、运行在线状态和执行方式分开：成熟度由版本化组件、Skill 开关、TODO 状态和对应代码证据生成，在线状态分别读取写作、审核、材料润色和运维四个 Bot 的心跳；执行方式直接读取写作和审核任务类型的真实队列注册表，并显示“持久队列”或“实时执行”，不靠人工重复维护。不调用模型自由判断进度。控制台展示全部已知心跳，但不替代运维 Bot 自身的告警服务配置。高层节点关系由代码内明确关系表维护，关系端点必须经过自动化测试确认存在；仓库级测试要求每个已安装 Skill 至少映射到一个状态清单组件。交互画布使用仓库内固定版本的 `vis-network 10.1.0`，不依赖 CDN 或 React 构建链；节点详情和状态清单仍由服务端生成。控制台只监听 `127.0.0.1`。Git 查询是代码内固定的只读命令，不接受页面输入、不自动联网；任务和知识库在总览中只统计数量，不读取用户正文。项目状态仍以代码、核心文档和 Git 为唯一事实来源，控制台不维护第二套状态文件。

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
- 工具编排
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

## 运行链路

公共运行链路：

```text
企业微信或本地入口
  -> 标准化消息和材料组装
  -> SkillRegistry / 路由 / 权限
  -> 任务目录和受限 ToolGateway
  -> Skill workflow / Pydantic AI
  -> 状态、会话和检查点
  -> 文字或附件交付
```

当前入口：

- `app/writing/`：直报、简报、综合调研、深银协动态、内参周报，并可路由文字润色。
- `app/rewrite_bot/`：只开放材料润色。
- `app/review/`：保持独立审核入口。
- `app/admin/`：只监听本机的项目控制台。

持久任务生效范围必须按具体 handler 判断：审核八类能力、直报、writer1、writer2 和 shenyinxie_news 已接入专用队列；其他能力不能仅因公共执行器存在就宣称支持进程恢复和发送幂等。

能力范围见 `docs/capabilities/`，入口运行见 `docs/operations/bots.md`，未完成演进统一见 `docs/development/TODO.md`。
