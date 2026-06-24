# M-Agent 状态报告（审核模块）

> 文档角色:开发日志/变更记录。
> 本仓库仅包含智能审核模块代码。

---

## 一一三、[2026-06-24] Phase1/Phase2 并行执行 & 规则持续优化

### Phase1/Phase2 并行化

- Phase1 两次 LLM 调用改为 `asyncio.gather` 并行执行，耗时从 ~185s 降至 ~90s
- Phase2 同样改为并行执行
- `review_phase1` / `review_phase2` 改为 `async` 函数
- 修复 `asyncio` 未 import 的 bug（导致审核完全没结果）

### 存档逻辑修复

- 之前只保存 phase2_result，导致 phase1 findings（格式检查等）丢失
- 现在合并 phase1 + phase2 所有 findings 再存档

### 新增格式规则 `consecutive-punct`

- 检测连续相同标点（`。。`、`！！`），排除书名号后紧跟标点的正常用法（`》。`、`》，`）
- Phase1 格式规则从 5 条增至 6 条

### rules.md 规则强化

- **content-mismatch**：会议名称须精确匹配，标题说"金融稳定工作会议"但正文说"国库工作会议"须报错配
- **content-wrong-section**：明确"资本市场综述/股市/债市/汇市评论"属于市场观察，不得放入监管动态

### Bot 状态

- PID: 91585 在线
- 日志: /tmp/review-bot.log

---

## 一一二、[2026-06-24] 两阶段审核拆分 & 规则优化

### 两阶段架构

**Phase 1（低级错误，11条）：**
- 格式正则（6条）：`quote-pair`、`num-unit`、`mixed-punct`、`consecutive-punct`、`toc-no-ordinal`、`toc-seq-skip`
- 基础语义（4条）：`title-truncated`、`content-mismatch`、`content-incomplete`、`toc-mismatch`
- LLM 调用：2次取并集

**Phase 2（内容质量，4条）：**
- `content-out-of-scope`、`content-wrong-section`、`content-duplicate`、`content-outdated`
- LLM 调用：2次取并集

### 消息流程

1. Bot 收到文件 → Phase1 完成后**立即发第一条**（"第一阶段审核完成（低级错误）"）
2. Phase2 完成后**追加发第二条**（"第二阶段审核完成（内容质量）"）
3. 第二条不再显示存档链接（面向用户，纯结果展示）

### 规则优化

- `title-truncated`：区分"截断（语句不完整）"vs"缩写（语句完整但简略）"
- `content-wrong-section`：明确板块归位标准（央行→监管动态，党和国家领导人→党政要闻，民营银行→同业动向，其他→市场观察兜底）
- `content-out-of-scope`：房地产调控/金融科技要放，普通科技不相关的不放
- `toc-mismatch` 保留在 Phase1
- Phase2 不含存档链接

### 文件改动

- `app/review/reviewer.py` — 两阶段拆分 + 调用次数优化
- `app/review/output_formatter.py` — phase1/phase2 格式化函数
- `app/review/main.py` — 两阶段发送逻辑
- `app/data/rules.md` — 规则定义优化

### Bot 状态

- PID: 90344 在线
- 日志: /tmp/review-bot.log

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
