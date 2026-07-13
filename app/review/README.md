# app/review - 旧审核 Bot

> 当前继续独立运行，后续包装为 `skills/review/`。迁移前不要大改。

## 这是什么

`app/review/` 是 M-Agent 的旧审核企业微信 Bot，支持三种文档类型：

1. **内参周报** (`微众银行信息内参周报...`)：按 `app/data/rules.md` 审核。
2. **半月报** (`信息动态半月报...`)：按 `app/review/rules_halfmonthly.md` 审核。
3. **通用审核** (其他 `.docx`)：按 `app/review/rules_general.md` 审核文字质量。

Bot 会根据文件名和文档头自动识别文档类型，然后分发到对应审核引擎。

## 架构定位

```text
当前：

企业微信审核 Bot
  -> app/review/main.py
  -> app/review/document_type.py    (识别内参/半月报)
  -> 内参: app/review/parser.py + format_checker.py + reviewer.py
  -> 半月报: app/review/parser.py + format_checker.py + halfmonthly_reviewer.py
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
python -m pip install -r app/requirements.txt
```

### 2. 配置凭证

```bash
cp app/review/config.example.env .env
# 编辑 .env,填入 WECOM_REVIEW_BOT_ID、WECOM_REVIEW_BOT_SECRET
# 如需让审核模块单独走 DeepSeek,再填 REVIEW_ANTHROPIC_* 和 REVIEW_MODEL_NAME
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
pytest tests/test_review_main_flow_optimization.py -q

# Bot 存档 + 配置测试
python tests/test_review_bot.py

# 半月报测试
pytest tests/test_review_halfmonthly.py -q

# 通用审核测试
pytest tests/test_review_general.py -q
pytest tests/test_review_general_rules.py -q
```

## 文档类型识别

Bot 根据文件名和文档头前 5 段自动识别：

| 文件名/标题含 | 识别为 |
|--------------|--------|
| "半月报" | 半月报 |
| "内参" 或 "周报" | 内参周报 |
| 其他 | 通用审核 |

识别后分别走不同审核引擎和规则库。

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
pytest tests/test_error_marker.py tests/test_review_general.py tests/test_review_general_rules.py tests/test_review_term_library.py tests/test_review_bot.py tests/test_review_main_flow_optimization.py tests/test_bot_logging.py tests/test_notification.py tests/test_user_registry.py -v
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

Bot 使用 Python `logging` 模块,支持按月切分和按用户分文件.

```text
../M-Agent-Files/runtime/logs/
├── review-bot-2026-07.log          # 公共日志(所有请求 + SDK 日志)
└── users/
    └── user-001/
        └── 2026-07.log             # 该用户的操作日志
```

配置项:

- `M_AGENT_LOGS_DIR`: 特殊部署时覆盖日志目录；默认由 `M_AGENT_DATA_DIR` 派生
- 每条日志都带 `user=<english_name>|userid=<userid>` 字段
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
- 附件回传失败时，审核 Bot 会告诉用户：审核已完成，但文件发送失败，并说明已提醒管理员处理。
- 同一种异常类型仍保留冷却机制，避免运维 Bot 刷屏。

## 企业微信附件发送稳定性

审核 Bot 返回 `marked_原文件名.docx` 时会先通过企业微信长连接上传临时素材，再发送文件消息。

已知行为：

- 当前依赖的 `wecom-aibot-sdk==1.0.7` 内部默认只等待 `5` 秒回复回执。
- 大文件分片上传时，企业微信回执可能超过 `5` 秒才返回；此时日志会出现 `Reply ack timeout`，随后又出现 `Received unknown frame`。
- 这种情况通常表示审核已经完成、标注文档也已生成，但附件发送阶段被 SDK 提前判定失败。

配置项（在 `.env` 中）：

```bash
REVIEW_REPLY_ACK_TIMEOUT_SECONDS=30
```

说明：

- 审核 Bot 启动时会覆盖 SDK 内部回执等待时间，默认 `30` 秒。
- 如果后续仍出现大文件上传回执超时，可先查看 `../M-Agent-Files/runtime/logs/review-bot-YYYY-MM.log` 中实际回执延迟，再谨慎调大该值。
- 该配置只影响审核 Bot 的企业微信回复/上传等待，不改变审核模型、规则或写作 Bot。

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
    └── meta.json                 # 时间、用户、消息和审核摘要
```

## 测试

- `tests/test_reviewer.py`:覆盖 reviewer.py 核心逻辑
- `tests/test_review_main_flow_optimization.py`:覆盖内参主流程优化回归
- `tests/test_review_bot.py`:覆盖存档机制 + 配置加载
- `tests/test_review_halfmonthly.py`:覆盖半月报类型识别、时间范围、领导职务规范
- `tests/test_review_general.py`:覆盖通用审核类型识别、mock LLM 审核
- `tests/test_error_marker.py`:覆盖通用审核 docx 批注和高亮
- 测试数量以仓库当前实际为准，不再在文档里写死总数

## 设计文档

- 两阶段拆分设计：`docs/superpowers/specs/2026-06-24-review-two-phase-design.md`
- 搜索增强方案：`docs/superpowers/specs/2026-06-29-review-search-enhancement-design.md`
