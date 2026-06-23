# M-Agent 项目指引

## 一、项目概述

### 做什么

M-Agent 是一个**多领导风格沉淀工具**,通过企业微信机器人为每位领导建立风格档案。

当前有两个独立模块:

| 模块 | 功能 | Bot ID |
|------|------|--------|
| **领导风格沉淀** | 接收材料 → AI 提炼风格 → 用户确认后写入 profile | 待查 |
| **智能审核** | 接收 .docx → 按规则审核 → 返回问题清单 | `aibsq5xHDu-...`(见 .env) |

### 不做什么

- 不做完整写稿流程、网页后台、数据库
- 不做风格自动入库(必须用户确认)
- 不做规则热更新(改规则后重启 Bot 才生效)
- 不创建 `agents/`、`templates/`、`knowledge/`、`runs/`、`services/` 目录

### 技术路线

- 企业微信长连接 SDK 接入(已选定 AiBotSDK)
- 自建经验沉淀和审核逻辑
- Markdown 文件存储(leaders/ + reviews/)

---

## 二、目录结构

```
M-Agent/
├── CLAUDE.md              ← 项目核心规范(本文件)
├── STATUS-REPORT.md       ← 开发日志(变更记录)
├── README.md
├── .env                   ← 密钥(不提交)
├── .gitignore
│
├── app/
│   ├── review/            ← 智能审核模块(独立模块)
│   │   ├── parser.py      # docx 解析
│   │   ├── reviewer.py   # LLM 调用
│   │   ├── rule_loader.py
│   │   ├── output_formatter.py
│   │   └── main.py       # 企微 Bot WS 服务
│   ├── agent/             ← 领导风格沉淀模块
│   │   └── ...
│   ├── data/
│   │   ├── rules.md      ← 审核规则(共享,Bot 重启加载)
│   │   ├── leaders/      ← 领导画像
│   │   └── reviews/      ← 审核存档
│   └── prompts/
│       └── style_extraction.md
│
├── docs/
│   ├── STATUS-REPORT.md   ← (同上,根目录是主引用)
│   ├── phase-1-prototype-structure.md
│   ├── wecom-learning-gateway-implementation-design-v0.1.md
│   └── review-module-design-v0.1.md
│
└── tests/
```

---

## 三、开发原则

### 交付铁律

任何开发/修改,必须**跑过测试、确认通过**后才算完成。不能口头说"应该没问题"。

### 开发顺序

1. 企业微信文本收发
2. 文件接收和解析
3. AI 逻辑(prompt + 调用)
4. 用户确认交互
5. 写入 + 存档
6. README + 配置示例 + 测试说明

不要跳过前置阶段直接做复杂功能。

### 设计原则

- 规则是给 **LLM 读的清单**,代码只负责 .docx 解析 + LLM 调用 + 格式化输出
- 不要在代码里写正则/字典 checker 来"代替" LLM 判断
- 规则变更 → 改 rules.md → 重启 Bot → 生效

### 错误处理

- Bot 收到非文件消息 → 回固定话术,不做处理
- LLM 超时(90s) → 记录,给用户提示,不卡死
- 空 issues → 合法结果,不重试

---

## 四、测试要求

### 功能标准

**领导风格沉淀模块:**
1. 指定领导后发文件 → 能解析、能生成建议
2. 用户"确认全部"/"确认 1、3" → 正确写入 profile.md
3. "不入库" → 不修改 profile.md
4. 每次写入 → 更新 update-log.md

**智能审核模块:**
1. 收到 .docx → ACK 消息 → LLM 审核 → 返回结果
2. 按段落顺序输出,每条:错误N + 类型 + 原文引用
3. 结尾无规则统计
4. 空结果 → "✅ 未发现低级错误"

### 验证标准

- 功能测试:能用企微 Bot 手动验证
- 单元测试:核心逻辑有测试覆盖
- 代码变更:改了什么 → 测什么,不能只跑全量

---

## 五、安全要求

1. Bot ID、Secret、API Key **不提交**到仓库
2. 原始材料可能含敏感信息,**不提交**到远程仓库
3. 日志不输出完整密钥和敏感正文
4. 审核建议必须用户确认后才能写入
5. 配置和密钥不得硬编码

---

## 六、文档规范

### 文档优先级

1. 真实代码
2. `STATUS-REPORT.md`(开发日志)
3. `CLAUDE.md`(项目规范)
4. `docs/*.md` 设计文档

### 变更同步

实现变更时同步更新:
- README.md(启动说明)
- STATUS-REPORT.md(做了什么)
- 相关设计文档(如果结构变了)

### 废弃文档

以下文档已废弃,不要参考:
- `docs/wecom-sdk-selection.md`
- `docs/wecom-learning-gateway-design.md`
- `docs/multi-agent-writing-tool-product-plan-v0.1.md`

---

## 七、每次会话开始

收到用户第一条消息时,先读 `STATUS-REPORT.md`,了解项目当前状态和待办。

---

### Hooks 自动记录

项目提供测试钩子,用于在关键节点自动写 STATUS-REPORT:

#### `scripts/hook-test.sh` — 测试完成后调用

```bash
# 每次测试通过后追加一条记录
./scripts/hook-test.sh review "5 tests OK"
```

**只记录项目开发**(代码改动、测试通过、功能上线),**不记录用户行为**(审核文件、对话等)。

---

### 快速启动

```bash
# 领导风格沉淀 Bot
cd ~/Desktop/M-Agent && python3.11 -m app.agent.main

# 智能审核 Bot
cd ~/Desktop/M-Agent && python3.11 -u -m app.review.main --log-file /tmp/review-bot.log

# 运行测试
cd ~/Desktop/M-Agent && python3.11 -m unittest discover tests/
```
