# M-Agent 智能审核模块

企业微信机器人自动审核 `.docx` 文档，标出低级错误。

## 功能

- 接收企业微信文件消息（`.docx`）
- 两层审核架构：
  - **格式类规则** → 代码正则检测（稳定）
  - **语义类规则** → LLM CoT + 3次取并集
- 按出现顺序输出问题清单
- 审核结果存档到 `data/reviews/`

## 快速开始

```bash
# 安装依赖
pip install -r app/requirements.txt

# 配置（复制示例并填入 Bot ID/Secret）
cp app/review/config.example.env .env

# 检查配置
python -m app.review.main --check-config

# 启动 Bot
python -m app.review.main
```

## 审核规则

| 类型 | 规则 | 检测方式 |
|------|------|----------|
| 格式 | `quote-pair` | 正则代码 |
| 格式 | `num-unit` | 正则代码 |
| 格式 | `mixed-punct` | 正则代码 |
| 格式 | `toc-no-ordinal` | 正则代码 |
| 格式 | `toc-seq-skip` | 正则代码 |
| 语义 | `title-truncated` | LLM CoT |
| 语义 | `content-mismatch` | LLM CoT |
| 语义 | `content-incomplete` | LLM CoT |
| 语义 | `toc-mismatch` | LLM CoT |
| 语义 | `content-out-of-scope` | LLM CoT |
| 语义 | `content-wrong-section` | LLM CoT |
| 语义 | `content-duplicate` | LLM CoT |
| 语义 | `content-outdated` | LLM CoT |

## 目录结构

```
app/review/           # 审核模块核心代码
  ├── main.py         # Bot 入口（独立进程）
  ├── reviewer.py     # LLM 调用 + 语义审核
  ├── format_checker.py  # 格式类规则正则检测
  ├── parser.py       # .docx 解析
  ├── output_formatter.py  # 审核意见格式化
  └── rule_loader.py  # 规则库加载

app/data/rules.md     # 审核规则库
tests/                # 单元测试
```

## 测试

```bash
python tests/test_reviewer.py
python tests/test_review_bot.py
```
