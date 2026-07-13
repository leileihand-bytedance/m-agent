# M-Agent 目录和文件规范

## 顶层目录

```text
M-Agent/
├── AGENTS.md
├── CLAUDE.md
├── app/
├── skills/
├── tests/
├── docs/
└── scripts/
```

运行数据不再放在代码仓库内，默认位于项目同级的桌面目录：

```text
M-Agent-Files/
├── tasks/          # 用户上传和系统生成的任务文件
├── knowledge/      # 政策库、政策 Wiki、微众银行信息库
├── runtime/        # 会话、日志、用户名表、运维事件和心跳
└── legacy/         # 历史运行数据迁移保留区
```

该目录不建立 Git 仓库，也不允许复制进 `M-Agent/` 后提交。

## `app/platform/`

底座区。当前主线，只放公共运行能力。

当前文件：

```text
app/platform/
├── __init__.py
├── app.py                 # 平台应用服务，串联路由、权限、任务、runtime
├── builtin_tools.py       # 当前内置工具
├── cli.py                 # 本地配置检查和消息测试入口
├── config.py              # 平台配置
├── conversation.py        # 平台级会话状态与活跃稿件版本链
├── demo.py                # 本地 demo 入口
├── gateway/               # 企业微信消息核心适配
├── identity.py            # 用户和 skill 权限
├── intent.py              # 改稿/新任务/追问意图分类
├── models.py              # 底座通用模型
├── pydantic_runtime.py    # Pydantic AI 执行层
├── registry.py            # Skill 注册表
├── router.py              # 意图路由
├── runtime.py             # 平台运行时
├── storage.py             # 任务目录和结果记录
└── tools.py               # 工具授权网关
```

未来可拆分：

```text
app/platform/
├── gateway/
├── identity/
├── storage/
├── safety/
└── tools/
```

拆分原则：只有当单文件职责变多、测试开始难维护时再拆。

## `app/admin/`

本机管理后台。属于底座管理面，不属于业务能力区。

当前职责：

- 查看 skill。
- 开启或关闭 skill。
- 配置用户可用 skill。
- 查看最近任务摘要。

禁止：

- 在后台里写业务规则。
- 直接展示 `.env` 或 API Key。
- 提供任意文件浏览能力。
- 对公网开放。

## `app/writing/`

当前直报企业微信 Bot 的入口适配层。

它负责：

- 读取 `WRITING_BOT_ID`、`WRITING_BOT_SECRET`。
- 启动现有直报 Bot 长连接。
- 把文本消息交给 `PlatformApp`。
- 把平台结果回复给企业微信。

它不再负责：

- 直报写作规则。
- 网页读取逻辑。
- 直接调用模型写稿。

直报业务规则唯一来源：

```text
skills/direct_report/
```

## `app/review/`

旧审核 Bot。当前继续独立运行，避免影响已经可用的审核入口。

后续迁移方向：

```text
skills/review/
  -> workflow 调用 app/review 的解析和审核能力
```

迁移前不要大改 `app/review/`。

## 已归档停滞模块

以下早期内容已移出主线目录，统一归档到 `archive/inactive-2026-07-04/`：

```text
app/agent/
app/main.py
app/config.py
app/prompts/
app/data/leaders/
data/leaders/
data/leader-mapping.json
scripts/diagnostic_review.py
docs/superpowers/plans/2026-05-26-*.md
docs/superpowers/specs/2026-05-26-*.md
```

这些内容不作为后续开发入口。如需复用，只提取思路，不直接恢复旧入口。

## `skills/`

功能区。每个 skill 一个目录。

标准结构：

```text
skills/<skill_id>/
├── SKILL.md
├── config.yaml
├── schema.py
├── workflow.py
└── prompts/
    ├── draft.md
    └── revise.md
```

### `SKILL.md`

写业务规则，给模型和开发者看。

必须包含：

- 使用场景
- 输入材料
- 执行步骤
- 输出要求
- 禁止事项
- 自检清单

### `config.yaml`

写底座可读配置。

必须包含：

```yaml
id: direct_report
name: 直报写作
description: 根据网页链接或用户材料生成信息直报初稿。
enabled: true
triggers:
  - 直报
allowed_tools:
  - web_reader
  - llm_writer
workflow: skills.direct_report.workflow:run
inputs:
  - url
outputs:
  - title
  - body
  - sources
supports_revision: true
```

### `schema.py`

写 Pydantic 输出模型。

示例：

```python
from pydantic import BaseModel, Field


class DirectReportResult(BaseModel):
    title: str
    body: str
    sources: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    message: str = ""
```

### `workflow.py`

写 skill 流程，只通过 `ToolGateway` 调工具。

禁止：

- 直接读取 `.env`
- 直接读取任意本机文件
- 直接绕过 `ToolGateway`
- 在 workflow 中写大量业务 prompt

## `tests/`

测试文件按能力命名。

当前平台测试：

```text
tests/test_platform_registry.py
tests/test_platform_router.py
tests/test_platform_tools.py
tests/test_platform_builtin_tools.py
tests/test_platform_pydantic_runtime.py
tests/test_platform_runtime.py
tests/test_platform_demo.py
tests/test_direct_report_workflow.py
```

新增 skill 时建议新增：

```text
tests/test_<skill_id>_workflow.py
```

新增底座模块时建议新增：

```text
tests/test_platform_<module>.py
```

## `docs/`

文档分区：

```text
docs/development/       # 开发规范
docs/agent-platform/    # 底座规划
docs/capabilities/      # 功能区规划
docs/archive/           # 历史方案，不作为新开发依据
```

修改架构时更新 `docs/development/architecture.md`。

修改开发流程时更新 `docs/development/codex-claude-workflow.md`。

新增能力时更新 `docs/capabilities/README.md` 或新增能力文档。

## `config/` 与运行数据的边界

```text
M-Agent/config/    # 静态配置，可随仓库提交
    platform-policy.yaml          # 用户 skill 权限策略
    platform-policy.example.yaml  # 权限策略示例

M-Agent-Files/     # 真实运行数据，位于代码仓库之外
    tasks/writing/YYYY/MM/        # 写作任务
    tasks/review/YYYY/MM/         # 审核任务
    runtime/conversations/        # 平台级会话状态
    runtime/users/review_users.yaml
    knowledge/                    # 本地知识库
```

说明：

- `M_AGENT_DATA_DIR` 是唯一推荐配置入口，默认值为项目同级的 `../M-Agent-Files`。
- 用户名表包含企业微信 userid，始终属于本机敏感运行数据，不允许随仓库分发。
- `data/error-examples/` 只允许保留已经脱敏、具有长期测试价值的固定错例；真实用户原件必须进入 `M-Agent-Files/tasks/`。

禁止把真实用户材料、日志、密钥、任务记录提交到仓库。

```text
app/review/
app/writing/
archive/inactive-2026-07-04/
```

这些是历史或过渡目录。不要一次性重写，也不要作为新能力开发入口。

早期单数 `skill/` 目录已废弃并删除。直报能力以 `skills/direct_report/` 为唯一来源。

迁移策略：

1. 先包装成 skill。
2. 跑通新入口。
3. 保留旧入口一段时间。
4. 确认可替代后再清理。
