# 审核两阶段拆分设计方案

## Context

**问题**：LLM 一次塞太多规则（8条）+ 太多内容（65+段落），导致执行不完整/超时/漏报。

**解法**：拆成两阶段，每阶段独立调用 LLM，每次只处理部分规则。

---

## 审核规则拆分

| 阶段 | 规则类型 | 具体规则 | 检测方式 |
|------|---------|---------|---------|
| 第一阶段 | 格式类 | `quote-pair`、`num-unit`、`mixed-punct`、`toc-no-ordinal`、`toc-seq-skip` | 正则代码（秒级） |
| 第一阶段 | 基础内容 | `title-truncated`、`content-mismatch`、`content-incomplete` | LLM 第一批 |
| 第二阶段 | 深度内容 | `toc-mismatch`、`content-out-of-scope`、`content-wrong-section`、`content-duplicate`、`content-outdated` | LLM 第二批 |

---

## 消息流程

### 阶段一（立即发送）

```
第一阶段审核完成（低级错误）

格式检查 + 基础内容审核，共 N 条：
错误1:【规则标签】问题描述
所属段落：原文（前40字）...
错误2:【规则标签】问题描述
所属段落：原文（前40字）...
...

第二阶段审核中，请稍候...
```

### 阶段二（追加发送）

```
第二阶段审核完成（内容质量）

深度内容审核，共 M 条：
错误1:【规则标签】问题描述
所属段落：原文（前40字）...
...

点击查看完整存档：data/reviews/<date-seq>
```

---

## 实现要点

1. **reviewer.py 拆分**：将 `review_text` 拆为 `review_phase1`（格式+基础内容）和 `review_phase2`（深度内容）
2. **main.py 发送逻辑**：
   - 阶段一完成后立即发送
   - 阶段二完成后追加发送（不重复阶段一结果）
3. **存档**：两阶段结果分别存档，文件名相同但 phase1/phase2 标记
4. **敏感词脱敏**：两阶段输出均经过 `_sanitize_text` 处理

---

## 数据流

```
用户发送 .docx
    ↓
parse_docx → paragraphs
    ↓
phase1: check_format_rules() + llm_review([title-truncated, content-mismatch, content-incomplete])
    ↓
reply_stream(阶段一消息)
    ↓
phase2: llm_review([toc-mismatch, content-out-of-scope, content-wrong-section, content-duplicate, content-outdated])
    ↓
reply_stream(阶段二追加消息)
    ↓
save_review(完整存档)
```

---

## 风险

- LLM 两次调用，成本 x2
- 第一阶段"第二阶段审核中"提示语需要清理（不要让用户误以为还在审核）
- 两阶段之间间隔 2-3 分钟，用户可能会问"怎么还没完"
