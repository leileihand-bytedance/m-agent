# M-Agent

M-Agent 是运行在本机 Mac 上、通过企业微信提供服务的业务智能体系统。项目采用“公共底座 + 独立 Skills + 受限工具”的结构，当前覆盖写作、材料润色、综合调研、动态整理和多类材料审核。

## 项目结构

```text
企业微信 / 本地入口
  -> app/                 # 入口、底座、管理和知识服务
  -> skills/              # 业务能力及其规则
  -> 受限工具和模型
  -> M-Agent-Files/       # 仓库外的任务、知识库和运行数据
```

主要目录：

```text
app/platform/       # 公共底座
app/writing/        # 写作企业微信入口
app/review/         # 独立审核入口和审核实现
app/rewrite_bot/    # 独立材料润色入口
app/admin/          # 本机项目控制台
skills/             # 正式业务能力
tests/              # 自动化测试
docs/               # 项目文档
scripts/            # 维护和交付脚本
archive/            # 已退出运行的历史代码
```

## 环境

项目固定使用 uv 管理的 Python 3.13.14 和根目录 `.venv`：

```bash
uv sync --locked
uv run --locked python -c "import sys; print(sys.executable); print(sys.version)"
```

不要使用系统 `python`、全局 `pip` 或其他项目的虚拟环境。

## 常用入口

```bash
# 检查公共底座配置
uv run --locked python -m app.platform.cli --check-config

# 写作、审核和管理台常驻服务（macOS，首次使用 install）
uv run --locked python scripts/bot_services.py install all
uv run --locked python scripts/bot_services.py status all
uv run --locked python scripts/bot_services.py restart all

# 写作 Bot 配置检查和前台排障
uv run --locked python -m app.writing.bot --check-config
uv run --locked python -m app.writing.bot

# 审核 Bot 配置检查和前台排障
uv run --locked python -m app.review.main --check-config
uv run --locked python -m app.review.main

# 材料润色 Bot
uv run --locked python -m app.rewrite_bot --check-config
uv run --locked python -m app.rewrite_bot

# 本机项目控制台由 admin 常驻服务提供
uv run --locked python scripts/bot_services.py status admin
```

所有真实密钥只写入本机 `.env`。用户上传文件、系统输出、日志、会话、任务队列和知识库默认保存在项目同级的 `M-Agent-Files/`，不进入 Git。

## 测试

```bash
uv run --locked pytest tests -q
uv run --locked python scripts/project_docs.py check
```

真实模型、企业微信和大文件测试的分层要求见 [测试和交付规范](docs/development/testing-and-delivery.md)。

## 文档入口

开发前按顺序阅读：

1. `AGENTS.md` 或 `CLAUDE.md`
2. [文档地图与治理规则](docs/README.md)
3. [开发入口](docs/development/README.md)
4. [整体架构](docs/development/architecture.md)
5. [当前待办](docs/development/TODO.md)

业务规则以各 `skills/<skill_id>/SKILL.md` 为准。当前能力、底座、运维、知识库和历史文档的具体入口统一从 [docs/README.md](docs/README.md) 查找。
