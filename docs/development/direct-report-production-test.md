# 直报 Bot 生产级测试方案

## 测试目标

本轮生产级测试只验证一条主链路：

```text
直报企业微信 Bot
  -> app/writing/bot.py
  -> app/platform/app.py
  -> skills/direct_report
  -> web_reader
  -> Pydantic AI llm_writer
  -> 企业微信回复
  -> ../M-Agent-Files/tasks/writing/ 任务记录
```

本轮不重点测试：

- 企业微信文件下载。
- Word/PDF 直报写作。
- 复杂多轮任务。
- 审核 skill。
- 搜索工具真实联网质量。

## 当前代码状态

直报 Bot 入口已经改为使用新底座：

```text
app/writing/bot.py
```

关键函数：

```text
build_platform_config
handle_text_with_platform
```

直报 Bot 仍使用：

```text
WRITING_BOT_ID
WRITING_BOT_SECRET
```

但实际写作执行已经走：

```text
PlatformApp + skills/direct_report
```

截至 2026-07-07，已通过真实企业微信文本链路验证：

- 用户发送可读取链接后，可以生成直报。
- 用户继续发送“精简一下”等短修改要求，可以基于上一稿改稿。
- DeepSeek 写作底座当前实际走 OpenAI 兼容 `/v1` 通道；4096 输出上限和 thinking mode 关闭逻辑已经跑通。审核 Bot 的 DeepSeek Anthropic 通道是独立配置。

## 测试前配置

`.env` 至少需要：

```text
WRITING_BOT_ID=...
WRITING_BOT_SECRET=...
MODEL_NAME=MiniMax-M2.7
MODEL_BASE_URL=https://api.minimaxi.com/anthropic
MODEL_API_KEY=...
M_AGENT_MODEL_MAX_TOKENS=4096
M_AGENT_DATA_DIR=../M-Agent-Files
```

写作底座优先读取 `MODEL_*`。旧的 `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` 只作为兼容兜底。

如果切到 DeepSeek 写作模型，可配置：

```text
MODEL_NAME=deepseek-v4-flash
MODEL_BASE_URL=https://api.deepseek.com/v1
MODEL_API_KEY=...
M_AGENT_MODEL_MAX_TOKENS=4096
```

建议生产级测试前增加权限文件：

```text
M_AGENT_PLATFORM_POLICY=config/platform-policy.yaml
```

可从样例复制：

```text
config/platform-policy.example.yaml
```

并把测试用户企业微信 userid 加进去。

## 启动前检查

先运行：

```bash
python -m app.writing.bot --check-config
```

通过标准：

- 显示“配置检查通过”。
- Bot ID 已脱敏显示。
- Skills 目录指向 `skills`。
- 任务目录指向 `../M-Agent-Files/tasks/writing`。
- 权限配置如果是正式测试，应显示已配置的 policy 路径。

再运行自动化测试：

```bash
pytest tests/test_writing_platform_bot.py tests/test_platform_app.py tests/test_platform_wecom_gateway.py tests/test_direct_report_workflow.py -v
```

通过标准：

- 全部通过。

## 启动命令

```bash
python -m app.writing.bot
```

建议正式测试时把输出保存到日志，例如：

```bash
python -m app.writing.bot 2>&1 | tee logs/direct-report-bot.log
```

## 企业微信测试用例

### 用例 1：正常链接写直报

发送：

```text
帮我根据这个链接写直报：https://baijiahao.baidu.com/s?id=1867680135222903517&wfr=spider&for=pc
```

通过标准：

- Bot 先快速回复“收到，正在按直报写作流程处理，请稍后……”。
- 稍后收到标题、正文、来源。
- `../M-Agent-Files/tasks/writing/YYYY/MM/` 下新增一个任务目录。
- 任务目录中有 `meta.json` 和 `output/result.json`。
- `result.json` 中 `skill_id` 为 `direct_report`。

### 用例 1A：通过本机素材页上传直报素材

前提：

- 本机已启动 `python -m app.writing.bot`
- `--check-config` 输出中能看到 `素材入口`

操作：

1. 在运行 Bot 的本机浏览器中打开直报素材页。
2. 上传一个 Word/PDF 或粘贴一个链接。
3. 补充一句写作要求并提交。

通过标准：

- 页面提示“已提交，处理结果会返回企业微信对话”。
- 企业微信会话先收到“已收到写直报素材，正在处理，请稍后……”。
- 稍后收到标题、正文、来源。
- `../M-Agent-Files/tasks/writing/YYYY/MM/` 下新增一个任务目录，`input/` 中能看到上传文件。

### 用例 2：空消息或无效消息

发送空白消息或仅空格。

通过标准：

- Bot 回复“请发送网页链接或文字素材，我会根据需求选择对应写作流程处理。”
- 不调用模型。

### 用例 3：无链接但说写直报

发送：

```text
帮我写一篇直报
```

当前预期：

- Bot 应追问用户提供网页链接、Word 文件或 PDF 文件。

说明：企业微信聊天框已接入 `message.file`，当前支持 Word(.docx) 和 PDF(.pdf)，单文件上限 20MB；文件入口仍位于 `app/writing/` 适配层，尚未下沉为底座公共网关。欢迎语默认不发送本机素材页链接。

### 用例 4：未授权用户

前提：

- 配置 `M_AGENT_PLATFORM_POLICY`。
- 测试用户不在 `direct_report` 允许列表中。

通过标准：

- Bot 回复“你没有使用该能力的权限。”
- 不调用网页读取和模型。

### 用例 5：网页读取或模型失败

发送一个不可访问链接。

通过标准：

- Bot 对用户只返回安全错误文案。
- 日志记录错误类型，不能向用户暴露密钥、堆栈、内部路径。

## 通过生产级测试的最低标准

全部满足才算通过：

1. `--check-config` 通过，且敏感配置脱敏显示。
2. 自动化测试通过。
3. 企业微信正常链接用例成功。
4. 任务目录和结果文件生成正常。
5. 未授权用户不能调用 direct_report。
6. 失败场景不会泄露内部错误。
7. 旧审核 Bot 测试仍通过：

```bash
python tests/test_review_bot.py
```

## 当前风险

- 企业微信长连接已经完成文本链路验证，但还需要继续扩大经授权链接样本。
- 当前直报 Bot 的文本、链接及聊天框 Word/PDF/PPTX 文件消息均已接入；文件由 `app/writing/` 适配层下载和持久化组装，再交给底座统一文档服务处理。PPTX 当前仅作为写作素材读取，不代表已支持 PPT 视觉审核。
- 当前 direct_report workflow 可把不少于 30 字的纯文本作为直接素材；过短、缺少有效信息的内容会继续追问。
- 权限文件尚未默认启用；正式测试前应配置 `M_AGENT_PLATFORM_POLICY`。
- 部分站点在本机环境下无法稳定读取。例如 `www.cet.com.cn` 测试链接 HTTPS 返回 `SSL_ERROR_SYSCALL`，HTTP 返回 `Empty reply from server`。这类问题归为网页读取工具兜底能力不足，不应误判为模型失败。
