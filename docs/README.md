# M-Agent 文档地图与治理规则

本文档是项目文档的总入口，规定每类文档写什么、怎么写，以及哪些内容不能写进去。后续 Codex、Claude Code 和开发者都以本规则判断文档归属。

## 基本原则

1. 一个事实只保留一个权威来源，其他文档只链接，不复制整段内容。
2. 当前文档只描述当前有效事实；已经完成或失效的设计进入 `docs/history/`。
3. README 只做入口、边界、启动和导航，不承担开发日志或完整路线图。
4. TODO 只记录尚未完成的工作，不保存已完成事项的长期正文。
5. 业务规则写在 Skill，公共运行机制写在底座或架构文档，不能混写。
6. 开发过程按月记录在本机日志；Git 提交用于技术追溯，不能代替当前文档。

## 唯一职责

| 文档 | 只负责写什么 | 不应该写什么 |
|---|---|---|
| 根目录 `README.md` | 项目定位、主要目录、环境、常用启动命令、文档入口 | 详细功能规则、开发历史、测试里程碑、完整待办 |
| `AGENTS.md` | 所有 AI 工具共同遵守的强制开发规则 | 业务状态快照、单次开发结果 |
| `CLAUDE.md` | Claude Code 入口和少量工具差异，引用共同规则 | 复制一整套与 `AGENTS.md` 重复的项目事实 |
| `docs/development/README.md` | 开发者进入项目的阅读顺序、日常开发流程和文档导航 | 当前能力长清单、历史修复记录、详细测试结果 |
| `docs/development/architecture.md` | 当前已经实现的分层、依赖、数据流、安全边界和关键协议 | 未来想法、开发日志、业务写作规则 |
| `docs/agent-platform/README.md` | 底座职责、公共接口、目录边界和扩展约束 | 各 Skill 的业务规则、逐次底座交付历史 |
| `docs/capabilities/` | 当前可用业务能力的范围、输入输出、入口和边界 | 公共底座实现细节、历史测试流水 |
| `skills/<skill_id>/SKILL.md` | 该 Skill 的适用场景、业务规则、流程、禁止事项和自检 | 企业微信接入、权限、任务队列等公共机制 |
| `app/<module>/README.md` | 模块职责、技术入口、配置组、运行和专项测试 | 跨项目路线图、完整业务规则、开发历史 |
| `docs/operations/` | Bot 启停、控制台、告警、日志、数据维护和故障处理 | 写作或审核业务规则 |
| `docs/knowledge/` | 知识库来源、数据结构、更新、检索和治理规则 | Skill 的成稿写法、运行日志 |
| `docs/development/TODO.md` | 未开始、进行中、已暂缓的事项及实施条件 | 已完成或已取消事项的长期正文 |
| `docs/plans/` | 当前正在评审或执行的设计和实施计划 | 已完成计划、当前架构事实 |
| `docs/history/` | 已完成、失效或仅供追溯的设计、计划和快照 | 任何当前开发依据 |
| `docs/development/testing-and-delivery.md` | 测试分层、选择规则、命令和交付闸门 | 每次测试通过多少项的历史记录 |
| 本机月度开发日志 | 完成功能、能力变化、关键验证、当前边界和下一步 | 用户材料、密钥、真实用户 ID、错误堆栈、文件提交清单 |

## 写法规范

### 当前事实文档

- 使用现在时，直接说明“系统当前如何工作”。
- 每项结论尽量附代码、配置、测试或 Skill 路径。
- 不写“2026-07-xx 修复了……”或连续测试通过数量；这些属于开发日志和 Git。
- 不使用“新底座”“旧审核”这类会快速过时的相对称呼，改用准确模块名。
- 不在多个 README 复制同一能力清单。

### TODO

- 每项必须有编号、状态、优先级、归属、背景、目标和验收标准。
- 状态只允许 `未开始`、`进行中`、`已暂缓`。
- 完成或取消后，从当前 TODO 移出；完成事实写入对应当前文档，过程保留在月度日志和 Git。
- 长期研究可以保留，但必须写清进入实施阶段的条件，不能伪装成当前排期。

### 设计和实施计划

- 尚在讨论或执行时放入 `docs/plans/`，文件名使用 `YYYY-MM-DD-topic-design.md` 或 `YYYY-MM-DD-topic-plan.md`。
- 计划完成、放弃或被替代后移入 `docs/history/designs-and-plans/`。
- 设计文档不能代替当前架构、模块 README、Skill 文档或 TODO。

### README

- 建议控制在能快速读完的范围，优先链接权威文档。
- 只在模块定位、启动方式、公共接口或导航变化时更新。
- 普通业务规则修改不更新根 README；普通开发完成不向 README 追加进度快照。

## 目录地图

```text
docs/
├── README.md                         # 本文档
├── development/                     # 开发规范、架构、TODO、测试和交付
├── agent-platform/                  # 公共底座说明
├── capabilities/                    # 当前业务能力
├── knowledge/                       # 政策库和微众银行信息库
├── operations/                      # Bot、控制台和运行维护
├── plans/                           # 仅保留当前计划
└── history/                         # 已完成或失效的历史资料
```

### 开发

- [开发入口](development/README.md)
- [整体架构](development/architecture.md)
- [目录规范](development/directory-standard.md)
- [Codex / Claude Code 工作流](development/codex-claude-workflow.md)
- [测试和交付](development/testing-and-delivery.md)
- [月度开发日志机制](development/status-report.md)
- [当前待办](development/TODO.md)

### 当前能力

- [能力索引与 Skill 规范](capabilities/README.md)
- [直报写作](capabilities/direct-report/README.md)
- [简报写作](capabilities/brief-writing.md)
- [材料润色](capabilities/rewrite.md)
- [综合调研整合](capabilities/research-synthesis.md)
- [深银协动态](capabilities/shenyinxie-news.md)
- [材料审核](capabilities/review.md)

### 运行和知识库

- [底座说明](agent-platform/README.md)
- [Bot 运行维护](operations/bots.md)
- [项目控制台](operations/admin-console.md)
- [政策知识库](knowledge/policy.md)
- [微众银行信息库](knowledge/bank.md)

### 历史

- [历史文档说明](history/README.md)
- `archive/` 保存已经退出运行的历史代码快照，不属于当前文档体系。

## 更新判断

一次变更只更新真正受影响的权威文档：

- 底座行为改变：架构文档或底座说明。
- 企业微信入口改变：对应 `app/<module>/README.md` 和必要的运维文档。
- Skill 业务规则改变：对应 `SKILL.md`；能力范围改变时再更新能力文档。
- 测试或交付机制改变：测试和交付规范。
- 路线改变：TODO；完成后将事项移出 TODO。
- 项目入口、目录或文档导航改变：根 README、本文档或目录规范。

任何计划文档、开发日志或 Git 提交都不能替代上述当前事实文档。
