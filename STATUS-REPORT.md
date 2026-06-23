# M-Agent 状态报告（审核模块）

> 文档角色:开发日志/变更记录。
> 本仓库仅包含智能审核模块代码。

---

## 一一一、[2026-06-23 下午] 敏感词脱敏修复

### 问题

`010` 审核结果发送时触发 40201 反垃圾检查：
- `original_text` 截断到40字后仍含敏感词（"军事打击"、"哈梅内伊"等）
- 企业微信会扫描全文而非只看长度

### 修复

`output_formatter.py` 新增 `_sanitize_text()` 函数，对敏感词做替换而非仅截断：

```python
_SPAM_SENSITIVE_PATTERNS = [
    re.compile(r"军事打击|武装冲突|空袭|导弹|核武器|生化|战争"),
    re.compile(r"哈梅内伊|内贾德|苏莱曼尼|伊朗领袖|伊朗总统"),
    re.compile(r"封锁霍尔木兹|石油运输受阻"),
]
```

- 描述 (`f.description`) 和原文 (`f.original_text`) 都经过脱敏处理
- 存档报告 (`report.md`) 同步脱敏，避免敏感内容泄露日志

### Bot 状态

- PID: 84370 在线
- 日志: /tmp/review-bot.log

---

## 一零九、[2026-06-23] 审核两层架构升级 & 输出格式重调

### 两层审核架构

**格式类规则 → 代码正则检测（稳定）:**
- `quote-pair`: 引号不成对
- `num-unit`: 数字和单位间空格
- `mixed-punct`: 中英文标点混用
- `toc-no-ordinal`: 目录项/正文区章节标题带序号
- `toc-seq-skip`: 目录序号跳号

**语义类规则 → LLM CoT + 结构化输出 + 3次取并集:**
- `title-truncated`、`content-mismatch`、`content-incomplete`
- `toc-mismatch`、`content-out-of-scope`、`content-wrong-section`
- `content-duplicate`、`content-outdated`

### 输出格式重调

- 按出现顺序逐条列
- 格式：`错误N:【类型】描述` + `所属段落：原文`
- `title-truncated` 的"所属段落"显示**原文**（截断的标题），方便在文档中搜索定位

### 文件改动

- `app/review/format_checker.py` — **新建**，5条格式规则正则检测
- `app/review/reviewer.py` — CoT prompt + 合并逻辑
- `app/review/output_formatter.py` — 输出格式重调
- `tests/test_reviewer.py` — 更新测试适配新架构

### 待办

- ~~规则文档同步更新 `docs/review-module-design-v0.1.md`~~ ✅ 已更新
- ~~`app/review/README.md` 同步更新~~ ✅ 已更新

---

## 一一零、[2026-06-23 下午] 企业微信 Bot 问题排查 & 凭证更新

### 问题现象

审核完成后，发送回复时企业微信报错：
- `846609 "aibot websocket not subscribed"` — 旧 Bot 凭证问题
- `40201 "anti-spam check"` — 回复内容触发反垃圾检查

### 排查结论

1. MiniMax API 完全正常
2. WeChat WebSocket 连接能建立并认证
3. 审核流程完全正常（存档完整）
4. 问题出在：①旧 Bot 凭证失效 ②回复内容触发反垃圾

### 修复措施

1. **更新 Bot 凭证** — 改用新的 Bot ID (`aibvyZ1_a6ezjW6l27uY5er-DXJnq73vzk6`)
2. **输出脱敏** — `output_formatter.py` 原文只显示前40字，避免触发敏感词过滤
3. **发送超时保护** — `main.py` reply_stream 加 30 秒超时保护

### 实测结果

- `微众银行信息内参周报2025年第50期.docx` — 8 个问题，审核结果**发送成功** ✅

---

## 一零八、[2026-06-22] 审核功能三层架构升级 & 输出格式优化

### 审核规则三层架构

**第一层:识别文档结构**(不审核,只定位)
- 文档头 / 目录(TOC) / 页脚 / 正文区

**第二层:正文区三关审核**
- `title-truncated`:新闻标题是否截断
- `content-mismatch`:标题和正文说的是否同一件事
- `content-incomplete`:正文段是否在句中戛然而止

**第三层:目录(TOC)专项审核**
- `toc-no-ordinal`:目录项不应带"一、二、三"序号
- `toc-seq-skip`:序号不应跳号
- `toc-mismatch`:目录与正文应在章节名、标题、顺序上对得上

**全局格式规则**
- `quote-pair` / `num-unit` / `mixed-punct`

### 实测结果

**第 23 期**:8 条问题,76 秒,全对
**第 21 期**:8 条问题,52 秒,全对

### 输出格式优化
- 按出现顺序逐条列,不用分组
- 格式:错误N: 描述（类型）；原文：「...」
- 结尾无规则统计

### Bot 状态
- PID: 41284 在线
- 日志: /tmp/review-bot.log

---

## 一零七、[2026-06-13] 审核 Bot 上线

### 完成内容
- review 模块从 0 到上线
- Bot ID: aibsq5xHDu-Rmd90EppF7JkimdBvjsWu7E3
- 初始 10 条规则,LLM-only 方案
- 审核存档到 data/reviews/

### Bot 操作
```
cd ~/Desktop/M-Agent && python3.11 -u -m app.review.main --log-file /tmp/review-bot.log
```
