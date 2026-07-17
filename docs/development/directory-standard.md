# M-Agent 目录和文件规范

本文档规定代码、文档、配置、测试、静态资产和运行数据应该放在哪里。文档内容职责另见 `docs/README.md`。

## 顶层目录

```text
M-Agent/
├── README.md             # 项目入口
├── AGENTS.md             # AI 工具共同规则
├── CLAUDE.md             # Claude Code 入口
├── pyproject.toml        # 直接依赖唯一声明
├── uv.lock               # 依赖准确版本
├── app/                  # 运行代码
├── skills/               # 业务能力
├── tests/                # 自动化测试
├── docs/                 # 项目文档
├── scripts/              # 长期维护工具
├── config/               # 可提交配置示例和本机权限文件
├── data/                 # 仅允许脱敏固定测试资料
├── .worktrees/           # 本机短期任务工作区，不进入 Git
└── archive/              # 已退出运行的历史代码
```

根目录禁止长期保留：

- `test_*.py`、`debug_*.py`、`tmp_*.py`、`audit_*.py` 等一次性脚本。
- 用户文件、模型输出、日志、数据库、下载文件和截图。
- 另一套虚拟环境或依赖清单。
- 带有 `old`、`new`、`final`、`copy`、`backup` 的临时版本文件。

一次性验证完成后必须删除；有长期价值的逻辑改为 `tests/` 或 `scripts/` 中的正式资产。

`.worktrees/` 由 `scripts/project_docs.py start-task` 和 `finish-task` 管理，同时最多 2 个。任务工作区可链接主项目 `.venv`，但不得复制或链接生产 `.env`；完成并成功推送后自动删除，不作为长期项目副本。

## 代码目录

### `app/platform/`

公共底座，只放入口标准化、路由、身份权限、材料组装、会话、任务、工具、文档服务、交付和运维等跨 Skill 能力。

禁止写具体文种、审核类型或机构口径。

### `app/<module>/`

入口、管理面和知识服务按模块划分。每个长期模块应有短 README，说明：

- 模块职责和边界。
- 主要技术入口。
- 配置组和启动方式。
- 专项测试入口。
- 指向业务能力、架构或运维权威文档的链接。

模块 README 不复制跨项目路线、完整业务规则和开发历史。

### `skills/`

每个正式 Skill 使用独立目录：

```text
skills/<skill_id>/
├── __init__.py
├── SKILL.md
├── config.yaml
├── schema.py
├── workflow.py
├── prompts/          # 可选
└── assets/           # 可选，只保存经批准的静态模板
```

Skill 只能通过底座授权工具访问网页、模型、知识库和用户材料。共享业务组件放在 `skills/` 顶层，只有确实被多个 Skill 复用时才抽取。

## 测试目录

当前测试以 `tests/test_<domain>_<behavior>.py` 命名。新增测试继续使用稳定领域前缀：

```text
test_platform_*.py
test_review_*.py
test_writing_*.py
test_ops_*.py
test_admin_*.py
test_<skill_id>_*.py
```

固定脱敏样本放入 `tests/fixtures/`。测试不得依赖真实 `.env`、真实用户材料、固定本机绝对路径或外部服务，真实模型端到端用例必须显式标识并单独说明。

测试文件继续增长后，可以按领域迁入子目录；迁移前必须先消除依赖 `Path(__file__)` 层级的脆弱路径计算，并保证全仓测试收集结果不变。

## 文档目录

```text
docs/
├── README.md             # 文档职责和导航
├── development/         # 架构、开发、测试、交付和 TODO
├── agent-platform/      # 公共底座说明
├── capabilities/        # 当前业务能力
├── knowledge/           # 知识库治理
├── operations/          # Bot、控制台和运行维护
├── plans/               # 当前设计和实施计划
└── history/             # 已完成或失效文档
```

当前文档使用描述性文件名；带日期的文件只用于计划、评审和历史资料。当前事实文档不得通过不断追加日期快照维护。

## 配置

```text
app/config.example.env              # 公共和写作配置示例
app/review/config.example.env       # 审核独立配置示例
config/platform-policy.example.yaml # 权限示例
config/platform-policy.yaml         # 本机真实权限，不进入 Git
.env                                # 本机真实密钥，不进入 Git
```

配置示例只保存变量名、安全默认值和说明，不保存真实密钥、Bot ID、用户 ID 或本机路径。新增变量必须进入对应配置读取、检查、示例和运维文档。

## 静态资产

经批准的模板、前端依赖和许可证可以进入 Git，但必须：

- 位于所属模块的 `assets/` 或 `static/vendor/`。
- 能说明来源、用途和许可证。
- 不包含真实用户材料或案例正文。
- 有测试验证业务依赖的结构，而不是依赖某台电脑的桌面文件。

## 运行数据

所有非 Git 数据位于项目同级的 `M-Agent-Files/`：

```text
M-Agent-Files/
├── tasks/                  # 用户输入和系统输出
├── knowledge/              # 政策库、银行信息库和 Wiki
├── runtime/                # 会话、intake、队列、日志、用户表和运维状态
│   └── development-logs/   # 按月开发日志
└── legacy/                 # 历史运行数据迁移保留区
```

`M_AGENT_DATA_DIR` 是统一入口。细分路径只用于特殊部署，不允许各模块自行发明新的持久目录。

开发分支真实联调使用另一棵仓库外目录 `M_AGENT_TEST_DATA_DIR`，例如项目同级的 `M-Agent-Test-Files/`。测试数据目录与生产 `M-Agent-Files/` 结构相同，但不得指向同一路径，也不得写入 Git。

## 历史资料

- `docs/history/`：历史设计、计划、方案和 TODO 快照。
- `archive/`：已退出运行的代码快照。
- Git：精确文件变化和版本历史。
- 本机月度开发日志：完整记录完成功能、能力变化、关键验证和下一步。

历史资料不得被当前入口自动加载，也不能作为当前行为的唯一依据。

## 命名规范

- Python 模块、测试、Skill ID：小写 `snake_case`。
- Markdown 普通文档：小写 `kebab-case.md`；约定文件保留 `README.md`、`TODO.md`、`SKILL.md`。
- 设计和计划：`YYYY-MM-DD-topic-design.md`、`YYYY-MM-DD-topic-plan.md`。
- 不使用空格、中文括号、`副本`、`最终版` 或连续版本号表达当前文件。
- 当前文件原位更新；历史版本依靠 Git，不创建 `xxx-v2-final.md`。

## 自动检查

提交前至少检查：

```bash
uv run --locked python scripts/project_docs.py check
uv run --locked pytest tests/test_project_documentation.py -q
```

文档闸门负责防止本机文件、绝对路径、错误 TODO 状态和缺少对应权威文档；它不能替代人工判断内容是否重复或放错位置。
