# app/rewrite_bot

独立企业微信材料润色 Bot 的入口适配层。它不包含润色业务规则，只负责把文字消息安全交给现有 `skills/rewrite/`。

## 能力边界

```text
企业微信材料润色 Bot
  -> app/rewrite_bot/bot.py
  -> 仅加载 rewrite 的 SkillRegistry
  -> PlatformApp
  -> skills/rewrite/
```

- 注册表白名单固定为 `rewrite`，不会路由到直报、简报、综合调研或审核。
- 只接收用户直接粘贴的文字；文件和网页链接会被明确拒绝。
- 用户只粘贴原文时先保存原文并追问修改方向，不会立即调用模型；用户可回复“更正式”“精简一些”“梳理逻辑”或“按默认方式润色”。
- 同一条消息已经同时包含原文和要求，或用户正在继续修改本 Bot 的上一版结果时，直接进入润色，不重复追问。
- 使用独立任务目录 `tasks/writing/rewrite/` 和独立会话目录 `runtime/conversations/rewrite-bot/`，不复用原写作 Bot 的活跃稿件。
- 待确认原文保存在独立目录 `runtime/intake/rewrite-bot/`，默认 30 分钟有效；Bot 重启后可恢复，成功处理或用户发送“取消”后清除。
- 继续复用底座的用户权限、用户名称表、模型配置、任务隔离、对话日志和运维事件。
- 原文明显与微众银行相关时，`rewrite` 可通过受限 `bank_materials` 工具核对机构名、产品名和已有标准表述；普通材料不检索，语料不得用于补充新事实。
- 真实凭证只写本机 `.env`，不得进入 Git。

配置项：

```text
M_AGENT_REWRITE_BOT_ID
M_AGENT_REWRITE_BOT_SECRET
M_AGENT_REWRITE_JOBS_DIR             # 可选
M_AGENT_REWRITE_CONVERSATION_DIR     # 可选
```

配置检查：

```bash
uv run --locked python -m app.rewrite_bot --check-config
```

启动：

```bash
uv run --locked python -m app.rewrite_bot
```

测试：

```bash
uv run --locked pytest tests/test_rewrite_bot.py tests/test_rewrite_workflow.py tests/test_platform_registry.py tests/test_platform_router.py tests/test_platform_app.py -v
```
