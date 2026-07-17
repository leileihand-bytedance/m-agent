# DeepSeek 原生联网搜索接入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 M-Agent 公共 `search` 工具从错误的 DeepSeek + MiniMax 路径组合，改为通过 DeepSeek Anthropic Messages API 的 `web_search_20250305` 原生联网搜索，并保持深银协工作流现有接口不变。

**Architecture:** `build_platform_tools()` 继续向 Skill 暴露统一的 `search(query, max_results)`。`app/platform/builtin_tools.py` 根据搜索基础地址识别供应商：DeepSeek 调用 `/anthropic/v1/messages` 并解析 `web_search_tool_result`；MiniMax 保留 `/v1/coding_plan/search` 兼容路径；未知供应商明确拒绝。深银协 Skill 继续用搜索结果 URL 调用 `web_reader` 核验原文日期和正文。

**Tech Stack:** Python 3.13.14、urllib、Anthropic Messages 兼容协议、Pydantic、pytest、uv。

## Global Constraints

- 所有命令使用 `uv run --locked ...`。
- 先写失败测试，确认失败原因正确后再修改生产代码。
- 不输出或记录 API Key，不把真实搜索正文写入仓库。
- Skill 只能通过 `ToolGateway` 调用 `search` 和 `web_reader`。
- 同步更新底座、能力、Skill 和配置示例文档，并通过项目文档闸门。
- 不改动旧审核 Bot 的独立搜索实现。

---

### Task 1: 用测试固定 DeepSeek 搜索协议

**Files:**
- Modify: `tests/test_platform_builtin_tools.py`
- Modify: `tests/test_platform_app.py`

**Interfaces:**
- Consumes: `search_web(query, api_key, base_url, max_results, requester)`。
- Produces: DeepSeek Messages 请求格式、搜索结果规范化和模型名透传的回归约束。

- [x] **Step 1: 写 DeepSeek 请求和结果解析失败测试**

  覆盖请求 URL 为 `https://api.deepseek.com/anthropic/v1/messages`，工具类型为 `web_search_20250305`，响应中的 `web_search_result` 转换为现有 `url/title/snippet/source` 结构。

- [x] **Step 2: 写供应商边界失败测试**

  覆盖 MiniMax 旧通道继续可用、未知模型供应商不会被错误拼接成 MiniMax 搜索路径。

- [x] **Step 3: 写平台模型名透传失败测试**

  覆盖 `build_platform_tools()` 把 `PlatformConfig.model_name` 传入公共搜索工具。

- [x] **Step 4: 运行失败测试**

  Run: `uv run --locked pytest tests/test_platform_builtin_tools.py tests/test_platform_app.py -k "search or build_platform_tools" -v`

  Expected: 新增 DeepSeek 用例因当前仍请求 `/v1/coding_plan/search` 或缺少模型参数而失败；原有 MiniMax 用例通过。

---

### Task 2: 实现 DeepSeek 原生 Web Search 适配

**Files:**
- Modify: `app/platform/builtin_tools.py`
- Modify: `app/platform/app.py`

**Interfaces:**
- Consumes: `api_key: str`、`base_url: str`、`model_name: str`、`query: str`、`max_results: int`。
- Produces: `list[dict[str, str]]`，每项包含 `url`、`title`、`snippet`、`source`。

- [x] **Step 1: 增加供应商识别和 DeepSeek Messages URL 规范化**

  DeepSeek 始终使用同一主机下的 `/anthropic/v1/messages`；MiniMax 继续使用 `/v1/coding_plan/search`；其他主机抛出可理解的“不支持搜索”错误。

- [x] **Step 2: 构造 DeepSeek Web Search 请求**

  请求包含当前模型、有限输出、用户查询，以及 `{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}`。认证使用 `x-api-key`，不记录密钥。

- [x] **Step 3: 解析服务器搜索结果**

  遍历 `web_search_tool_result.content`，仅接收带 HTTP/HTTPS URL 的 `web_search_result`，规范化为现有结果结构，按 `max_results` 截断。

- [x] **Step 4: 从平台工具构造器透传模型名**

  `build_platform_tools()` 调用 `search_web()` 时传入 `config.model_name`。

- [x] **Step 5: 运行专项测试确认转绿**

  Run: `uv run --locked pytest tests/test_platform_builtin_tools.py tests/test_platform_registry.py tests/test_platform_router.py tests/test_writing_platform_bot.py tests/test_shenyinxie_news_date.py tests/test_shenyinxie_news_whitelist.py tests/test_shenyinxie_news_selection.py tests/test_shenyinxie_news_workflow.py tests/test_shenyinxie_news_docx.py -v`

  Expected: 全部通过。

---

### Task 3: 同步 Skill 与核心文档

**Files:**
- Modify: `skills/shenyinxie_news/SKILL.md`
- Modify: `app/config.example.env`
- Modify: `docs/development/architecture.md`
- Modify: `docs/agent-platform/README.md`
- Modify: `docs/capabilities/README.md`
- Modify: `docs/development/README.md`
- Modify: `docs/development/TODO.md`
- Modify: `docs/development/testing-and-delivery.md`

**Interfaces:**
- Consumes: 已验证的 DeepSeek 搜索行为。
- Produces: 与实际运行一致的搜索供应商、配置回退、异常边界和测试命令说明。

- [x] **Step 1: 更新深银协 Skill 搜索说明**

  明确当前默认通过 ToolGateway 使用 DeepSeek 原生 Web Search，原文仍由 `web_reader` 二次核验。

- [x] **Step 2: 更新配置示例和底座架构**

  说明使用 DeepSeek 模型配置时无需 MiniMax 搜索地址；`SEARCH_API_*` 仅用于显式覆盖兼容供应商。

- [x] **Step 3: 更新能力、状态、TODO 和测试文档**

  记录深银协主动检索已切换 DeepSeek 原生通道，并列出专项回归命令。

- [x] **Step 4: 运行文档闸门**

  Run: `uv run --locked python scripts/project_docs.py check`

  Expected: 通过且无核心文档缺口。

---

### Task 4: 真实联调与交付

**Files:**
- No repository file changes during live verification.

**Interfaces:**
- Consumes: 当前 `.env` 中已存在的 DeepSeek 模型配置。
- Produces: 不含密钥和全文的真实搜索验证证据。

- [x] **Step 1: 运行一次公共搜索工具真实联调**

  使用公开查询、一次 Web Search 和最多 5 条结果；只输出结果数量、来源域名和标题，不输出密钥。

- [x] **Step 2: 运行全仓离线测试（排除真实旧审核模型用例）**

  Run: 使用 `docs/development/testing-and-delivery.md` 当前登记的离线回归命令。

- [x] **Step 3: 检查工作区和文档闸门**

  Run: `git status --short`

  Run: `uv run --locked python scripts/project_docs.py check`

- [ ] **Step 4: 按项目规范提交并受管推送**

  创建单一逻辑提交，使用 `scripts/project_docs.py push` 推送 `main`，随后运行 `check-sync`。不得输出密钥、用户材料或真实任务正文。
