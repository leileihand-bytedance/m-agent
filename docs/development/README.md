# M-Agent 后续开发总指南

本文档给 Codex、Claude Code 和后续开发者使用。目标是让后续开发可以通过自然语言持续推进，同时不破坏现有功能。

## 一句话结论

M-Agent 后续采用：

```text
M-Agent 自建底座
  + Pydantic AI 运行层
  + 文件化 skills
  + 受限工具层
  + 企业微信统一入口
```

它不是 Dify 这样的可视化平台，也不是 Hermes/OpenClaw 那种个人助手系统。它是一个代码型、可测试、适合 Codex/Claude Code 持续开发的业务 agent 底座。

## 当前重点

已经跑通的最小链路：

```text
本地 CLI / 本地 demo 输入自然语言
  -> 路由到 direct_report
  -> 创建独立任务目录
  -> 校验用户是否有 skill 权限
  -> 读取用户 URL
  -> Pydantic AI Agent 生成结构化结果
  -> 写入任务结果
  -> 输出标题、正文、来源
```

底座当前还具备：

- 直报 Bot 真实企业微信文本入口适配。
- 企业微信文本消息核心适配。
- 写作 Bot 短任务组装 v1：支持用户先发材料再说明用途，或先说明用途再连续发送多个材料，最后回复“开始写”。
- 写作 Bot 文件消息接收 v1：可把企业微信文件暂存为结构化请求材料，后续仍需下沉为底座公共文件能力。
- 网页、Word、PDF 基础读取工具。
- 用户/skill 权限策略。
- 共享用户名称映射表，写作 Bot 和审核 Bot 都能把企业微信 `userid` 对应到可读用户名。
- 每次请求独立 job 目录。
- 平台级会话状态和稿件版本链 v1。
- 开发期写作对话日志，可用于前期问题排查，稳定后可关闭。
- CLI 配置检查。
- 本机管理后台。

后续重点：

1. 优化直报写作质量。
2. 优化 `writer1` / `writer2` 简报成稿质量和多素材整合规则。
3. 把写作 Bot 的文件下载和短任务组装能力下沉为底座公共能力。
4. 把更多 skill 接入当前企业微信入口。
5. 把现有审核能力包装为 review skill。
6. 增加多轮改稿上下文。
7. 继续完善本机管理后台。

## 当前进度快照

更新时间：2026-07-13

写作入口当前状态：

- 直报 Bot 已接入新底座，真实企业微信文本链路已经验证。
- DeepSeek 写作底座当前实际使用 OpenAI 兼容 `/v1` 通道，推荐使用 `MODEL_*` 配置；审核 Bot 的 DeepSeek 配置仍是独立的 Anthropic 兼容通道。
- 已修复模型输出上限硬编码 `2048` 导致直报“处理失败”的问题，默认输出上限为 `4096`。
- 已修复 `deepseek-v4-flash` thinking mode 与结构化输出不兼容的问题。
- 直报 critic 默认改为 `advisory` 模式：做语义质检并提示风险，但不因 critic 自动重写，避免质检层增加真实 Bot 失败概率。
- 直报生成和“精简一下”等上一稿改稿已通过真实链路验证。
- 已新增平台级 `ConversationStore` 和 `intent` 意图分类器，直报和简报改稿共用同一套活跃稿件、版本链和改稿/新任务判断。
- 已增强改稿执行协议：上一稿材料不再被 2000 字过早截断，且会对“只改标题”“不要拆段”“改变原文意思”等要求加入局部编辑约束。
- 已新增独立 `rewrite` skill v1：支持用户直接贴文字做材料润色，默认只优化表达、不新增事实，并可继续对话式修改。
- 已补“新润色任务”和“旧稿续改”的切割规则：用户明确贴新正文要求润色时，即使当前有活跃直报/简报会话，也会新开 `rewrite`，不会误改上一稿。
- 已新增开发期 `ChatLogStore`，记录完整用户输入、即时提示、最终回复、意图、skill、job、用户名和版本信息。
- 已将审核 Bot 原有用户名注册表抽到 `app/platform/user_registry.py`，写作 Bot 和审核 Bot 共用外部运行数据目录中的用户名表。
- 写作 Bot 的即时提示已改为按实际 skill 动态变化：直报、单素材简报、多素材简报不再共用“直报写作流程”话术。
- 已新增独立运维 Bot：异常和链接读取失败等事件写入统一运行数据目录，运维 Bot 独立长连接发送告警，并在每个工作日 9:00 发送前一工作日日报；写作 Bot 和审核 Bot 已接入心跳监控。
- 已将用户上传、系统生成、知识库、日志和运行状态统一剥离到桌面 `M-Agent-Files/`；代码仓库不再承载真实运行数据。写作和审核任务均按年/月归档，审核原件与标注结果分别存入 `input/` 和 `output/`。
- 本机首次迁移已通过逐文件 SHA-256 校验；旧目录已移入 `M-Agent-Files/legacy/pre-migration-source/` 作为临时回滚备份，没有删除历史材料。
- 不可读取网页会返回安全提示，不再把底层 curl 错误直接暴露给用户。
- 简报写作已支持链接读取失败兜底：有链接读取失败时先询问用户，是继续使用已读取素材写，还是粘贴失败链接正文后再一起写。
- 已建立直报质量回归测试集 v1，固定使用 4 个用户提供的公开链接做质量对比。
- 已固化主体称谓：直报全篇只写“微众银行”；简报首次写“深圳前海微众银行（以下简称“我行”）”，后文写“我行”。
- 简报当前真实口径已明确：报送对象为深圳市金融办、南山区、前海管理局、深圳人行、深圳金监局等地方政府和监管部门，主要展示微众银行近期动态及成果；篇幅正常控制在 `1000` 字左右，最长不超过 `1200` 字。
- 已按经授权样本沉淀第一版简报类型分类和 A 类样本写法：综合成果型、机制成果型、产品工具型、平台合作型、标准引领型、能力建设型、外部认可型、活动亮相型、专项治理型。
- 已新增直报政策挂接闸门：案件、活动、获奖、直播、判决等时间节点稿件默认直入主题，不额外注入政策库材料；产品、机制、长期服务类素材才允许补充政策背景。
- 已新增共享政策研究层 v1：`direct_report`、`writer1`、`writer2` 统一通过 `policy_research` 判断“能不能挂、挂哪条、为什么、摘哪一句”，政策库已补启用状态和标签治理字段。
- 已新增写作 Bot 短任务组装层 `app/writing/intake.py`：支持“先发材料后说用途”“先说用途后发材料”“多条链接/文字/文件发完后回复开始写”，并通过 `PlatformApp.handle_structured_request(...)` 进入对应 skill。
- 写作 Bot 已注册 `message.file` 入口，Word/PDF 文件可作为直报、简报的本次任务材料；独立 `rewrite` v1 仍只支持直接粘贴文字。当前文件能力仍是写作入口 v1，后续要下沉为底座级公共能力。

最近真实测试结论：

- `baijiahao.baidu.com` 测试链接可以成功生成直报。
- `www.cet.com.cn` 测试链接在本机环境下 HTTPS 返回 `SSL_ERROR_SYSCALL`，HTTP 返回 `Empty reply from server`。这属于网页读取失败，不是模型失败。

下一步建议：

1. 继续按 `TODO-001` 收敛直报写作质量。
2. 按 `TODO-013` 做直报案例细节压缩和机制提炼。
3. 按 `TODO-012` 增强网页读取兜底能力。
4. 按 `TODO-003` 把写作 Bot 的短任务组装和文件处理能力下沉为底座公共能力。

## 当前待办

当前待办统一维护在：

```text
docs/development/TODO.md
```

规则：

- `TODO.md` 记录当前还没完成、需要后续推进的事项。
- Git 提交和核心文档记录真实项目状态；`STATUS-REPORT.md` 仅作为本机自动开发日志，不进入 Git。
- 新增、完成、暂缓或取消待办时，必须同步更新 `TODO.md` 和相关模块文档，不以本机状态报告代替核心文档。

## 文档地图

```text
docs/
├── README.md                         # 文档分区总览
├── archive/                          # 历史方案，不作为新开发依据
├── development/
│   ├── README.md                     # 本文件
│   ├── admin-console.md              # 本机管理后台说明
│   ├── architecture.md               # 整体架构说明
│   ├── bank-knowledge-base.md        # 微众银行信息库说明
│   ├── codex-claude-workflow.md      # 用 Codex/Claude Code 开发的流程
│   ├── direct-report-production-test.md # 直报 Bot 生产级测试方案
│   ├── direct-report-quality-phase1.md # 直报质量第一阶段规则说明
│   ├── direct-report-quality-regression-v1.md # 直报质量回归测试集 v1
│   ├── direct-report-quality-review-20260707.md # 4 个直报样本人工评审记录
│   ├── directory-standard.md         # 目录和文件规范
│   ├── policy-knowledge-base.md      # 政策知识库与直报政策研究层说明
│   ├── status-report.md              # 本地状态日志和自动记录机制
│   ├── TODO.md                       # 当前待办统一入口
│   └── testing-and-delivery.md       # 测试和交付规范
├── agent-platform/
│   └── README.md                     # 底座区规划
└── capabilities/
    └── README.md                     # 功能区/skills 规划
```

## 开发前阅读顺序

通用开发：

1. `AGENTS.md` 或 `CLAUDE.md`
2. `docs/development/README.md`
3. `docs/development/architecture.md`
4. `docs/development/codex-claude-workflow.md`
5. `docs/development/TODO.md`

改底座：

1. `docs/agent-platform/README.md`
2. `docs/development/directory-standard.md`
3. 相关 `app/platform/` 文件

改 skill：

1. `docs/capabilities/README.md`
2. 对应 `skills/<skill_id>/SKILL.md`
3. 对应 `skills/<skill_id>/config.yaml`
4. 对应测试文件

## 后续自然语言开发示例

新增 skill：

```text
新增一个“简报写作”skill。
输入是网页链接或 Word/PDF 文件。
输出包括标题、摘要、正文、来源。
请按 direct_report 的结构实现，并补测试。
```

优化 skill：

```text
优化 direct_report skill。
现在生成内容太像新闻稿，需要更像国办直报材料。
请调整 SKILL.md、prompts/draft.md 和测试。
```

新增工具：

```text
新增 pdf_reader 工具。
只能读取用户本次上传的 PDF，不能读取本机其他目录。
让 direct_report、writer1 和 writer2 可以声明使用它。
```

接企业微信：

```text
新增统一企业微信入口。
收到文本或链接后走 app/platform 的路由和 runtime。
先只开放 direct_report。
不要影响现有 app/review Bot。
```

检查新底座配置：

```text
运行 python -m app.platform.cli --check-config。
如果配置正常，再用一条本地消息测试 PlatformApp。
```

## 不要做的事

- 不要把所有能力写进一个大 prompt。
- 不要让模型自由决定调用任意工具。
- 不要在 skill 中直接读取 `.env`。
- 不要让外部用户访问本机任意文件。
- 不要一上来重写 `app/review/` 或 `app/writing/`。
- 不要为了跑 demo 把密钥写进代码。
