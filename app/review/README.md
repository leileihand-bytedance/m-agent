# app/review - 智能审核模块

> 与领导风格沉淀并列的**独立业务模块**。第一版只审低级错误,不查风格、不查深度、不改稿。

## 这是什么

`app/review/` 是 M-Agent 的**第二个企微 Bot**,**只做一件事**:用户丢一个 `.docx` 过来,Bot 按 `app/data/rules.md` 里的规则审,标出低级错误并存档。

## 架构定位

```text
                  M-Agent/
                     │
       ┌─────────────┴──────────────┐
       │                            │
   app/main.py                  app/review/main.py
   (领导风格沉淀 Bot)            (智能审核 Bot)
       │                            │
       │                            │
       └────────────┬───────────────┘
                    │
                    ▼
         app/data/rules.md     (共享规则库,Hermes 写,Bot 读)
                    │
                    ▼
         data/reviews/         (审核结果存档)
```

## 目录结构

```text
app/review/
├── __init__.py             # 公开 API:parse_docx / load_rules / review_text / format_review_result
├── parser.py               # .docx 解析(复用 zipfile + ET)
├── reviewer.py             # LLM 调用 + 语义类规则审核
├── format_checker.py       # 格式类规则正则检测(5 条)
├── rule_loader.py          # rules.md 加载(带 mtime 缓存)
├── output_formatter.py     # 审核意见格式化(纯文本)
├── main.py                 # 企微 Bot 入口(独立进程)
└── config.example.env      # 配置示例
```

## 快速开始

### 1. 安装依赖

```bash
python -m pip install -r app/requirements.txt
```

### 2. 配置凭证

```bash
cp app/review/config.example.env .env
# 编辑 .env,填入 WECOM_REVIEW_BOT_ID 和 WECOM_REVIEW_BOT_SECRET
```

### 3. 启动 Bot

```bash
# 检查配置(不连企微)
python -m app.review.main --check-config

# 启动 Bot
python -m app.review.main
```

### 4. 测试

```bash
# 单元 + 端到端测试
python tests/test_reviewer.py

# Bot 存档 + 配置测试
python tests/test_review_bot.py
```

## 规则管理(走 Hermes 对话)

**第一版预置 10 条规则**,写在 `app/data/rules.md` 里。

规则库由 **Hermes 这边手动维护**,Bot 启动时加载(支持 mtime 缓存,但**不热更新**,改完需重启 Bot)。

新规则入库流程:
1. 用户跟 Hermes 对话,丢错文/口述一条规则
2. Hermes 抽出"规则候选"(ID、严重程度、检查方式、正反例)
3. 用户**确认**后,Hermes 写入 `app/data/rules.md`
4. 重启审核 Bot 让新规则生效

## 两层审核架构

**格式类规则 → 代码正则检测（稳定，无 LLM 依赖）:**
- `quote-pair`: 引号不成对
- `num-unit`: 数字和单位间空格
- `mixed-punct`: 中英文标点混用
- `toc-no-ordinal`: 目录项/正文区章节标题带序号
- `toc-seq-skip`: 目录序号跳号

**语义类规则 → LLM CoT + 结构化输出 + 3次取并集:**
- `title-truncated`: 新闻标题被截断
- `content-mismatch`: 标题和正文不匹配
- `content-incomplete`: 正文语义不完整
- `toc-mismatch`: 目录与正文不匹配
- `content-out-of-scope`: 内容不在收录范围
- `content-wrong-section`: 内容放错板块
- `content-duplicate`: 重复内容
- `content-outdated`: 过时信息

## 审核流程

1. 用户丢一个 `.docx` 文件到审核 Bot
2. Bot 立即回:"已收到文件,在努力审核了,请稍等……"
3. 解析 .docx → 格式类规则正则检测 → LLM 语义审核(3次取并集)
4. 合并结果，按出现顺序输出审核意见
5. 存档到 `data/reviews/2026-06-13-001/`

### 拒接规则

- **非文件消息**(文字/图片/语音)→ 回:"本入口仅接收审核文档(.docx),请直接发送文件"
- **非 .docx 文件**(.pdf/.txt/.md)→ 同上拒接
- **空文件/解析失败** → 回明确错误信息

## 预置规则清单

| 分类 | 规则 | ID | 检测方式 |
|------|------|-----|----------|
| 格式 | 引号配对 | `quote-pair` | 正则代码 |
| 格式 | 数字/单位规范 | `num-unit` | 正则代码 |
| 格式 | 中英文标点混用 | `mixed-punct` | 正则代码 |
| 格式 | 目录/章节序号 | `toc-no-ordinal` | 正则代码 |
| 格式 | 目录序号跳号 | `toc-seq-skip` | 正则代码 |
| 语义 | 标题截断 | `title-truncated` | LLM CoT |
| 语义 | 标题正文不匹配 | `content-mismatch` | LLM CoT |
| 语义 | 内容不完整 | `content-incomplete` | LLM CoT |
| 语义 | 目录正文不匹配 | `toc-mismatch` | LLM CoT |
| 语义 | 内容超出范围 | `content-out-of-scope` | LLM CoT |
| 语义 | 内容放错板块 | `content-wrong-section` | LLM CoT |
| 语义 | 重复内容 | `content-duplicate` | LLM CoT |
| 语义 | 过时信息 | `content-outdated` | LLM CoT |

## 第一版明确不做

- 多文件类型(只支持 .docx)
- 打分 / 总评 / 改稿建议
- 政治表述合规检查
- 规则热更新(改完需重启 Bot)
- 消息卡片 / 富文本
- 会话状态(完全被动型)
- 白名单 / 权限控制

## 存档结构

```text
data/reviews/
└── 2026-06-13-001/              # 日期-序号
    ├── source/
    │   └── 汇报材料.docx         # 原始文件
    ├── report.md                # 审核意见(纯文本)
    └── meta.md                  # 时间 / Bot ID / 触发用户 / 解析预览
```

## 测试

- `tests/test_reviewer.py`:覆盖 reviewer.py 核心逻辑
- `tests/test_review_bot.py`:覆盖存档机制 + 配置加载
- 合计 19 个测试,全过

## 设计文档

见 `docs/review-module-design-v0.1.md`。
