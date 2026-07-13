# M-Agent 底座区规划

## 定位

底座区负责 M-Agent 的公共运行能力。它不直接决定一篇直报怎么写、一个文档怎么审，而是负责：

- 接收企业微信消息
- 判断用户要做什么
- 检查用户是否有权限
- 找到对应 skill
- 给 skill 分配允许使用的工具
- 保存任务记录
- 把结果安全返回给用户

一句话概括：

> 底座区是 M-Agent 的运行框架，功能区是 M-Agent 的业务能力。

## 为什么需要单独建设底座区

现有 M-Agent 已经有审核、写作和早期 agent 代码，但它们更多是独立模块：

```text
app/review/     # 审核 Bot
app/writing/    # 写作 Bot
```

如果继续每新增一个能力就做一个独立 Bot，后续会出现几个问题：

1. 用户入口分散，不知道该找哪个 Bot。
2. 每个功能都要重复处理企业微信、文件、权限、日志。
3. 多轮改稿和任务上下文难以复用。
4. 安全边界不统一，容易让某个功能越权访问本机资源。
5. Codex / Claude Code 后续维护时，缺少清晰边界。

因此需要新建底座区，把公共能力沉淀出来。

## 推荐技术路线

底座区推荐采用：

```text
Python
+ Pydantic AI
+ MCP / 现成工具
+ 文件化 Skill Registry
+ 后续按需引入 LangGraph
```

### Pydantic AI 的角色

Pydantic AI 负责：

- 调用大模型
- 注册工具
- 约束结构化输入输出
- 把每个 skill 的执行过程封装成可测试的 Python 逻辑

当前已接入：

```text
app/platform/pydantic_runtime.py
skills/direct_report/schema.py
```

`direct_report` 已经通过 Pydantic AI Agent 返回结构化 `DirectReportResult`。

选择它的原因：

- 和当前 M-Agent 的 Python 代码一致，迁移成本低。
- 适合被 Codex / Claude Code 通过自然语言继续开发。
- 比完整平台更轻，不需要大量界面配置。
- 比个人助手框架更容易控制工具权限。

### MCP / 现成工具的角色

MCP 和现成库用于接入基础能力：

- 联网搜索
- 网页读取
- Word 读取
- PDF 读取
- PPTX 读取
- 知识库或数据库
- 其他外部服务

但 MCP 和工具必须经过底座区授权，不能直接暴露给用户。

### LangGraph 的角色

第一阶段不建议直接引入 LangGraph 作为主框架。

后续当出现复杂长任务时，再引入 LangGraph，例如：

- 多轮改稿
- 长流程审核
- 多步骤写作
- 人工确认后继续执行
- 任务中断后恢复

## 未来代码目录建议

底座区当前已建立基础目录：

```text
app/platform/
├── __init__.py
├── app.py
├── builtin_tools.py
├── cli.py
├── config.py
├── demo.py
├── documents/            # DOCX/PDF/PPTX 统一安全解析和标准材料结构
├── gateway/
│   ├── __init__.py
│   └── wecom.py
├── identity.py
├── models.py
├── pydantic_runtime.py
├── registry.py
├── router.py
├── runtime.py
├── storage.py
└── tools.py
```

后续正式扩展时，建议逐步新增：

```text
app/platform/
├── gateway/             # 企业微信入口适配
├── identity/            # 用户身份、白名单、权限
├── storage/             # 任务记录、会话记录、临时文件
├── safety/              # 工具授权、文件隔离、越权拦截
└── logging/             # 审计日志
```

这些目录只放公共能力，不放具体业务写作规则。

## 底座区模块说明

### 1. 企业微信入口

当前位置：

```text
app/platform/gateway/wecom.py
```

职责：

- 接收企业微信文本、链接、文件消息。
- 下载用户上传的文件。
- 把用户、群、消息、附件整理成统一请求。
- 把最终结果返回企业微信。

原则：

- 继续优先复用当前已经跑通的企业微信 SDK 经验。
- 不在入口层写具体业务逻辑。
- 入口层只做接收、回复、转发和基础校验。

当前已完成文本消息核心：

```text
extract_text_message     # 从企业微信消息结构中取出文本和用户 ID
handle_text_frame        # 调用平台 runner
format_text_reply        # 把 PlatformResult 整理成回复文本
handle_text_frame_with_app # 直接调用 PlatformApp
```

当前写作 Bot 还新增了一个轻量素材入口页：

```text
企业微信欢迎语
  -> 引导用户直接发送链接，并说明是写简报还是写直报
  -> 多个素材文档建议先整合成一个文档后再发送

本机素材页
  -> 收集文件、链接和补充要求
  -> PlatformApp 结构化提交
  -> 结果主动回企业微信对话
```

当前写作 Bot 已经通过 `app/writing/bot.py` 接入真实 AiBotSDK 长连接，并调用 `PlatformApp`。后续要做的是把更多 skill 接入统一入口，而不是再新增独立业务 Bot。

写作 Bot 当前还具备短任务组装 v1：

```text
先发链接/文字/文件 -> 后说“写简报/写直报”
先说“帮我写简报” -> 连续发多个材料 -> 回复“开始写”
直接粘贴文字 -> 可选择独立 `rewrite` 润色
```

该能力由 `app/writing/intake.py` 实现，默认 1800 秒过期。状态和文件持久化到 `M-Agent-Files/runtime/intake/`，Bot 重启后可恢复有效会话，任务提交或过期后清理。后续仍需把接口下沉为 `app/platform/` 公共入口能力，供审核多文件会话和多进程部署复用。

### 2. 身份和权限

当前位置：

```text
app/platform/identity.py
config/platform-policy.example.yaml
```

职责：

- 识别企业微信用户 ID。
- 判断用户是否在白名单。
- 判断用户所在群是否允许使用某些能力。
- 控制不同用户可调用的 skill 范围。

第一阶段可以简单做成配置文件：

```yaml
allow_unknown_users: false
default_allowed_skills: []

users:
  user_a:
    allowed_skills:
      - direct_report
      - writer1
      - writer2
  user_b:
    allowed_skills:
      - review
```

### 3. 意图识别和路由

职责：

- 判断用户是要写直报、写简报、审核、改稿，还是提出了范围外问题。
- 如果不确定，向用户追问。
- 如果不属于已有能力，拒绝执行。

重要原则：

> 意图识别只能在已登记 skill 中选择，不能让模型自由发明新能力。

### 4. Skill 注册表

职责：

- 扫描 `skills/` 目录。
- 读取每个 skill 的说明、触发条件、输入输出、允许工具。
- 给路由层提供可用能力清单。
- 给运行层提供 skill 执行入口。

注册表应该只接受仓库中已提交、已测试的 skill。

### 5. 运行层

职责：

- 加载 skill。
- 组装 prompt 和上下文。
- 调用 Pydantic AI agent。
- 调用允许的工具。
- 校验输出结构。
- 返回结果。

运行层不直接决定业务风格，业务风格放在 skill 中。

### 6. 受限工具层

基础工具包括：

- `web_reader`：已实现，读取用户给出的网页链接。
- `word_reader`：已实现，读取用户本次任务目录内的 Word 文件。
- `pdf_reader`：已实现，读取用户本次任务目录内的 PDF 文件。
- `document_reader`：已实现，统一安全读取 DOCX、PDF 和 PPTX，返回适合模型使用的标准材料片段，同时把完整解析结果写入任务 `work/documents/`。写作 skill 已优先使用该工具，旧 Word/PDF 工具保留兼容。
- `llm_writer`：已实现，通过 Pydantic AI 调用模型生成文稿。
- `search`：已实现，调用搜索 API 返回标题、摘要、链接和来源类型。
- `policy_research` / `policy_materials` / `policy_search`：已实现，提供共享政策挂靠判断、政策知识库材料包和底层检索。
- `review_engine`：待实现，后续包装现有审核能力。

工具必须遵守：

1. 只能访问本次任务材料。
2. 不能读取 Mac 上的任意目录。
3. 不能执行系统命令。
4. 不能把密钥、配置、日志、历史文件暴露给用户。
5. 每次工具调用都要记录日志。

### 7. 文件隔离

底座运维通知采用“当天实时、历史汇总、同类去重”规则：运维 Bot 只处理当天新事件，前一工作日事件由工作日报汇总，不在重启后逐条补发；同一天内来源、级别、主题和详情完全相同的事件只通知一次，重复次数仍保留在日报中。这样既保留故障记录，也避免重连抖动、迁移或周一重启造成告警刷屏。

每个任务应有独立任务目录：

```text
../M-Agent-Files/tasks/writing/
└── 2026/07/20260703-0001/
    ├── input/
    ├── work/
    ├── output/
    └── meta.json
```

skill 和工具只能访问当前任务目录。

公共文件与文档处理继续统一使用桌面 `M-Agent-Files/`：尚未开始处理的文件暂存在 `runtime/intake/`；任务原件进入 `input/`；DOCX/PDF/PPTX 的完整解析结果进入 `work/documents/`，PPT 提取图片进入对应 `assets/`；后续渲染图片和 OCR 等中间产物也进入 `work/`；最终报告、标注或生成文件进入 `output/`。这些运行文件都不进入 Git。

当前写作材料格式范围是 Word、PDF 和 PPTX。HTML 文件支持已暂缓；现有网页链接读取能力保持不变，不因文档服务建设扩大为 HTML 文件上传或执行能力。

统一文档服务当前边界：原生文字 PDF 可按页解析，疑似扫描 PDF 会记录 `ocr_required` 告警，但尚未自动 OCR；PPTX 可提取文本、表格、图表、备注、图片、对象位置和基础样式，但尚未进行页面渲染、视觉版式审核或批注回传。

当前已由 `app/platform/storage.py` 实现，实际目录名使用时间戳和随机短 ID，避免并发冲突。

## 当前成熟底座形态

当前已经具备：

- 文件化 skill 注册表。
- 关键词路由。
- Pydantic AI 结构化写作运行层。
- 受限工具网关。
- 网页读取，以及 DOCX/PDF/PPTX 统一文档读取工具。
- 企业微信文本消息核心适配。
- 平台应用服务 `PlatformApp`。
- 用户/skill 权限控制。
- 每次请求独立任务目录和结果记录。
- 同一用户、同一入口的上一稿改稿 v1。
- CLI 配置检查和本地消息入口。
- 写作 Bot 真实企业微信文本入口。
- 写作 Bot 短任务组装 v1：支持链接、文字、Word/PDF/PPTX 跨消息组成一次直报/简报请求，待组装文件可在 Bot 重启后恢复；`rewrite` v1 只接收直接粘贴文字。
- 本机管理后台。
- 搜索工具。

仍未完成：

- 跨写作和审核复用的底座级多文件会话组装接口。
- 扫描 PDF 按需 OCR、PDF/PPT 页面渲染和复杂文档后台执行。
- 覆盖直报、简报、审核等能力的统一企业微信入口。
- 审核能力包装为 `review` skill。
- 复杂多轮任务上下文和人工确认。

审核旧模块已接入统一数据根目录，任务写入 `../M-Agent-Files/tasks/review/YYYY/MM/`；原件和系统生成文件不再混放。

### 8. 会话和任务记录

第一阶段已经支持短任务：

```text
用户发链接 -> 生成结果 -> 返回
```

当前已支持“上一稿改稿 v1”：

```text
用户发链接 -> 生成直报/简报初稿
用户继续说“再压缩一点” -> 系统读取同一用户、同一入口最近一次输出 -> 回到上一轮 skill 修改上一稿
```

这个能力只使用上一轮 M-Agent 生成的标题、正文和来源，不读取历史任务目录里的原始文件，也不会把本机其他文件暴露给用户。

后续支持复杂多轮任务时，需要进一步记录：

- 用户是谁
- 用户名和企业微信 userid 的对应关系
- 上一次调用了哪个 skill
- 输入材料是什么
- 输出结果是什么
- 用户这次修改要求是什么

可以先用文件或 SQLite，不急于引入复杂数据库。

当前用户名映射已经作为底座公共能力落在：

```text
app/platform/user_registry.py
../M-Agent-Files/runtime/users/review_users.yaml
```

写作 Bot 和审核 Bot 共用同一份映射表。写作链路会在任务记录、会话记录、开发期对话日志和运行输出中同时保留 `sender_name` 与 `sender_userid`。该文件位于 Git 仓库之外，属于本机敏感运行数据。

### 9. 安全边界

底座区必须坚持：

1. 企业微信用户不是可信用户。
2. 用户发来的网页、文档、文字都可能包含诱导模型越权的内容。
3. 安全不能只靠 prompt。
4. 所有工具必须通过底座授权。
5. 对外只暴露已登记 skill，不暴露本机助手能力。

## 与现有功能的关系

现有功能不立即迁移：

```text
app/review/   # 继续作为现有审核 Bot 运行
app/writing/  # 继续作为现有写作 Bot 运行
```

新底座先独立开发。等底座最小闭环稳定后，再逐个迁移：

1. 先迁移直报写作。
2. 再迁移简报写作。
3. 再迁移审核能力。
4. 最后考虑长期记忆能力。如需参考早期领导风格沉淀实验，只查看 `archive/inactive-2026-07-04/`，不要恢复旧入口。

## 第一阶段最小闭环

第一阶段最小闭环已经在本地 demo 中跑通：

```text
用户在本地 demo 发一个链接
  -> 底座识别为直报写作
  -> 调用 direct_report skill
  -> 读取网页内容
  -> Pydantic AI Agent 生成结构化直报初稿
  -> 输出标题、正文、来源
```

这个闭环可以验证：

- 统一入口
- 意图识别
- skill 注册
- 网页读取工具
- Pydantic AI 运行层
- 本地 demo 输出
- 基础安全边界

统一文档服务 v1 已解决 DOCX/PDF/PPTX 的公共安全解析、标准材料结构、长材料均衡取样和完整结果落盘。下一步先把写作 Bot 已验证的持久化短任务组装接口下沉为底座公共能力，让审核也能复用；再补扫描 PDF 按需 OCR、页面渲染和复杂文件回归，并在此基础上实现多文件联合审核。底座只负责组装和解析本次任务，正文与附件一致性、PPT 版式等业务规则仍由审核模块负责。HTML 文件支持暂缓，统一企业微信入口继续作为长期方向。

## 后续开发工作流

后续你主要通过 Codex / Claude Code 做自然语言开发：

```text
你描述需求
  -> Codex / Claude Code 修改 app/platform 或 skills
  -> 自动补测试
  -> 本地验证
  -> 企业微信手动试用
```

底座区开发主要由工程任务驱动，不频繁改。

功能区开发主要由业务需求驱动，会频繁新增和修改。

## 底座区完成标准

底座区只有满足以下条件才算可用：

1. 能统一接收企业微信消息。
2. 能按用户权限过滤 skill。
3. 能识别意图并路由到已登记 skill。
4. skill 只能调用声明过的工具。
5. 工具只能访问当前任务材料。
6. 每个任务有日志和输出记录。
7. 测试能覆盖路由、权限、工具限制和至少一个真实 skill。
