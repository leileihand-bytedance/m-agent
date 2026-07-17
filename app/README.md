# `app/` 运行代码

`app/` 保存 M-Agent 的公共底座、企业微信入口、管理面和知识服务。具体写作业务规则放在 `skills/`，用户材料和运行结果放在仓库外的 `M-Agent-Files/`。

## 目录

```text
app/
├── platform/          # 公共底座、文档、任务、工具和运维
├── writing/           # 写作企业微信入口和材料组装
├── review/            # 独立审核入口和审核实现
├── rewrite_bot/       # 独立材料润色入口
├── admin/             # 本机项目控制台
├── policy_knowledge/  # 政策采集、存储和材料包
├── bank_knowledge/    # 微众银行信息库导入和检索
├── policy_research/   # 写作侧政策匹配和研究层
├── data/              # 当前审核静态规则，后续迁入审核模块
└── config.example.env # 公共配置示例
```

## 边界

- `app/platform/` 只实现公共运行能力，不写具体文种规则。
- `app/writing/`、`app/rewrite_bot/` 和企业微信回调只做入口适配，不保存业务 prompt。
- `app/review/` 当前同时承担独立入口和审核业务实现；共享审核核心按 TODO 渐进治理，不做一次性重写。
- 知识服务只返回可追溯材料，不直接生成业务成稿。
- 任何模块都不能在仓库中保存真实用户材料、任务结果、日志、队列或知识数据库。

## 文档

- 公共底座：`docs/agent-platform/README.md`
- 当前架构：`docs/development/architecture.md`
- 业务能力：`docs/capabilities/`
- Bot 运维：`docs/operations/bots.md`
- 知识库：`docs/knowledge/`
- 目录规范：`docs/development/directory-standard.md`

各模块 README 只说明该模块的技术入口、配置、运行和专项测试，不复制跨项目路线或开发历史。

## 验证

测试选择统一查看 `docs/development/testing-and-delivery.md`。最小全仓回归：

```bash
uv run --locked pytest tests -q
uv run --locked python scripts/project_docs.py check
```
