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

## 生产常驻服务

生产写作 Bot、审核 Bot、材料润色 Bot、运维 Bot 和本机管理台使用 macOS LaunchAgent 常驻运行。首次安装必须在 `main` 执行：

```bash
uv run --locked python scripts/bot_services.py install all
```

安装命令先对写作、审核、材料润色和运维 Bot 分别运行 `--check-config`，通过后才写入本机 `~/Library/LaunchAgents/` 并启动服务；管理台没有 Bot 凭证，不执行该检查，固定只监听 `127.0.0.1:8787`。服务文件只保存项目、`uv`、启动参数和日志的绝对路径，不保存 Bot 凭证或模型密钥。用户登录 macOS 后服务自动启动；进程异常退出时由系统延迟拉起，配置检查正常退出或管理员主动停止时不会反复重启。五个服务均以后台进程运行，不打开终端窗口。

常用管理命令：

```bash
# 查看五个常驻服务
uv run --locked python scripts/bot_services.py status all

# 分别重启
uv run --locked python scripts/bot_services.py restart writing
uv run --locked python scripts/bot_services.py restart review
uv run --locked python scripts/bot_services.py restart rewrite
uv run --locked python scripts/bot_services.py restart ops
uv run --locked python scripts/bot_services.py restart admin

# 一起启停
uv run --locked python scripts/bot_services.py stop all
uv run --locked python scripts/bot_services.py start all

# 删除常驻配置
uv run --locked python scripts/bot_services.py uninstall all
```

代码不会热加载。功能更新合并回 `main` 后，按影响范围重启：

- 只改 `app/writing/` 或写作 Skill：重启 `writing`。
- 只改 `app/review/` 或审核规则：重启 `review`。
- 只改 `app/rewrite_bot/` 或润色 Skill：重启 `rewrite`。
- 只改 `app/platform/ops/`：重启 `ops`。
- 只改 `app/admin/` 或管理台展示：重启 `admin`。
- 改 `app/platform/`、公共配置、模型配置、`pyproject.toml` 或 `uv.lock`：先执行 `uv sync --locked`，再重启 `all`。
- 只新增任务材料、知识库数据、队列记录或文档说明：通常不需要重启；不确定配置是否在启动时加载时，重启对应 Bot。

服务标准输出分别写入 `runtime/logs/writing-bot-service.*.log`、`review-bot-service.*.log`、`rewrite-bot-service.*.log`、`ops-bot-service.*.log` 和 `admin-console-service.*.log`。常驻服务只在当前 macOS 用户登录后运行，不等同于无需登录的系统级守护进程。

## 写作 Bot

```bash
uv run --locked python -m app.writing.bot --check-config
```

入口配置使用 `WRITING_BOT_ID`、`WRITING_BOT_SECRET` 和公共模型、数据目录配置。生产运行由上述常驻服务管理；前台命令只用于排障，执行前必须先 `stop writing`，避免同一 Bot 重复连接。详细技术行为见 `app/writing/README.md`。

内参周报批准后的洁净版需要本机已安装 Microsoft Word。系统不会让 Word 直接打开任务目录文件，而是使用 `~/Library/Containers/com.microsoft.Word/Data/Documents/M-Agent-TOC/` 作为固定临时目录，后台更新目录、校验页码并清理副本。该目录只保存处理中的随机文件，不是长期任务存储；真实成品仍只保存在任务 `output/`。Word 未安装、自动化超时或目录不完整时，任务按生成失败处理，不会发送空目录附件。首次上线后应使用脱敏周报样本做一次生产用户会话验收，确认 Word 自动化权限可用、没有弹出文件访问提示，成品打开后目录项和页码已显示。

## 审核 Bot

```bash
uv run --locked python -m app.review.main --check-config
```

审核使用独立 Bot 凭证和可选的独立模型配置。生产运行由上述常驻服务管理；前台命令只用于排障，执行前必须先 `stop review`。详细技术行为见 `app/review/README.md`。

## 材料润色 Bot

```bash
uv run --locked python -m app.rewrite_bot --check-config
uv run --locked python scripts/bot_services.py restart rewrite
```

该入口只加载 `rewrite`，不能执行直报、简报和审核。

## 运维 Bot

```bash
uv run --locked python -m app.platform.ops.bot --check-config
uv run --locked python scripts/bot_services.py restart ops
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

本机管理台的“交付恢复”区域会列出需要人工处理的任务，并提供与上述命令相同的受控操作。页面不显示用户内容、附件路径或企业微信原始标识；“送达未知”必须经过明确确认才能重发，所有写操作均校验当前页面的 CSRF 令牌。

## 上线准备检查

无需连接企业微信即可验证消息幂等、多用户隔离和重启恢复：

```bash
uv run --locked python scripts/platform_readiness.py offline
```

合并到 `main` 并重启生产服务后，执行只读生产检查：

```bash
uv run --locked python scripts/platform_readiness.py production
```

生产检查核对五个 LaunchAgent、四个 Bot 心跳、写作和审核队列数据库完整性及待人工恢复交付数量；不读取用户正文，也不主动发送企业微信消息。真实发送超时、拒绝和回执丢失仍必须使用专用测试 Bot 按测试规范验收。

## 配置来源

- 公共示例：`app/config.example.env`
- 审核示例：`app/review/config.example.env`
- 用户和 Skill 权限示例：`config/platform-policy.example.yaml`
- 本机真实权限：`config/platform-policy.yaml`，不进入 Git

当前环境模板仍分为公共和审核两份；后续如统一为根目录 `.env.example`，必须同步所有 Bot 配置检查和文档。
