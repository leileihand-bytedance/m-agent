# app/review - 旧审核 Bot

> 当前继续独立运行，后续包装为 `skills/review/`。迁移前不要大改。

## 这是什么

`app/review/` 是 M-Agent 的旧审核企业微信 Bot，支持三种自动识别的内容审核类型、一种需要用户明确提出的独立格式审核，以及多文件联合审核：

1. **内参周报** (`微众银行信息内参周报...`)：按 `app/data/rules.md` 审核。
2. **半月报** (`信息动态半月报...`)：按 `app/review/rules_halfmonthly.md` 审核。
3. **通用审核** (其他 `.docx`)：按 `app/review/rules_general.md` 审核文字质量。
4. **公文格式审核**：用户在发送 `.docx` 前或后明确说“格式审核”“按公文格式检查”；只检查实际格式，不审核文字内容。
5. **多文件联合审核**：用户直接连续发送 2 至 5 份 `.docx` 即可；系统自动归为一次任务，逐份执行原有内容审核，并检查正文与其他文件的一致性。

普通文件仍根据文件名和文档头自动分发到内容审核引擎。公文格式审核不会被自动识别，也不会接入通用 Word 审核主流程。

当前只有“单个 `.docx` 且识别为通用审核”的分支接入底座持久任务执行器。Bot 在 8 秒单/多文件静默分流后先返回任务编号，再由审核专用后台 worker 完成模型审核和结果发送。内参、半月报、公文格式、多文件联合审核和文字审核保持原执行方式，因此不能把本次接入理解为审核模块整体迁移。

## 架构定位

```text
当前：

企业微信审核 Bot
  -> app/review/main.py
  -> app/review/document_type.py    (识别内参/半月报)
  -> 内参: app/review/parser.py + format_checker.py + reviewer.py
  -> 半月报: app/review/parser.py + format_checker.py + halfmonthly_reviewer.py
  -> 用户明确要求格式审核: official_format_checker.py
  -> 连续多文件自动联合审核: intake.py + multi_file_reviewer.py
  -> 单个 Word 通用审核: task_execution.py + 审核专用 SQLite 队列
  -> ../M-Agent-Files/tasks/review/

后续迁移目标：

统一企业微信入口
  -> app/platform/
  -> skills/review/
  -> 复用 app/review/ 的解析和审核能力
```

## 目录结构

```text
app/review/
├── __init__.py             # 公开 API
├── parser.py               # .docx 解析(复用 zipfile + ET)
├── reviewer.py             # 内参周报：LLM 调用 + 语义类规则审核
├── halfmonthly_reviewer.py # 半月报：LLM 调用 + 半月报专属规则 + 标红定位
├── general_reviewer.py     # 通用审核：文字质量审核
├── task_execution.py       # 单个 Word 通用审核：持久任务、检查点和安全交付
├── quality_evaluation.py   # 通用审核真实文件评测：去重选样、运行和评分数据
├── review_metrics.py       # 评测用模型请求、失败和降级阶段统计
├── intake.py               # 格式/多文件审核的持久化消息与文件组装
├── multi_file_reviewer.py  # 逐文件审核 + 跨文件确定性和语义一致性检查
├── official_format_checker.py # 独立公文格式审核：检查 docx 实际格式
├── official_format_rules.json # 从桌面公文模板提炼的确定性格式规则
├── general_term_checker.py # 通用审核术语检查器
├── term_loader.py          # 通用审核术语库加载器
├── term_library_general_webank.json  # 通用审核微众银行术语库
├── error_marker.py         # 通用审核/半月报：给 docx 加红色高亮和批注
├── document_type.py        # 文档类型识别（内参/半月报/通用）
├── format_checker.py       # 格式类规则正则检测(6 条)
├── rule_loader.py          # rules.md 加载(带 mtime 缓存)
├── output_formatter.py     # 审核意见格式化(纯文本)
├── main.py                 # 企微 Bot 入口(独立进程)
├── config.example.env      # 配置示例
├── rules_halfmonthly.md    # 半月报规则库
├── rules_general.md        # 通用审核规则库
└── README.md               # 本文件
```

## 快速开始

### 1. 安装依赖

```bash
uv sync --locked
```

审核 Bot 与写作、底座、运维和管理后台共用项目根目录 `.venv`，不要单独使用全局 `pip` 安装依赖。

### 2. 配置凭证

```bash
cp app/review/config.example.env .env
# 编辑 .env,填入 WECOM_REVIEW_BOT_ID、WECOM_REVIEW_BOT_SECRET
# 如需让审核模块单独走 DeepSeek,再填 REVIEW_ANTHROPIC_* 和 REVIEW_MODEL_NAME
```

### 3. 启动 Bot

```bash
# 检查配置(不连企微)
uv run --locked python -m app.review.main --check-config

# 启动 Bot
uv run --locked python -m app.review.main
```

### 4. 测试

```bash
# 单元 + 端到端测试
uv run --locked python tests/test_reviewer.py
uv run --locked pytest tests/test_review_main_flow_optimization.py -q

# Bot 存档 + 配置测试
uv run --locked python tests/test_review_bot.py

# 半月报测试
uv run --locked pytest tests/test_review_halfmonthly.py -q

# 通用审核测试
uv run --locked pytest tests/test_review_general.py -q
uv run --locked pytest tests/test_review_general_rules.py -q

# 独立公文格式审核测试
uv run --locked pytest tests/test_official_format_review.py -q

# 文件/指令衔接和多文件联合审核测试
uv run --locked pytest tests/test_review_intake.py tests/test_review_multi_file.py -q

# 单个 Word 通用审核持久任务测试
uv run --locked pytest tests/test_review_task_execution.py tests/test_review_intake.py tests/test_review_bot.py -v
```

## 单个 Word 通用审核持久任务

该分支使用独立数据库 `M-Agent-Files/runtime/task-execution/review.sqlite3`，默认只启动 1 个 worker，同一用户同一时间只运行 1 个通用审核任务。独立数据库用于防止未来写作 worker 误领审核任务。

执行分为两个检查点：

1. 审核处理：解析 Word、调用通用审核模型、保存报告或 `marked_` Word。
2. 结果交付：记录即将发送，再主动发送文字或附件；成功后记录已交付。

同一企业微信消息重复投递时只保留一个任务。Bot 在审核完成后重启会复用已保存结果，不重复调用模型。队列最终文字或附件只尝试发送一次；如果发送回执超时或恰好在发送过程中中断，系统无法确定企业微信是否已收到，为避免重复文件会停止自动重发，同时提示用户并向运维记录失败任务。管理员根据任务编号核对后处理。后台 worker 意外退出时会记录运维事件并自动重启，不允许 Bot 继续受理而队列静默停止。

单文件内容审核启动后，原暂存文件仍保留到 30 分钟有效期结束，供用户随后补说“格式审核”时复用；接入队列不能提前删除这份文件。

可选配置：

```text
M_AGENT_REVIEW_TASK_DB
M_AGENT_REVIEW_TASK_WORKERS=1
M_AGENT_REVIEW_TASK_POLL_SECONDS=0.25
M_AGENT_REVIEW_TASK_RECOVERY_SECONDS=5
M_AGENT_REVIEW_TASK_LEASE_SECONDS=120
```

当前尚未给用户开放队列任务取消命令，真实试点验收时一并确定交互方式。内参、半月报、公文格式、多文件和文字审核不得使用这组配置推断为已切流。

## 文档类型识别

Bot 根据文件名和文档头前 5 段自动识别：

| 文件名/标题含 | 识别为 |
|--------------|--------|
| "半月报" | 半月报 |
| "内参" 或 "周报" | 内参周报 |
| 其他 | 通用审核 |

识别后分别走不同审核引擎和规则库。

公文格式审核是例外：它不靠文件名或文档内容自动识别。用户可以先说格式审核再发文件，也可以在发出文件后 30 分钟内补说格式审核，系统会对对应 `.docx` 额外执行一次独立格式检查。

## 独立公文格式审核

### 触发方式

支持两种顺序：

1. 先说“格式审核”“审核公文格式”“按公文格式检查”，再发送 `.docx`；该文件只做格式审核。
2. 先发送 `.docx`，再在 30 分钟内补说“格式审核”；系统会复用刚发送的文件执行格式审核，不要求重新发送。
3. 格式审核状态为一次性，使用后清除；默认 30 分钟过期，Bot 重启后仍可恢复未过期状态。

文件先发时，原有内容审核可能已经启动；后续格式指令会追加一次独立格式检查，不会取消已经开始的内容审核。

直接发送文件、只说“帮我审核”，或者明确说“只审核内容，格式不用看”，都不会进入公文格式审核。

### 规则来源和判断口径

规则提炼自桌面 `格式模板 .docx`，模板 SHA-256 为 `024626b605851babfab67843370769b8f6621310b90f29c989a60a037186f8cd`，配置保存在 `app/review/official_format_rules.json`。

**不使用 Word 样式名称判错。** 模板中的“标题 1”“标题 2”等样式只用于目录和查看便利。程序按段落可见编号识别标题层级，再读取 Word 最终生效的字体、字号、加粗、对齐、缩进、行距和页面设置；即使全部段落都使用 `Normal` 样式，只要实际格式正确也会通过。

当前确定性规则：

| 对象 | 实际格式要求 |
|---|---|
| 页面 | A4 纵向；上下边距 2.54 厘米，左右边距 3.175 厘米 |
| 主标题 | 宋体 18 磅、加粗、居中、首行不缩进、固定值 29 磅 |
| 一级标题 `一、` | 黑体 16 磅、不加粗、两端对齐、首行缩进 32 磅、固定值 29 磅 |
| 二级标题 `（一）` | 楷体 16 磅、加粗、两端对齐、首行缩进 32 磅、固定值 29 磅 |
| 三级标题 `1.` | 仿宋 16 磅、加粗、两端对齐、首行缩进 32 磅、固定值 29 磅 |
| 正文 | 仿宋 16 磅、不加粗、两端对齐、首行缩进 32 磅、固定值 29 磅 |
| 所有段落 | 段前、段后均为 0 |

程序接受对应中文字体和常见英文字体别名，例如宋体/`SimSun`、黑体/`SimHei`、楷体/`KaiTi`、仿宋/`FangSong`。发现问题后，返回带红色定位和批注的 Word 文档；批注会说明当前实际格式和模板要求。该流程完全由代码检查，不调用大模型。

### 当前边界

- 第一版检查所有分节的页面设置，以及文档顶层非空段落。
- 标题角色按可见文字识别：第一个顶层非空段落为主标题，`一、`、`（一）`、`1.` 分别为一至三级标题，其余为正文。
- 当前模板没有给出表格、页眉、页脚、文本框的专门格式标准，因此暂不检查这些区域，避免无依据误报。
- 规则是这份常用公文模板的确定性基线，不推断其他公文文种的特殊版式。

## 多文件联合审核

### 使用流程

1. 用户直接发送 `.docx`，不需要事前说“联合审核”，也不需要发完说“开始审核”。每份文件到达后 Bot 都会立即确认，不会等下载、自动归集或模型审核启动后才回复。普通内容审核沿用“收到文件啦，正在加紧审核，请稍等（模型反应有点慢，你可以先干点别的，一会儿再来看）……”原话术。
2. 系统在最后一份文件到达后使用默认 8 秒的后台静默窗口归集连续文件：只有 1 份时走原单文件审核，2 至 5 份时自动走联合审核。该时间可用 `M_AGENT_REVIEW_AUTO_BATCH_SECONDS` 调整，等待过程不向用户暴露。
3. 系统综合文件名和正文中的附件编号、附件标题、引用关系判断主文件，不使用上传顺序兜底。
4. **系统不会把第一份上传文件默认当成正文。** 文件名和内容证据仍不足时，Bot 才会列出文件并要求用户回复“第2个是正文”或直接写主文件名。
5. 原有“联合审核”“开始审核”和“取消审核”指令继续兼容，但都不是默认流程的必需步骤。

### 检查范围

- 每份文件先按原有类型识别执行内参、半月报或通用内容审核。
- 代码确定性检查正文引用的附件编号和名称，识别附件缺失、编号重复、已上传但正文未引用，以及编号相同但标题不一致。
- 通篇模型只检查必须同时阅读两份以上文件才能确认的名称、日期、数量、金额、状态、统计范围和要求冲突。
- 每条模型问题必须同时提供两份真实文件中的原文证据；两侧文件编号、段落坐标和原文任一项无法验证，就不会进入批注。
- 文档内容按不可信输入处理，不能用材料内的命令改变审核规则；每轮最多保留 30 条高置信跨文件问题。
- 多文件总文字不超过 10 万字时执行通篇跨文件模型检查；超过后仍执行逐文件审核和确定性附件检查，并在结果中说明降级。

### 状态、返回和边界

- 待组装状态默认保留 30 分钟，持久化在 `M-Agent-Files/runtime/intake/review/`；底层通过公共 `app/platform/intake.py` 统一原子状态、匿名用户目录、文件安全暂存、路径隔离和过期清理。Bot 重启后文件不会丢失，后续文件或明确开始指令可继续已有批次。
- 单文件进入内容审核后，最近原件仍在有效期内保留给“先发文件、后说格式审核”复用；下一份普通文件到达时会替换它，不会误并入新的联合审核。
- 用户可说“取消审核”清空本次暂存文件。每次最多 5 份文件，总大小最多 50MB，仍受单文件接收上限约束。
- 一次联合审核只生成一个任务目录；原件分别进入 `input/`，报告和有问题的标注文档进入 `output/`。
- 聊天摘要明确显示主文件、各文件问题数和跨文件问题数；只回传实际有批注的 Word，没有问题的文件不重复回传。
- 当前只支持 `.docx`。PDF、Excel 和 PPT 多文件联合审核仍属于后续扩展范围。
- 当前格式审核、单/多文件分流和主文件识别仍位于旧审核模块；公共持久化与限制校验已经下沉，跨写作和审核统一的业务组装协议仍由 `TODO-003` 承接。

## 内参周报

### 两层审核架构

**格式类规则 → 代码正则检测（稳定，无 LLM 依赖）:**
- `quote-pair`: 引号不成对
- `num-unit`: 数字和单位间空格
- `mixed-punct`: 中英文标点混用
- `consecutive-punct`: 连续相同标点
- `toc-no-ordinal`: 目录项/正文区章节标题带序号
- `toc-seq-skip`: 目录序号跳号

**语义类规则 → 两阶段 LLM 审核:**
- `title-truncated`: 新闻标题被截断
- `content-mismatch`: 标题和正文不匹配
- `content-incomplete`: 正文语义不完整
- `toc-mismatch`: 目录与正文不匹配
- `content-out-of-scope`: 内容不在收录范围
- `content-wrong-section`: 内容放错板块
- `content-duplicate`: 重复内容
- `content-outdated`: 过时信息

**内参正文格式补充规则 → 代码检查:**
- `weekly-body-format`: 目录之后的板块标题、新闻标题和正文格式检查
- 如果文档没有单独的“目录”段，会从首个正式板块标题开始检查，避免把封面、期号、主编信息误算进正文区
- 如果文档存在“目录”段，正文格式检查会从目录结束后的首个正式板块开始，不检查目录区本身的字体、字号、缩进等格式
- 板块标题同时兼容 `同业动向` / `同业动态` 两种模板写法，避免把合法板块名误当正文
- 有 `.docx` 样式信息时，正文/新闻标题/板块标题会优先按 Word 真实样式识别，不再只靠“短句像不像标题”猜测
- `原文：...` 这类提示行会并入上一条正文；`前沿观点` 里的问句小标题会跟随主标题归入同一篇内容，避免误报“正文缺失”
- 两阶段发给模型的内参正文条目现在都直接传完整原文，不再裁成“正文摘要”，避免因为系统预裁剪漏掉错误或诱发“正文不完整”误报
- 为了给完整正文让出 token，阶段规则说明改用精简版，不再把长规则块重复塞进 prompt
- `weekly-body-format` 读取 `.docx` 样式时会忽略空 run，并补走段落样式链回退，减少“标题字体对了却被报宋体”“正文缩进明明正确却被误报”的情况
- `content-incomplete` 会做二次校正：完整句尽量过滤，明显戛然而止的尾句会统一改成通用描述，避免模型编造“未引用原文”这类原因
- `content-incomplete` 二次校正现在只允许落在正文段，不再允许把封面、主编、板块标题、新闻标题误报成“内容不完整”
- 文档没有目录区时，不再保留 `toc-mismatch` 结果，避免“无目录却报目录正文不匹配”
- 目录区仍会继续检查 `toc-no-ordinal` / `toc-seq-skip`，例如 `一、党政要闻`、目录跳号等问题
- `toc-mismatch` 现在除了模型判断外，还会用代码把目录条目和正文板块/标题逐项对照，专门兜住“正文改了但目录没刷新”的情况
- 当前模板口径：章节标题黑体 18pt，新闻标题黑体，正文宋体 12pt、1.15 倍行距、首行缩进约 0.85cm
- 内参语义类 finding 会尽量补齐 `target_text`，用于回原文精确标红

### 审核流程

1. 用户丢一个 `.docx` 文件到审核 Bot
2. Bot 立即回:"已收到文件,在努力审核了,请稍等……"
3. 解析 `.docx`
4. 启动两阶段审核：第二阶段会先在后台启动；第一阶段继续负责格式类规则 + 基础语义审核（默认 1 次模型调用,失败再重试 1 次）
5. 第二阶段在后台继续完成目录/内容质量审核（默认 1 次模型调用,失败再重试 1 次）
6. 合并结果后，基于原文生成审核文档：
   - 有问题时返回 `marked_原文件名.docx`
   - 无问题时不回传文档，只回复“没有发现问题，可以走审批了”
7. 存档到 `../M-Agent-Files/tasks/review/YYYY/MM/<task_id>/`

## 半月报

### 审核规则

**格式类规则（复用代码正则）:**
- `quote-pair`、`num-unit`、`mixed-punct`、`consecutive-punct`

**语义类规则（LLM + 部分代码预检）:**
- `content-incomplete`: 正文戛然而止
- `halfmonthly-date-mismatch`: 事件时间超出半月报标注范围
- `halfmonthly-section-order`: 一级标题顺序不符合标准顺序
- `halfmonthly-section-mismatch`: 段落放错一级标题
- `content-duplicate`: 同一事件重复
- `halfmonthly-leader-title`: 行内领导职务与排序规范

### 标准一级标题

半月报支持以下一级标题（可出现任意子集）：

- 业务动态及成果
- 工作动态及成果
- 行内重要会议
- 获得资质与荣誉
- 行外联络及交流

出现时必须保持以下顺序：
`业务动态及成果` → `工作动态及成果` → `行内重要会议` → `获得资质与荣誉` → `行外联络及交流`

### 领导职务与排序规范

行内领导默认按以下顺序和职务出现：

| 排序 | 姓名 | 默认公开职务 |
|------|------|--------------|
| 1 | 顾敏 | 董事长 |
| 2 | 李南青 | 党委书记 |
| 3 | 黄黎明 | 行长 |
| 4 | 万军 | 党委委员、监事会主席 |
| 5 | 马智涛 | 常务副行长、首席信息官 |
| 6 | 陈峭 | 副行长 |
| 7 | 方震宇 | 党委委员、副行长 |
| 8 | 王立鹏 | 党委委员、行长助理、首席财务官、董事会秘书 |
| 9 | 公立 | 党委委员、行长助理 |
| 10 | 万磊 | 企业及机构金融事业群副总裁 |
| 11 | 江旻 | 纪委书记、科技及智能事业群副总裁 |
| 12 | 陈婷 | 个人金融事业群副总裁 |

**例外说明：**
- 李南青：默认不写"首席合规官"，有监管来访单独请示
- 黄黎明：通常不写"党委副书记"；只有同一条信息里其他人员（不含李南青，含第三方来访人员）已采用党内职务口径时才一起写出
- 党内职务补齐：李南青单独写"党委书记"不触发；除李南青外，只要同一条信息里其他内部人员或第三方来访人员已写党内职务，出现的相关领导就要补齐对应党内职务

### 审核流程

1. 识别文档类型为半月报
2. 解析 `.docx`
3. 单阶段审核：格式正则 + 代码预检（时间范围、领导职务/排序）+ LLM 语义
4. 生成 findings，代码类 findings 会带上 `target_text`（如越界日期、错误职务、缺失党内职务对应的人名、排序错误的人名），便于回原文标红
5. 用 `app/review/error_marker.py` 在原文对应位置添加**红色高亮 + 批注**，生成 `marked_{原文件名}.docx`
6. 企业微信不再发送"错误1/错误2..."文字列表，直接返回文档：
   - 有问题时返回 `marked_原文件名.docx`
   - 无问题时不回传文档，只回复“没有发现问题，可以走审批了”
7. 存档到 `../M-Agent-Files/tasks/review/YYYY/MM/YYYYMMDD-NNN/`

### 标红能力

半月报复用通用审核的 `error_marker.py` 标红能力：

- 代码预检产生的 `halfmonthly-date-mismatch`、`halfmonthly-leader-title` 等 finding 会填充 `target_text`。
- LLM 语义类规则也在 prompt 中要求返回 `target_text`，用于精确定位。
- 半月报语义规则仍按错误所在语句提供上下文；共享格式规则只标红精确错误片段。
- 定位时优先搜索可直接复制到 Word 的长原文片段，再用内部段号辅助；内容也无法匹配时仍按内部段号落批注，并在批注里给出“定位原文”供用户手工搜索。

## 通用审核

### 审核规则

通用审核适用于既不是内参周报、也不是半月报的其他 `.docx` 文档，只检查文字质量，不做业务规则判断。

**格式类规则（复用代码正则）:**
- `quote-pair`、`num-unit`、`mixed-punct`、`consecutive-punct`

**代码类规则（稳定、低误报）:**
- `general-placeholder`: 占位内容未清理
- `general-heading-seq-skip`: 标题/列表编号跳号
- `general-heading-empty`: 标题后无正文
- `general-reference-missing`: 附件/附表引用悬空
- `general-attachment-name-mismatch`: 正文引用的附件名称与对应附件标题不一致
- `general-invalid-date`: 日期本身不存在或不符合日历常识
- `general-date-range-logic`: 同一句里显式起止日期前后逻辑不一致
- `general-term-variant`: 术语库中明确禁止的错写变体

**语义类规则（LLM）:**
- `general-typo`: 错别字
- `general-name-error`: 名称错误或不一致
- `general-grammar`: 语病
- `general-punctuation`: 标点符号错误
- `general-incomplete`: 内容没写完
- `general-duplicate`: 重复内容
- `general-logic-inconsistency`: 通篇阅读后才能确认的前后逻辑矛盾

### 微众银行专业术语库

通用审核内置一份专用术语库，用于减少模型把专业术语误判成错别字/名称错误，并对明确的术语错写做确定性检测。

```text
app/review/term_library_general_webank.json
app/review/term_loader.py
app/review/general_term_checker.py
```

术语库字段：

| 字段 | 说明 |
|------|------|
| `term_id` | 唯一 ID |
| `category` | institution / product / platform / technology / external_org |
| `standard` | 标准写法 |
| `allowed_aliases` | 允许的简称/别名/大小写变体 |
| `forbidden_variants` | 明确禁止的错写/旧错写/常见误写 |
| `allowed_suffixes` | 可选，仅中文错写检测使用；表示术语后面允许直接跟随的中文上下文词，用于减少误报 |
| `doc_types` | 当前只支持 `["general"]` |
| `severity` | `error` / `warning` |
| `notes` | 备注 |

首期覆盖范围：

- 微众银行主体名称：`微众银行`、`深圳前海微众银行`、`深圳前海微众银行股份有限公司`
- 核心产品：`微粒贷`、`微业贷`、`微贸贷`、`微车贷`、`微闪贴`、`微众银行财富+`
- 核心技术/平台：`OpenHive`（开放蜂巢）、`FISCO BCOS`、`DDTP`、`WeNOS`、`WeDataSphere`、`Link-SLB`、`SONiC`
- 常见外部机构：`中国人民银行`、`中国人民银行深圳市分行`、`国家金融监督管理总局`、`中国证券监督管理委员会`、`国家外汇管理局`

行为：

1. **术语保护**：`general_reviewer.py` 在构造每个 chunk 的 prompt 时，会从术语库中选出当前 chunk 实际出现或高度相关的术语，追加“受保护术语”段，提示模型不要因为生僻、中英混排、缩写就判错。
2. **明确错写检测**：`general-term-variant` 会在段落中命中 `forbidden_variants` 时直接产出 Finding。英文术语按不区分大小写的整词匹配；中文术语会避开“嵌在更长中文词里”的误命中。例如 `OpenHiev` / `openhiev` 都会被提示应为 `OpenHive`，但 `微业代理` 不会因为包含 `微业代` 而误报。
3. 术语库缺文件时安全降级，不影响审核主流程。

### 审核流程

1. 识别文档类型为通用审核
2. 解析 `.docx`
3. 单阶段审核：格式正则 + 代码规则 + LLM 语义，生成 findings
4. 有问题时，用 `app/review/error_marker.py` 在原文对应位置添加**红色高亮 + 批注**，生成并发送 `marked_{原文件名}.docx`；不再额外发送重复的“审核完成”文字。
5. 没有发现问题时，只回复通过话术，不生成、不发送 marked 文件。
6. 原始文件存入任务 `input/`，实际生成的 `marked_` 文件存入任务 `output/`。

### 标记流程（识别与标注拆分）

通用审核把"识别错误"和"标红错误"拆成两个阶段：

```text
识别阶段(LLM)
  -> 返回 paragraph_index + target_text + description

定位阶段(代码)
  -> 从 original_text 提取足够长的连续“定位原文”
  -> 先在整份文档搜索完整原文/定位原文
  -> 再用 paragraph_index 辅助消歧或作为最终保底
  -> 同段短目标重复时，用定位原文确定具体字符位置

标注阶段(代码)
  -> 拆分 run,按区间标红
  -> 添加只含事实问题的批注说明
```

### 当前实现约束

- 通用审核读取 docx 和回写 marked docx 共用同一套“可审核段落”遍历口径，包含表格里的非空段落。表格去重直接保留 XML 单元格对象，不再保存可能被复用的临时对象 ID，保证长表格多次解析的段落数量和顺序一致。
- 通用审核保留按 `6000` 字符拆分的逐段文字校对，同时对 `200` 至 `100000` 字的文档并行增加一次通篇逻辑校对；通篇校对只检查跨段矛盾，不重复检查错别字、标点和一般语病。
- `20000` 字及以上文档的每个分段会使用模型原生模式并行独立扫描两次，再合并候选，避免单次模型随机漏掉明显错误。结构化复核器单独使用零温度并关闭思考模式，保证输出格式稳定。
- `20000` 字及以上文档会对名称、语病、标点、重复、内容不完整和跨段逻辑等高误报风险候选并行复核两次；候选至少经过首轮识别和一次独立复核确认才保留。已精确命中原文并给出明确改法的错别字直接保留，避免复核模型过度删除。
- 数字类前后矛盾必须先满足统计时间、统计范围和统计对象一致。原文存在不同明确年份、一处是累计而另一处是当期、基层数与组织总数等范围不同且无法证明同口径时，按低置信结果丢弃；关联段落说明同时兼容“第 N 段”和模型偶尔返回的 `paragraph N`。
- 超过 `100000` 字时，为避免单次调用耗时和输出失控，跳过通篇模型调用，仍保留分段语义审核和全部代码规则。
- 模型只能返回当前分段中的段号；越界识别结果直接丢弃。回写 Word 时，长原文内容匹配优先于内部段号；内容匹配失败仍按合法段号添加批注，批注会附上可搜索原文，不再静默漏掉意见。
- `quote-pair` 会区分中文单引号和英文弯撇号。`People’s`、编辑器误转成左弯撇号的 `people‘s`、`students’` 等英文所有格或缩写不报错；真正未配对的中文 `‘` / `’` 仍继续检查，中文单引号内出现英文所有格也不会打断外层配对。
- 错别字提示词和规则库统一使用“原文错误片段应为正确写法”，修改前文本必须与 `target_text` 一致；普通分段审核使用零温度，输出前要求再次核对原文。若模型仍把原文 `7×24小时` 虚构成 `7×7小时`，该证据矛盾候选会进入两次高精度复核且要求一致确认；复核不可用时不交付这类矛盾意见，而不是在普通归一化阶段静默删除。
- 通用审核分块会识别尾部的“数字序号 + 短标签”记录头。字符边界原本会把 `5`、`Fortune` 留在上一块而把奖项说明切到下一块；现在会把记录头携带到下一块，与奖项名称和排名说明共同送审。已移除“短英文标签一律过滤”的结果层规则，`Forbes`、`Fortune` 等文本按实际相邻上下文判断。
- 标点类“多余空格”如果能在模型目标中确认，会缩小到实际的“标点 + 空格”字符并改正标点名称。例如原文真实存在 `、 ` 时，批注写“顿号后有多余空格”，并精确标红这两个字符。
- 同一段里如果已经有更具体的格式规则（如 `consecutive-punct`、`quote-pair`），会尽量去掉重复的 `general-punctuation`。
- 同一段同一 `target_text` 被同时报为错别字、语病或名称问题时，只保留优先级最高的一条，避免重复批注。
- 同一段的多个 target 互相包含时，只保留更长、更明确的目标。例如“考考”和“综合考考量”只保留后者对应的一条意见。
- `general-duplicate` 只保留较长文本的完整或近完整重复；短表格表头重复、概述后再详细展开不再报重复内容。
- `general-heading-empty` 和标题跳号检查会避开单行键值、小数/百分比、编号问卷题目；问卷题号仍参与后续编号状态推进，避免连带制造下一条跳号误报。
- 调研问卷中的可选分项提示（如“负债端所面临的困难及对策建议”）允许暂未填写，不按普通文章空标题报错。
- `general-reference-missing` 目前只覆盖“附件/附表”这类文字引用，不直接判断图片、图表实体是否真实存在。
- `general-attachment-name-mismatch` 当前只覆盖高确定性场景：正文里显式写出“附件编号 + 附件名称”时，才会和附件标题做一致性比对；支持附件与编号间有无空格、`附件1：《XXX》`、`附件1《XXX》`、`《XXX》（附件1）`、常见文件名和“意见反馈表”等表单名称。若相同名称实际列在另一个附件编号下，会直接提示真实编号。
- `general-invalid-date` 当前主要检查显式日期本身是否成立，例如 `2026年2月30日`、`4月31日`、`2026-13-05`；对未写年份的 `2月29日` 这类可能跨闰年的表达，先保守放过。
- `general-date-range-logic` 当前只检查**同一句里的显式起止区间**，例如 `7月9日至7月8日`、`2026年7月9日至2026年7月8日`、`2026年7月9日15:00-14:00`。不会拿整篇文档的多个日期做全局比较，也会保守放过 `2026年12月30日至1月5日` 这类可能跨年的省略写法，以及明确写了“次日”的跨夜时间段。
- `general-logic-inconsistency` 会读完整文档，重点检查附件对应、同一事实前后矛盾、总数与列项不一致、时间顺序和条件结论冲突；只依据文档内部证据，不联网猜测业务事实。长文数字矛盾还会经过时间口径硬过滤。
- 通用审核术语库按 chunk 注入“受保护术语”段，不要把整个库塞进单次 prompt；只有当前 chunk 相关术语才会出现。

### 标记规则

**按规则类型的标红粒度：**

| 规则类型 | 标红范围 |
|---------|---------|
| 所有 `general-*` 通用审核规则 | 只标红模型或代码给出的**精确错误片段** |
| `consecutive-punct` / `mixed-punct` / `num-unit` | 只标红**精确错误标点、文字或数字单位片段** |
| `quote-pair` | 只标红**出错的那一个引号字符**，不再把前面半句话一起染红 |

- 批注文本不再附带 `【错误类型】` 标签，也不显示“第369段”等内部编号；统一附加“定位原文”，用户可直接在 Word 中搜索。

**定位优先级：**

1. 优先用 `original_text` 或围绕 `target_text` 截取的长原文片段在全文搜索
2. 内容命中多处时，用内部 `paragraph_index` 辅助选择
3. 进入目标段后，再用长原文片段确定短 `target_text` 的具体出现位置
4. `target_text` 为空时，从 `description` 提取引号内容/中文词组作为定位关键词
5. “段末缺标点”强制定位到段落末尾，避免重复词命中前一个位置
6. 内容和目标均无法定位时，仍按合法内部段号落批注，并在备注提供可搜索原文

### 已知限制

- **同一段落重复 target**: 当前会优先用长定位原文消歧；如果模型原文自身也无法区分多个完全相同位置，才回退到第一次出现处，并在批注提供定位原文。
- **复杂 run 结构**: 对包含多个 `w:t` 节点、修订痕迹或特殊字段的 run,拆分策略会保留第一个文本节点、清空其余节点,极端复杂文档可能丢格式。
- **多实例错误合并**: 同一句话内多个不同错误会分别生成独立标注,但标红区间可能重叠,视觉上会合并成一片红色。

### 测试

- `tests/test_error_marker.py`: 覆盖句子级/半句级标红、多 run 拆分、fallback、越界处理
- `tests/test_bot_logging.py`: 覆盖按月切分、按用户分文件日志
- `tests/test_notification.py`: 覆盖管理员通知和冷却去重
- `tests/test_user_registry.py`: 覆盖英文名注册流程
- `tests/test_review_bot.py`: 覆盖文字消息审核

运行：

```bash
uv run --locked pytest tests/test_error_marker.py tests/test_review_general.py tests/test_review_general_rules.py tests/test_review_term_library.py tests/test_review_bot.py tests/test_review_main_flow_optimization.py tests/test_bot_logging.py tests/test_notification.py tests/test_user_registry.py -v
```

### 真实文件质量基线

`scripts/review_quality.py` 使用与 Bot 相同的 `parse_docx -> review_general -> mark_errors_in_docx` 主链路，不维护另一套测试审核逻辑。它会从历史审核输入中筛选通用 Word，先按文件 SHA-256 去重，再用正文标准化哈希和近重复分片覆盖率排除不同保存版本；短文只做精确正文去重，避免模板相似导致误合并。

第一批默认选 5 份，优先覆盖问卷、附件引用、长文、表格和常规材料：

```bash
uv run --locked python scripts/review_quality.py run --limit 5 --run-id YYYYMMDD-baseline-v1
```

模型或网络中断后，可使用相同样本清单恢复：

```bash
uv run --locked python scripts/review_quality.py run --limit 5 --run-id YYYYMMDD-baseline-v1 --resume
```

恢复时严格核对 `case_id + 文件 SHA-256`，只跳过 `completed`；`failed`、`partial_failed` 和没有结果的中断样本会重跑。每次真实请求记录 `model_calls`，连接失败或无效响应记录 `model_failures`；只有重试后仍未完成的分段、通篇逻辑或长文复核才进入 `degraded_stages`，避免把已恢复的偶发失败误判成不完整审核。

结果保存在：

```text
M-Agent-Files/evaluations/review/<run_id>/
```

目录包含样本清单、逐份 JSON、原件副本、标注文档、评分 CSV 和人工评分 Excel。真实文件名、原文和评分不得进入 Git。向外部模型重新发送历史文件前，必须取得用户针对本次评测的明确授权。

离线测试：

```bash
uv run --locked pytest tests/test_review_quality_evaluation.py -v
```

## 规则管理

**内参周报规则**：`app/data/rules.md`，Bot 启动时加载(支持 mtime 缓存,但**不热更新**,改完需重启 Bot)。

**半月报规则**：`app/review/rules_halfmonthly.md`，同样重启生效。

**通用审核规则**：`app/review/rules_general.md`，同样重启生效。

新规则入库流程:
1. 用户提供错文或口述一条规则
2. AI 开发工具抽出"规则候选"(ID、严重程度、检查方式、正反例)
3. 用户**确认**后，写入对应规则库
4. 重启审核 Bot 让新规则生效

## 模型配置

- 审核模块现在优先读取 `REVIEW_ANTHROPIC_API_KEY`、`REVIEW_ANTHROPIC_BASE_URL`、`REVIEW_MODEL_NAME`
- 如果没配审核专用变量，才回退到全局 `ANTHROPIC_*` / `MODEL_*`
- 推荐审核模块单独使用 DeepSeek Anthropic 兼容通道：`https://api.deepseek.com/anthropic`
- 搜索增强里的“主动搜索 API”仍是 MiniMax 专属能力；如果审核模型切到 DeepSeek，这一段会自动跳过，不会影响当前主流程

## 拒接规则

- **非文件/文字消息**(图片/语音/链接等)→ 回:"本入口接收 .docx 文件或直接发送文字,请发送需要审核的内容"
- **非 .docx 文件**(.pdf/.txt/.md 等)→ 同上拒接
- **空文件/解析失败/空文字** → 回明确错误信息

## 文字消息审核

除了 `.docx` 文件,用户也可以直接发送一段文字进行通用审核。

处理逻辑:

1. Bot 收到文本消息后,先回复:"收到文字啦,正在加紧审核,请稍等……"
2. 把文字按每个手工换行拆成段落并过滤空段，避免正文与附件清单之间存在空行时把多条附件合成一个大段
3. 走通用审核引擎(`review_general`)
4. 把原始文字、审核意见和段落预览一起存档到 `../M-Agent-Files/tasks/review/YYYY/MM/YYYYMMDD-NNN/`
5. 以纯文本形式返回审核意见

限制:

- 文字消息**不会生成 marked 文档**,只返回文本意见，但现在会保存 `input/文字消息.txt`、`output/report.md` 和 `meta.json`，便于事后复盘
- 文字消息固定走**通用审核**,不走内参/半月报规则
- 超长文字会自动按 `6000` 字符拆 chunk 做逐段审核；`200` 至 `100000` 字同时增加通篇逻辑校对

## 日志系统

Bot 使用 Python `logging` 模块，公共日志和用户日志都按天记录，并在单个文件达到上限后继续分片。

```text
../M-Agent-Files/runtime/logs/
├── review-bot-2026-07-13.log          # 当天公共日志
├── review-bot-2026-07-13.part-002.log # 当天超过阈值后的分片
└── users/
    └── user-001/
        └── 2026-07-13.log             # 该用户当天操作日志
```

配置项:

- `M_AGENT_LOGS_DIR`: 特殊部署时覆盖日志目录；默认由 `M_AGENT_DATA_DIR` 派生
- `M_AGENT_LOG_MAX_MB`: 单个日志文件上限，默认 `20MB`
- 每条日志都带 `user=<english_name>|userid=<userid>` 字段
- `system`、SDK 心跳等系统日志只进入公共日志，不再重复写入 `users/system/`
- 用户日志处理器最多同时保持 `64` 个文件句柄，超过后关闭最久未使用的句柄
- 旧的月度日志保留原文件，不重命名、不删除；新规则从 Bot 重启后生效
- 用户名映射实现已迁移到 `app/platform/user_registry.py`，审核模块原导入路径保留兼容。

## 异常通知和运维告警

当 Bot 运行中出现异常时,审核 Bot 负责给用户返回简短、可理解的提示；详细错误写入运维事件，由独立运维 Bot 统一通知管理员。

配置项(在 `.env` 中):

```bash
REVIEW_ADMIN_USER_ID=
REVIEW_ADMIN_NAME=
REVIEW_NOTIFICATION_COOLDOWN=300
REVIEW_DIRECT_ADMIN_NOTIFY=false
```

通知规则:

- 文件解析失败、审核失败、标注文档生成失败、结果发送失败都会写入 `../M-Agent-Files/runtime/ops/events/`。
- 运维 Bot 读取运维事件后，向管理员发送详细告警。
- 审核 Bot 默认不再直接向管理员发送详细报错，避免用户入口和运维入口职责混在一起。
- `REVIEW_DIRECT_ADMIN_NOTIFY=true` 仅用于兼容旧部署；正常运行应保持 `false`。
- 附件回传失败时，审核 Bot 会告诉用户审核已完成但文件发送失败；只有运维事件写入成功时才说明已提醒管理员，并返回任务编号供人工取回。
- 同一种异常类型仍保留冷却机制，避免运维 Bot 刷屏。

## 企业微信附件发送稳定性

审核 Bot 返回 `marked_原文件名.docx` 时统一使用底座 `AttachmentDelivery`，不再在审核入口内手写上传和文件消息重试。

已知行为：

- 发送前再次校验结果必须位于本次审核任务 `output/`，拒绝目录外路径、符号链接和发送前被替换的文件。
- 同一交付实例串行上传，根据文件大小设置等待时间，并重试完整“上传 + 发送”流程。
- 默认遵守 SDK 100 个 512KB 分片的约 50MB 上限；超限或多次失败时保留本机结果，用户收到任务编号，详细原因进入运维事件和交付指标。
- 当前不自动压缩 Word/PPT 内图片，避免静默降低画质。未来启用压缩时必须在被压缩位置或回复消息中明确提醒用户替换原图。

配置项（在 `.env` 中）：

```bash
REVIEW_REPLY_ACK_TIMEOUT_SECONDS=30
```

说明：

- 审核 Bot 启动时仍将 SDK 基础回执等待设为 `30` 秒；公共交付层会在此基础上按文件大小计算实际上传等待。
- 如果后续仍出现大文件上传回执超时，先查看 `../M-Agent-Files/runtime/logs/`、任务 `status.json` 和 `runtime/ops/events/`，不要只反复调大 SDK 等待时间。

## 用户注册（已启用）

Bot 已配置为要求新用户先注册名字再使用。

配置项（在 `.env` 中）：

```bash
REVIEW_REQUIRE_REGISTRATION=true
```

启用后流程：

1. 新用户第一次发消息 → Bot 回复：
   > 欢迎使用智能审核BOT！
   > 这是你第一次使用，先互相认识一下吧，请先告诉我你的英文名（例如：Jack）。

2. 用户发送名字（支持中文、英文、数字、下划线、短横线，2-30 字符）→ 记录到统一运行数据目录的用户名表
   如果用户发来的文字本身就是一个合法名字，Bot 会直接完成注册，并回复：
   > 你好，XXX：
   > 我可以帮你审内参、半月报，或者其他文字材料，直接发文字或docx给我就可以。另外请注意，涉及行内数据请务必脱敏哦。
   如果用户第一句发的是“你好”“在吗”或“我要审个材料”这类寒暄/说明文字，Bot 不会把它误记成名字，而是继续引导先报名字。

3. 之后该用户可正常使用审核功能
   已注册用户进入会话时，Bot 会提示：
   > 你好，需要我帮你审核什么呢？请直接发送 .docx 文档或直接发送文字,我会认真审核。

4. 如果用户先发待审核的 `.docx` 或正文，紧接着又补一句“帮我审一下”“帮我看看有无问题”这类催审短句，Bot 会把第二句当补充说明，不会再把它单独按文字材料审核。
   如果用户先发一句“帮我审一下这个材料”，后面才补发正文或 `.docx`，Bot 也不会先审核这句短话，而是提示继续把材料发过来。
   如果用户发的是“好的”“收到”“谢谢”“辛苦了”这类确认/感谢短句，Bot 会按普通对话简短回复，不会误触发文字审核。

本机预注册用户写入 `../M-Agent-Files/runtime/users/review_users.yaml`，格式示例：

```yaml
user-001: 测试用户
```

注意：如果用户第一次发的是 `.docx` 文件，Bot 也会先要求注册名字，**不会审核这个文件**。用户注册成功后需要再发一次。

该用户名表也是写作 Bot 使用的共享映射表，写作任务记录、会话记录和开发期对话日志会同步保存 `sender_name`。该文件包含企业微信用户 ID，位于 Git 仓库之外。

## 存档结构

```text
../M-Agent-Files/tasks/review/
└── 2026/07/20260713-001/        # 年/月/日期-序号
    ├── input/
    │   └── 汇报材料.docx         # 用户原始文件
    ├── output/
    │   ├── marked_汇报材料.docx  # 系统生成的标注文档
    │   └── report.md             # 审核意见
    ├── meta.json                 # 时间、用户、消息和审核摘要
    └── status.json               # 不含正文的处理/交付状态索引
```

审核报告写入成功后，`status.json` 的处理状态记为 `completed`。这只代表本机已生成报告；企业微信是否成功回传是独立的交付状态，尚未验证时保持 `unknown`。管理台统计同时兼容历史任务的 `meta.md`，不会再只统计新格式 `meta.json`。

## 测试

- `tests/test_reviewer.py`:覆盖 reviewer.py 核心逻辑
- `tests/test_review_main_flow_optimization.py`:覆盖内参主流程优化回归
- `tests/test_review_bot.py`:覆盖存档机制 + 配置加载
- `tests/test_review_halfmonthly.py`:覆盖半月报类型识别、时间范围、领导职务规范
- `tests/test_review_general.py`:覆盖通用审核类型识别、mock LLM 审核
- `tests/test_review_intake.py`:覆盖格式审核前后置触发、持久化恢复、超时清理、文件隔离和主文件确认
- `tests/test_review_multi_file.py`:覆盖附件缺失/重复/名称错配、双边证据校验、主文件选择和多文件结果合并
- `tests/test_error_marker.py`:覆盖通用审核 docx 批注和高亮
- 测试数量以仓库当前实际为准，不再在文档里写死总数

## 设计文档

- 两阶段拆分设计：`docs/superpowers/specs/2026-06-24-review-two-phase-design.md`
- 搜索增强方案：`docs/superpowers/specs/2026-06-29-review-search-enhancement-design.md`
