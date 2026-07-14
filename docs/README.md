# M-Agent 文档分区说明

本文档用于区分后续 M-Agent 的两类建设工作：底座区和功能区。

## 结论

M-Agent 后续不再把每个能力都做成独立 Bot 或独立脚本，而是逐步演进为：

```text
统一企业微信入口
  -> M-Agent 底座区
  -> 功能区 Skills
  -> 受限工具层
  -> 返回企业微信
```

当前新底座已经建立，直报写作已经接入新底座。旧审核入口继续保留，后续逐个能力迁移。

## 文档分区

```text
docs/
├── README.md
├── archive/
│   └── ...历史方案与旧规划
├── development/
│   ├── README.md
│   ├── admin-console.md
│   ├── architecture.md
│   ├── codex-claude-workflow.md
│   ├── direct-report-production-test.md
│   ├── directory-standard.md
│   ├── policy-knowledge-base.md
│   ├── TODO.md
│   └── testing-and-delivery.md
├── agent-platform/
│   └── README.md
└── capabilities/
    └── README.md
```

### `docs/development/`

开发规范文档。给 Codex、Claude Code 和后续开发者使用，包括：

- 整体架构
- 目录和文件规范
- 自然语言开发工作流
- 当前待办和待办更新规则
- 测试和交付规则

### `docs/agent-platform/`

底座区文档。用于描述 M-Agent 的公共运行能力，包括：

- 企业微信统一入口
- 用户身份和权限
- 意图识别
- Skill 注册和调度
- 工具权限控制
- 文件隔离
- 会话和任务记录
- 日志和测试规范

底座区解决的是“系统如何安全、稳定地运行”的问题。

### `docs/capabilities/`

功能区文档。用于描述具体业务能力，包括：

- 智能审核
- 直报写作
- 简报写作
- 改稿
- 会议纪要
- 其他后续新增 skill

功能区解决的是“每个业务能力具体怎么做”的问题。

### `docs/archive/`

历史方案和已失效规划。保留用于追溯决策，不作为后续开发依据。

## 当前代码分区

```text
app/
├── platform/          # 新底座区，当前主线
├── admin/             # 本机管理后台
├── writing/           # 当前直报 Bot 入口适配层
└── review/            # 现有审核 Bot，继续保持可用并逐步包装

skills/                # 正式功能区
├── direct_report/
├── writer1/
├── writer2/
├── rewrite/
└── research_synthesis/
```

当前直报、单素材简报、多素材简报和文字润色能力已进入 `skills/`。早期 `app/agent/` 已归档到 `archive/inactive-2026-07-04/`，不再作为开发入口。

## 迁移原则

1. 现有 `app/review/`、`app/writing/` 不直接重构，避免影响当前可用功能。
2. 新底座已经建立，后续按 skill 逐个迁移能力。
3. 每迁移一个功能，就补一份对应 skill 文档和测试。
4. 迁移前后要保留旧入口一段时间，方便回退。
5. 所有外部用户通过企业微信只能调用已登记 skill，不能直接访问本机文件、命令或未授权工具。

## 推荐阅读顺序

1. `docs/development/README.md`
2. `docs/development/architecture.md`
3. `docs/development/codex-claude-workflow.md`
4. `docs/development/TODO.md`
5. `docs/agent-platform/README.md`
6. `docs/capabilities/README.md`
7. 具体能力的 skill 文档

## 不再作为新架构依据的旧文档

以下文档为历史方案或已废弃方案，不作为新底座设计依据：

- `docs/archive/wecom-sdk-selection.md`
- `docs/archive/wecom-learning-gateway-design.md`
- `docs/archive/wecom-learning-gateway-development-plan-v0.1.md`
- `docs/archive/wecom-learning-gateway-implementation-design-v0.1.md`
- `docs/archive/phase-1-prototype-structure.md`
- `docs/archive/multi-agent-writing-tool-product-plan-v0.1.md`
