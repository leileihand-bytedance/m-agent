# M-Agent Bot 运行维护

本文档只说明 Bot 的启动、配置、心跳、日志和故障处理。业务能力范围见 `docs/capabilities/`。

## 公共前提

```bash
cd /path/to/M-Agent
uv sync --locked
```

真实凭证写入根目录 `.env`，运行数据由 `M_AGENT_DATA_DIR` 指向仓库外的 `M-Agent-Files/`。不要把密钥、真实用户 ID、日志或任务材料写入文档和 Git。

### 运行环境硬隔离

生产运行：

```text
M_AGENT_RUNTIME_ENV=production
M_AGENT_DATA_DIR=../M-Agent-Files
```

生产 Bot 只允许从 `main` 分支启动。开发任务工作区不复制生产 `.env`，也不能使用生产 Bot 联调。

任务分支真实联调必须使用：

```text
M_AGENT_RUNTIME_ENV=test
M_AGENT_TEST_DATA_DIR=../M-Agent-Test-Files
M_AGENT_TEST_WRITING_BOT_ID=          # 写作测试 Bot
M_AGENT_TEST_WRITING_BOT_SECRET=
M_AGENT_TEST_REVIEW_BOT_ID=           # 审核测试 Bot
M_AGENT_TEST_REVIEW_BOT_SECRET=
M_AGENT_TEST_REWRITE_BOT_ID=          # 润色测试 Bot
M_AGENT_TEST_REWRITE_BOT_SECRET=
M_AGENT_TEST_OPS_BOT_ID=              # 运维测试 Bot
M_AGENT_TEST_OPS_BOT_SECRET=
```

只需配置本次要联调的测试 Bot。测试模式不会读取对应生产凭据，也不会回退到生产数据目录；所有任务、会话、队列、日志、用户表、知识库和运维状态必须位于测试根目录。`--check-config` 会显示运行环境、遮罩后的 Bot ID 和数据根目录，不显示 Secret。

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
M-Agent-Files/runtime/logs/review-capabilities/<capability_id>/  # 审核子能力日志
M-Agent-Files/runtime/task-execution/   # 持久任务队列和恢复状态
M-Agent-Files/runtime/task-relations/   # 多任务卡片、版本、材料台账和待确认关系
```

`runtime/task-relations/task-relations.sqlite3` 由写作和润色入口共享，数据按入口和 `userid` 隔离。可通过 `M_AGENT_TASK_RELATION_DB` 显式覆盖；正常部署使用 `M_AGENT_DATA_DIR` 下的默认路径，不需要单独配置。关系判断指标不保存消息正文，数据库不得复制进 Git。

日志按天并按大小分片。审核 Bot 总日志和按用户日志继续保留；具体审核任务还按通用文字、通用 Word、HTML、内参、半月报、公文格式、PPTX 和多文件八类子能力分别写日志，记录稳定的能力 ID 和任务 ID。日志可以记录消息、意图、Skill、任务、耗时和错误分类，但不能记录密钥、其他用户材料或不必要的全文。控制台默认不展示用户权限和任务正文。

## 故障处理顺序

1. 确认对应 Bot 心跳是否更新。
2. 根据运维告警区分链接、文件、模型、企业微信发送或进程故障。
3. 使用处理编号定位本机任务，不要求用户理解后台任务 ID。
4. 检查持久队列状态和交付检查点，避免人工操作造成重复发送。
5. 修复后使用脱敏样本建立自动化回归，再恢复服务。

## 企业微信结果交付恢复

持久任务的精确交付状态保存在任务 `execution.json`：`confirmed_delivered` 表示已收到成功回执，`confirmed_not_delivered` 表示企业微信明确拒绝或本地明确未发送，`delivery_unknown` 表示发送已发起但没有取得可判断回执。管理台 `status.json` 仍只展示 `delivered`、`failed`、`unknown`。SDK 1.0.7 没有发送状态查询接口，未知状态必须人工核实，不能直接重发。

先用运维告警中的处理编号查看脱敏依据；该命令不显示结果正文、附件路径和原始企业微信请求标识：

```bash
uv run --locked python -m app.platform.delivery_recovery <处理编号> inspect
```

确认“明确未送达”后，按原检查点重新排队，不重新调用模型：

```bash
uv run --locked python -m app.platform.delivery_recovery <处理编号> retry --operator <操作人>
```

如果状态为“送达未知”，必须先由管理员从用户侧或企业微信侧人工核实。确认实际未送达后才允许：

```bash
uv run --locked python -m app.platform.delivery_recovery <处理编号> retry --confirm-unknown-not-delivered --operator <操作人>
```

确认实际已经送达，或决定保留未知状态并关闭任务时分别使用：

```bash
uv run --locked python -m app.platform.delivery_recovery <处理编号> confirm-delivered --operator <操作人>
uv run --locked python -m app.platform.delivery_recovery <处理编号> close --operator <操作人>
```

所有变更写入交付历史和脱敏运维事件，操作人只保存哈希标识。生产恢复只能从 `main` 和生产数据目录执行；任务分支的命令会被运行环境守卫限制为专用测试数据，不能借此操作生产任务。已确认送达的结果禁止恢复重发。

## 配置来源

- 公共示例：`app/config.example.env`
- 审核示例：`app/review/config.example.env`
- 用户和 Skill 权限示例：`config/platform-policy.example.yaml`
- 本机真实权限：`config/platform-policy.yaml`，不进入 Git

当前环境模板仍分为公共和审核两份；后续如统一为根目录 `.env.example`，必须同步所有 Bot 配置检查和文档。
