# M-Agent 智能审核模块开发规范

## 一、项目概述

M-Agent 智能审核模块，通过企业微信机器人为 `.docx` 文档提供自动审核服务。

### 功能定位

- 接收企业微信 `.docx` 文件
- 按规则审核，标出低级错误
- 返回问题清单，按出现顺序呈现

### 技术架构

- 企业微信长连接 SDK（AiBotSDK）
- 两层审核：格式类（正则）+ 语义类（LLM CoT + 3次取并集）
- Markdown 文件存储审核结果

## 二、目录结构

```
M-Agent/
├── CLAUDE.md              ← 项目核心规范(本文件)
├── STATUS-REPORT.md       ← 开发日志
├── README.md
├── .gitignore
│
├── app/
│   ├── review/            ← 智能审核模块
│   │   ├── __init__.py
│   │   ├── main.py        # Bot 入口(独立进程)
│   │   ├── reviewer.py    # LLM 调用
│   │   ├── format_checker.py  # 格式类规则正则
│   │   ├── parser.py      # .docx 解析
│   │   ├── output_formatter.py
│   │   ├── rule_loader.py
│   │   └── config.example.env
│   ├── data/
│   │   └── rules.md      ← 审核规则库
│   └── requirements.txt
│
├── tests/
│   ├── test_reviewer.py
│   └── test_review_bot.py
│
└── scripts/
    ├── hook-test.sh       # 测试钩子
    └── hook-test.py
```

## 三、开发原则

### 交付铁律

任何开发/修改，必须**跑过测试、确认通过**后才算完成。不能口头说"应该没问题"。

### 两层审核架构

| 类型 | 规则 | 检测方式 |
|------|------|----------|
| 格式类 | `quote-pair`, `num-unit`, `mixed-punct`, `toc-no-ordinal`, `toc-seq-skip` | 正则代码 |
| 语义类 | `title-truncated`, `content-mismatch`, `content-incomplete`, `toc-mismatch`, `content-out-of-scope`, `content-wrong-section`, `content-duplicate`, `content-outdated` | LLM CoT + 3次取并集 |

### 设计原则

- 格式类规则：**必须代码化**，不用 LLM
- 语义类规则：LLM Chain-of-Thought + 结构化 JSON 输出
- 多次调用取并集去重，提高稳定性
- 规则是给 **LLM 读的清单**，代码只负责 .docx 解析 + LLM 调用 + 格式化输出

### 错误处理

- Bot 收到非文件消息 → 回固定话术
- LLM 超时(90s) → 记录,给用户提示,不卡死
- 空 issues → 合法结果,不重试

## 四、测试要求

### 验证标准

- 功能测试：能用企微 Bot 手动验证
- 单元测试：核心逻辑有测试覆盖
- 代码变更：改了什么 → 测什么

### 测试命令

```bash
python tests/test_reviewer.py
python tests/test_review_bot.py
```

## 五、安全要求

1. Bot ID、Secret、API Key **不提交**到仓库
2. `.env` 已纳入 `.gitignore`
3. 日志不输出完整密钥和敏感正文
4. 配置和密钥不得硬编码

## 六、变更同步

实现变更时同步更新：
- `STATUS-REPORT.md`（做了什么）
- 相关文档

## 七、快速启动

```bash
# 安装依赖
pip install -r app/requirements.txt

# 配置
cp app/review/config.example.env .env

# 检查配置
python -m app.review.main --check-config

# 启动 Bot
python -m app.review.main

# 测试
python tests/test_reviewer.py
```

## 八、废弃文档

以下文档已废弃，不要参考：
- `docs/wecom-sdk-selection.md`
- `docs/wecom-learning-gateway-design.md`
- `docs/multi-agent-writing-tool-product-plan-v0.1.md`
