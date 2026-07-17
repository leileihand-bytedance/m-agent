# M-Agent Bot 运行维护

本文档只说明 Bot 的启动、配置、心跳、日志和故障处理。业务能力范围见 `docs/capabilities/`。

## 公共前提

```bash
cd /path/to/M-Agent
uv sync --locked
```

真实凭证写入根目录 `.env`，运行数据由 `M_AGENT_DATA_DIR` 指向仓库外的 `M-Agent-Files/`。不要把密钥、真实用户 ID、日志或任务材料写入文档和 Git。

## 写作 Bot

```bash
uv run --locked python -m app.writing.bot --check-config
uv run --locked python -m app.writing.bot
```

入口配置使用 `WRITING_BOT_ID`、`WRITING_BOT_SECRET` 和公共模型、数据目录配置。详细技术行为见 `app/writing/README.md`。

## 审核 Bot

```bash
uv run --locked python -m app.review.main --check-config
uv run --locked python -m app.review.main
```

审核使用独立 Bot 凭证和可选的独立模型配置。详细技术行为见 `app/review/README.md`。

## 材料润色 Bot

```bash
uv run --locked python -m app.rewrite_bot --check-config
uv run --locked python -m app.rewrite_bot
```

该入口只加载 `rewrite`，不能执行直报、简报和审核。

## 运维 Bot

```bash
uv run --locked python -m app.platform.ops.bot --check-config
uv run --locked python -m app.platform.ops.bot
```

运维 Bot 独立读取脱敏运维事件，发送实时异常和工作日日报。业务 Bot 只向用户返回简洁错误，详细分类和处理编号进入运维事件。

## 心跳和日志

```text
M-Agent-Files/runtime/ops/heartbeats/   # 服务心跳
M-Agent-Files/runtime/ops/events/       # 脱敏运维事件
M-Agent-Files/runtime/logs/             # 系统和用户日志
M-Agent-Files/runtime/task-execution/   # 持久任务队列和恢复状态
```

日志按天并按大小分片。日志可以记录消息、意图、Skill、任务、耗时和错误分类，但不能记录密钥、其他用户材料或不必要的全文。控制台默认不展示用户权限和任务正文。

## 故障处理顺序

1. 确认对应 Bot 心跳是否更新。
2. 根据运维告警区分链接、文件、模型、企业微信发送或进程故障。
3. 使用处理编号定位本机任务，不要求用户理解后台任务 ID。
4. 检查持久队列状态和交付检查点，避免人工操作造成重复发送。
5. 修复后使用脱敏样本建立自动化回归，再恢复服务。

## 配置来源

- 公共示例：`app/config.example.env`
- 审核示例：`app/review/config.example.env`
- 用户和 Skill 权限示例：`config/platform-policy.example.yaml`
- 本机真实权限：`config/platform-policy.yaml`，不进入 Git

当前环境模板仍分为公共和审核两份；后续如统一为根目录 `.env.example`，必须同步所有 Bot 配置检查和文档。
