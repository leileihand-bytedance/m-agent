# M-Agent 状态报告

> 文档角色:开发日志/变更记录。
> 当前真实结构以 `CLAUDE.md` 和 `app/review/` 代码为准。

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

### 待办
- 审核模块独立化(本次讨论后暂不做)
- CLAUDE.md + STATUS-REPORT.md 建立

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

---

## 一零六至一零一、[2026-05-20] 领导风格沉淀模块核心功能完成

### 阶段 1:项目骨架 + 企业微信文本收发
- 简化结构:docs/ + app/ + data/
- 企业微信长连接 SDK(AiBotSDK)接入
- 文本消息收发验证通过

### 阶段 2:文件接收和保存
- 企业微信文件消息监听
- 文件下载 + 解密 + 保存
- 未指定领导时提示先指定
- 验证:24 tests OK

### 阶段 3:文件解析
- .md 直接使用(不生成 .parsed.md)
- .docx 通过 zipfile + ElementTree 解析
- .txt 支持 utf-8-sig / utf-8 / gb18030
- PDF 有清晰失败提示

### 交互方式调整
- 用户发"把这个提炼到黄总"后发文件
- 只发文件/文字 → Bot 追问领导归属
- 领导名只对下一份材料有效

### 验证结果
```
python -m unittest app/test_main.py
24 tests OK
```

---

## 早期日志(2026-05-20 之前)

## 2026-05-20

### 今日目标

推进第一阶段“企业微信经验沉淀入口”的最小闭环，先完成项目骨架和企业微信文本收发验证。

### 已完成

1. 创建第一阶段简化项目骨架：
   - `docs/`
   - `app/`
   - `data/`

2. 创建基础项目文件：
   - `README.md`
   - `.gitignore`
   - `app/README.md`
   - `app/config.example.env`
   - `app/main.py`
   - `app/prompts/style_extraction.md`
   - `data/leaders/example-leader/profile.md`
   - `data/leaders/example-leader/update-log.md`

3. 明确第一阶段不创建复杂框架目录：
   - 不创建 `agents/`
   - 不创建 `templates/`
   - 不创建 `knowledge/`
   - 不创建 `runs/`
   - 不创建 `services/`

4. 完成 MiniMax M2.7 配置口径：
   - `MODEL_PROVIDER=minimax-anthropic`
   - `MODEL_NAME=MiniMax-M2.7`
   - `MODEL_BASE_URL=https://api.minimaxi.com/anthropic`
   - `ANTHROPIC_BASE_URL=https://api.minimaxi.com/anthropic`

5. 完成企业微信智能机器人长连接文本收发：
   - 使用 `wecom-aibot-sdk==1.0.7`
   - 程序可读取 `.env`
   - 程序可连接企业微信长连接
   - 企业微信认证成功
   - 收到用户文本消息
   - 成功回复文本消息
   - 收到企业微信 reply ack

6. 新增本地测试：
   - `app/test_main.py`
   - 覆盖配置读取
   - 覆盖文本回复逻辑
   - 覆盖“沉淀领导风格：张总”指令提示

### 验证结果

1. 单元测试通过：

```text
python -m unittest app/test_main.py
5 tests OK
```

2. 配置检查通过：

```text
python app/main.py --check-config
配置检查通过
模型：MiniMax-M2.7
数据目录：data
```

3. 企业微信长连接验证通过：

```text
WebSocket connection established
Authentication successful
收到文本消息
回复消息发送成功
Reply ack received
```

### 当前风险

1. 企业微信 Bot Secret 和 MiniMax API Key 曾在聊天中明文出现。
2. 建议在完整流程测试跑通后，到企业微信和 MiniMax 后台重新生成密钥，只保存在本地 `.env`。
3. 当前 `.env` 已被 `.gitignore` 排除，但仍需避免复制、截图或提交。

### 下一步

进入阶段 2：文件接收与保存。

下一步目标：

1. 监听企业微信文件消息。
2. 接收用户上传文件。
3. 将原始文件保存到：

```text
data/leaders/某领导/source/
```

4. 未指定领导时上传文件，机器人提示先发送：

```text
沉淀领导风格：姓名
```

5. 本阶段暂不解析文件内容，先只验证文件能收到并保存。

### 阶段 2 进展补充

已完成本地代码实现：

1. 增加内存会话状态，用于记录某个用户当前正在沉淀哪位领导。
2. 增加企业微信 `message.file` 文件消息监听。
3. 增加文件下载后保存逻辑。
4. 原始文件保存到：

```text
data/leaders/某领导/source/
```

5. 同步生成 `meta.md`，记录领导、发送人、消息 ID、原始文件名和接收时间。
6. 如果用户未先发送“沉淀领导风格：姓名”就上传文件，机器人会提示先指定领导。

新增本地测试：

1. 会话能记住用户对应的领导。
2. 未指定领导时能识别为空状态。
3. 文件保存会写入原始文件和元信息。
4. 领导名称和文件名会做路径安全处理。

验证结果：

```text
python -m unittest app/test_main.py
9 tests OK
```

真实企业微信文件收发验证状态：

1. 长连接可以建立并认证成功。
2. 本次等待用户发送“沉淀领导风格：张总”和文件时，未收到新的企业微信消息推送。
3. 已停止长连接测试进程，避免持续占用。
4. 后续需要重新启动 `python app/main.py`，再由用户在企业微信中发送文本和文件完成实测。

### 阶段 2 调试补充

问题现象：

1. 后续实测中，程序可以收到 `message.file` 文件消息并完成回复。
2. 但没有打印“已保存文件”，说明文件消息进入了处理函数，却没有走到保存成功分支。

本轮处理：

1. 增加 `extract_file_payload`，统一解析文件下载地址、解密密钥和文件名。
2. 支持 SDK 文档中的字段：

```text
file.url
file.aeskey
file.filename
```

3. 同时兼容常见字段变体：

```text
file.download_url
file.aes_key
file.name
```

4. 增加 `describe_file_message_structure`，当找不到下载地址时，只返回字段名称，不返回下载地址、密钥等敏感值。
5. 企业微信真实消息如果字段仍不匹配，机器人会回复类似：

```text
文件消息已收到，但暂时没找到下载地址。诊断信息：body字段：...；file字段：...
```

验证结果：

```text
python -m unittest app/test_main.py
12 tests OK

python app/main.py --check-config
配置检查通过
```

密钥检查：

```text
已检查非 .env 文件，未发现企业微信或模型密钥明文。
检查命令本身不记录到文档，避免把密钥片段写入日志。
```

真实企业微信验证状态：

1. 重新启动长连接后，连接和认证成功。
2. 等待约 2 分钟，未收到新的企业微信消息推送。
3. 已停止长连接测试进程。
4. 下一次实测需要重新发送：

```text
沉淀领导风格：张总
```

然后再发送文件。

### 阶段 2 真实验证完成

验证时间：2026-05-20 16:40 左右

验证结果：

1. 企业微信长连接建立成功。
2. 企业微信机器人认证成功。
3. 收到“沉淀领导风格”文本消息并回复成功。
4. 收到文件消息。
5. 文件下载成功。
6. 文件解密成功。
7. 原始文件和元信息文件均已保存。

保存结果：

```text
data/leaders/张总测试/source/20260520-164019-深圳政策动态周报-2026-0427至0503.md
data/leaders/张总测试/source/20260520-164019-meta.md
```

阶段结论：

1. 阶段 2“文件接收与保存”的主链路已经跑通。
2. 后续可以进入阶段 3“文件解析”。
3. 当前长连接仍是手动启动方式，后续如需长期使用，再考虑后台守护、自动重启和日志文件。

### 阶段 3 文件解析进展

本轮目标：

1. 文件保存后，自动生成一份给 AI 后续读取的统一文本文件。
2. 第一版支持 `.md`、`.txt`、`.docx`。
3. PDF 如果当前环境缺少解析依赖，要返回清楚提示，不能影响原始文件保存。

已完成：

1. 增加 `ParsedFile` 解析结果结构。
2. 增加 `parse_source_file`。
3. `.md`、`.txt` 文件按文本读取，支持 `utf-8-sig`、`utf-8`、`gb18030`。
4. `.docx` 文件通过标准库读取 `word/document.xml` 并提取段落文本。
5. 解析成功后，在同目录生成：

```text
原文件名.parsed.md
```

6. 企业微信回复中会同时说明“已保存”和“已解析”。
7. 解析失败时，企业微信回复中会说明失败原因。

验证结果：

```text
python -m unittest app/test_main.py
15 tests OK
```

真实材料解析验证：

```text
data/leaders/张总测试/source/20260520-164019-深圳政策动态周报-2026-0427至0503.parsed.md
```

解析结果：

```text
解析成功，提取 7754 个字符。
```

PDF 当前状态：

```text
当前环境缺少 PDF 解析依赖，暂时无法解析 PDF。
```

阶段结论：

1. 阶段 3 的 `.md`、`.txt`、`.docx` 主能力已经具备。
2. PDF 已具备清晰失败提示，但还未安装解析依赖。
3. 下一步可以进入阶段 4“领导风格提炼”，也可以先补 PDF 解析依赖。

### 阶段 3 轻量化调整

调整原因：

1. 用户指出：如果上传文件本身就是 `.md`，没有必要再生成一份 `.parsed.md`。
2. 这个判断成立，重复文件会增加理解和维护成本。

调整后的规则：

1. `.md` 文件不再生成 `.parsed.md`，直接作为可提炼材料。
2. `.txt` 文件仍生成 `.parsed.md`，便于后续统一按 Markdown 读取。
3. `.docx` 文件仍生成 `.parsed.md`。
4. `.pdf` 当前仍按“可尝试解析，失败给出明确提示”处理。

验证结果：

```text
python -m unittest app/test_main.py
16 tests OK
```

补充验证：

```text
Markdown 文件可直接用于提炼，不生成重复的 .parsed.md。
```

### 交互方式调整

调整原因：

1. 原来的“沉淀领导风格：姓名”太像程序命令，不符合用户希望“直接丢材料”的使用方式。
2. 用户明确要求机器人只做一件事：收到文件或文字材料后，提炼领导风格。
3. 领导归属必须明确；只发文件时不能默认沿用历史上下文。

调整后的交互规则：

1. 用户发送 `黄总`，机器人只把它作为下一份材料的领导归属。
2. 用户发送 `把这个提炼到黄总` 后，再发文件，文件归到黄总。
3. 用户发送 `把这段提炼到黄总：具体材料内容`，机器人直接保存这段文字材料。
4. 用户只发送文件，机器人先暂存文件并追问：

```text
已收到文件。这份材料要提炼到哪位领导？请直接回复：黄总
```

5. 用户只发送文字材料但没说领导，机器人先暂存文字并追问：

```text
这段文字要提炼到哪位领导？请直接回复：黄总
```

6. 用户补充领导后，机器人继续处理刚才暂存的文件或文字材料。
7. 领导名只对下一份材料有效，用完即清空，不长期沿用。

已完成：

1. 增加 `TextIntent`，用于识别文本里的领导名和材料内容。
2. 增加 `PendingMaterial`，用于暂存缺少领导归属的文件或文字。
3. 增加一次性 `next_leader` 逻辑。
4. 增加领导名清洗，去掉开头结尾的逗号、冒号等标点。
5. 文件和文字材料统一进入 `process_material`。

验证结果：

```text
python -m unittest app/test_main.py
24 tests OK
```
## 更新日期: 2026-06-22

**[审核]** 微众银行信息内参周报2026年第23期.docx | 发现 8 处问题 | 16:01

**[测试通过]** agent | 24 tests OK | 16:05
**[审核]** 微众银行信息内参周报2026年第21期.docx | 发现 8 处问题 | 16:05
**[测试通过]** review | 5 tests OK | 16:01
