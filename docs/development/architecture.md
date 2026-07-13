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
- `app/platform/cli.py`：新底座 CLI，可做配置检查和本地消息测试。
- `app/platform/app.py`：平台应用服务，把路由、权限、任务记录、runtime 串起来。
- `app/writing/bot.py`：当前写作 Bot 的真实企业微信入口适配层，已经调用 `PlatformApp`。
- `app/writing/intake.py`：写作 Bot 的短任务组装层，负责把用户分多条发送的意图、链接、文字和文件组装成一次结构化写作请求。
- `app/review/main.py`：审核 Bot 独立入口；普通文件按原有内容审核分流，用户明确要求“格式审核”时只把下一份有效 `.docx` 交给确定性公文格式检查器，不把该能力自动接入通用内容审核。

后续新增：

- 底座级文件消息和多消息任务组装：把写作 Bot 已验证的能力下沉到公共入口，统一处理暂存、超时、显式开始、取消、格式、大小和任务隔离，供写作与多文件审核复用。
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

### 8. 任务存储层

位置：

```text
app/platform/storage.py
```

职责：

- 每次用户请求创建独立 job 目录。
- 固定分为 `input/`、`work/`、`output/`。
- 写入 `meta.json` 和 `output/result.json`。
- 保留按用户和入口查找最近一次成功输出的兜底能力。
- `meta.json` 同时保存 `sender_userid` 和 `sender_name`，便于把企业微信内部 ID 对应到真实使用者。
- 只在 `meta.json` 中保存截断后的消息预览，避免把长正文和敏感材料直接写入元信息。

默认任务目录（由 `M_AGENT_DATA_DIR` 派生）：

```text
../M-Agent-Files/tasks/writing/YYYY/MM/<job_id>/
```

运行数据根目录位于 Git 仓库之外；每个任务仍固定包含 `input/`、`work/`、`output/` 和 `meta.json`。

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
- 独立运维 Bot 通过 `python -m app.platform.ops.bot` 长期运行。
- 运维 Bot 使用独立企业微信机器人凭证，不依赖写作 Bot 或审核 Bot 的长连接。
- 运维 Bot 轮询当天的 `ops_events`，把未通知过的异常和提醒发送给管理员。
- 实时通知不回看昨天或前一个工作日，避免 Bot 重启、周一启动或运行数据迁移后逐条补发历史告警；历史事件统一进入工作日报。
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
```

其中 `writer1` / `writer2` 已启用，已补齐 workflow、schema 和业务测试，并已接入 `bank_materials` 微众银行材料包和共享 `policy_research` 政策判断层；兼容层仍保留 `policy_materials` 作为低层政策材料包工具。当前简报真实口径已明确为面向深圳市金融办、南山区、前海管理局、深圳人行、深圳金监局等地方政府和监管部门，重点展示微众银行近期动态及成果，篇幅正常控制在 `1000` 字左右、最长不超过 `1200` 字。当前已新增简报共享质量层：写作前会注入隐藏规划，并按经授权样本识别综合成果型、机制成果型、产品工具型、平台合作型、标准引领型、能力建设型、外部认可型、活动亮相型、专项治理型等类型；多素材会先判断是否弱关联，生成后会做确定性规则检查和语义审查；后续仍需继续通过经授权样本收敛成稿质量和多素材整合表达。

每个 skill 自己负责业务流程、prompt 和结构化 schema，但不能绕过底座权限。

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
python -m app.platform.demo "帮我根据这个链接写直报：https://..."
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
```

该能力目前是本机单进程内存态，默认暂存 1800 秒，可用 `M_AGENT_WRITING_INTAKE_TTL` 调整。它解决的是短任务连续消息场景，不替代 `ConversationStore` 的上一稿改稿能力。

尚未完成：

- 底座级企业微信文件下载、多消息/多文件任务组装和文件安全策略。
- 一个覆盖直报、简报、审核等能力的统一企业微信 Bot。
- 群权限。
- 复杂多轮会话存储。

已完成 v1：

- 同一用户在同一入口下，基于上一轮 M-Agent 生成的直报/简报初稿继续提出修改要求。
- 写作入口支持“先发材料后说用途”“先说用途后发材料”“发完回复开始写”的短任务组装。

## 后续演进顺序

建议顺序：

1. 先建立审核真实样本质量基线，不在效果未稳定时继续铺开文件类型。
2. 补通用 Word 格式审核。
3. 把写作 Bot 的文件下载和短任务组装能力下沉为底座公共能力，并扩展为审核可复用的多文件任务组装。
4. 在公共组装层之上实现“正文 + 多附件”联合审核。
5. 完成大文件回传后增加 PPT 审核。
6. 直报、简报和改稿保持样本驱动迭代；统一企业微信入口和 `review` skill 包装暂不作为近期前置条件。
7. 需要复杂长任务时再引入 LangGraph。
