# Codex / Claude Code 开发工作流

## 基本协作模式

你负责用自然语言描述业务目标，Codex / Claude Code 负责改代码、补测试、更新文档。

推荐表达格式：

```text
我要新增/修改什么能力：
输入是什么：
输出是什么：
允许使用哪些工具：
不允许做什么：
验收标准是什么：
```

## 新增 Skill 的标准流程

示例需求：

```text
新增一个“简报写作”skill。
输入是网页链接或 Word/PDF 文件。
输出包括标题、摘要、正文、来源。
风格正式、简洁，适合领导阅读。
请按 direct_report 的结构实现，并补测试。
```

AI 编程工具应执行：

1. 读取 `docs/capabilities/README.md`。
2. 读取 `skills/direct_report/` 作为模板。
3. 按场景补齐或新增对应 skill，例如 `skills/writer1/` 或 `skills/writer2/`。
4. 写 `SKILL.md`。
5. 写 `config.yaml`。
6. 写 `schema.py`，用 Pydantic 模型定义输出。
7. 写 `workflow.py`，通过 `ToolGateway` 调工具。
8. 写测试。
9. 跑平台测试。
10. 更新文档。

## 修改 Skill 的标准流程

示例需求：

```text
优化 direct_report，使它更像正式报送材料，不要像新闻稿。
```

优先修改：

```text
skills/direct_report/SKILL.md
skills/direct_report/prompts/draft.md
tests/test_direct_report_workflow.py
tests/test_platform_pydantic_runtime.py
```

只有业务流程变了，才修改 `workflow.py`。

只有输出结构变了，才修改 `schema.py`。

## 新增工具的标准流程

示例需求：

```text
新增 pdf_reader，只允许读取用户本次上传的 PDF。
```

AI 编程工具应执行：

1. 在 `app/platform/builtin_tools.py` 或未来 `app/platform/tools/` 中新增工具。
2. 写测试验证正常读取。
3. 写测试验证不能读非任务目录。
4. 在需要的 skill `config.yaml` 中声明工具。
5. 通过 `ToolGateway` 调用，不允许 skill 直接导入工具绕过授权。

当前已实现的基础工具：

```text
web_reader
word_reader
pdf_reader
llm_writer
```

## 修改底座的标准流程

底座修改包括：

- 路由
- 注册表
- runtime
- Pydantic AI 执行层
- 工具授权
- 配置
- 企业微信入口

底座修改必须：

1. 先写测试。
2. 不改变旧功能入口。
3. 不把业务规则写进底座。
4. 不放宽安全边界。
5. 更新 `docs/development/architecture.md` 或 `docs/agent-platform/README.md`。

## 接企业微信的标准流程

接企业微信前，必须先保证本地 demo 可用：

```bash
python -m app.platform.demo "帮我根据这个链接写直报：https://..."
```

企业微信统一入口应放在：

```text
app/platform/gateway/
```

它应该调用：

```text
route_message
SkillRegistry
PlatformRuntime
build_builtin_tools
```

不要把业务规则写进企业微信入口。

当前已存在可测试核心：

```text
app/platform/gateway/wecom.py
tests/test_platform_wecom_gateway.py
```

当前直报 Bot 已经通过 `app/writing/bot.py` 接入真实 AiBotSDK 并调用 `PlatformApp`。后续接更多企业微信入口时，应只新增 SDK 适配代码，把 SDK 消息转成平台可处理的标准输入，不要在 SDK 回调里写路由、写作、审核等业务逻辑。

新底座主入口是：

```text
app/platform/app.py
PlatformApp.handle_text_message(...)
```

真实企业微信文本消息应走：

```text
app/platform/gateway/wecom.py
handle_text_frame_with_app(frame, app)
```

## 常用命令

平台测试：

```bash
pytest tests/test_platform_registry.py tests/test_platform_router.py tests/test_platform_tools.py tests/test_platform_builtin_tools.py tests/test_platform_file_readers.py tests/test_platform_pydantic_runtime.py tests/test_direct_report_workflow.py tests/test_platform_runtime.py tests/test_platform_demo.py tests/test_platform_wecom_gateway.py tests/test_platform_storage.py tests/test_platform_identity.py tests/test_platform_app.py tests/test_platform_cli.py -v
```

底座配置检查：

```bash
python -m app.platform.cli --check-config
```

旧审核 Bot 存档测试：

```bash
python tests/test_review_bot.py
```

真实本地 demo：

```bash
python -m app.platform.demo "帮我根据这个链接写直报：https://..."
```

真实 demo 需要：

- `.env` 中有 `MODEL_NAME`
- `.env` 中有 `MODEL_BASE_URL`
- `.env` 中有 `MODEL_API_KEY`
- 网络可访问网页和模型服务

旧的 `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` 仍作为兼容兜底。新配置优先使用 `MODEL_*`。

## 交付前检查

每次完成前至少检查：

1. 是否改了不该改的旧功能。
2. 是否新增/修改了测试。
3. 是否跑过相关测试。
4. 是否更新了文档。
5. 是否没有输出或提交密钥。
6. 是否运行了 `python scripts/project_docs.py check`。
7. 是否确认 `STATUS-REPORT.md`、`config/platform-policy.yaml`、真实用户材料和本机路径没有进入暂存区。
8. 是否已按逻辑创建提交，并在推送前获取远端最新状态。
9. 是否已推送 `main`，且 `python scripts/project_docs.py check-sync` 显示本地与远端同步。

首次克隆后运行：

```bash
python scripts/project_docs.py install-hooks
```

pre-commit hook 会读取暂存区版本，检查 TODO 编号和状态、本机文件/路径，并按模块要求代码、依赖、hooks、配置同步对应核心文档；无关计划文档不能充当核心文档。post-commit hook 会把提交摘要自动写入本机 `STATUS-REPORT.md`，并提醒本地未推送提交；pre-push hook 会再次运行核心文档检查。行为已变化但核心文档未同步，或本地提交尚未推送且没有说明时，不允许交付。

## 推荐给 AI 的提示词

新增能力：

```text
请按 M-Agent 新架构新增一个 skill。
先阅读 AGENTS.md、docs/development/README.md、docs/development/TODO.md、docs/capabilities/README.md。
遵守底座区/功能区边界，测试先行，不要改旧 app/review 和 app/writing。
```

修改底座：

```text
请修改 M-Agent 底座。
先阅读 AGENTS.md、docs/development/architecture.md、docs/development/TODO.md、docs/agent-platform/README.md。
测试先行，保留 ToolGateway 安全边界，改完跑平台测试。
```
