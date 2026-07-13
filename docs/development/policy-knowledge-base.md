# 政策知识库 v1

本文档说明 M-Agent 本地政策知识库和共享政策研究层的当前实现。它用于给直报、简报等写作 skill 提供可靠的监管政策背景，降低通用联网搜索的不稳定性。`wiki` 写作链路已下线，当前以本地 SQLite 政策库和共享 `policy_research` 判断层为主。

## 当前范围

第一版已经接入两个官方来源：

1. 国家金融监督管理总局官网。
2. 中国政府网/国务院政策文件库。

### 金融监管总局

| 栏目 | itemId | 用途 |
| --- | --- | --- |
| 政策解读 | `917` | 用于发现政策原文链接，并保留答记者问、政策问答等解释材料 |
| 监管动态 | `915` | 会议、部署、调研、公告、监管指标等动态信息 |

默认抓取最近约 3 个月数据。

### 国务院政策文件库

国务院数据只抓正式政策原文，不抓普通新闻稿。当前按主题词抓取，不做全量政策库。

说明：中国政府网页面偶尔会被解析成导航栏文本。当前实现有两层兜底：

- 新抓取时，如果正文疑似只有导航栏，改用政策文件库搜索接口返回的政策摘要。
- 检索已有 SQLite 数据时，如果发现旧数据正文是导航栏，也会用 metadata 中的 `summary` 替代。

重点范围：

| 方向 | 主题词示例 |
| --- | --- |
| 宏观经济 | 宏观经济、扩大内需、稳增长、优化营商环境 |
| 促进消费 | 促进消费、扩大消费、服务消费、消费品以旧换新 |
| 实体经济 | 实体经济、民营经济、中小企业、制造业 |
| 战略性新兴产业 | 战略性新兴产业、新兴产业、科技创新、新质生产力 |
| 未来产业 | 未来产业、人工智能、低空经济、量子科技 |

更新频率：

```text
每周一更
```

原因：政策文件不是高频实时信息，每周更新足够覆盖写直报、简报所需的政策背景，同时可以减少抓取压力和本地库噪声。

核心原则：

```text
政策原文 > 政策解读 > 监管动态
```

写直报、简报时应优先使用政策原文中的正式监管要求。政策解读只作为理解背景、监管口径和重点说明的辅助材料。

## 数据流

```text
金融监管总局官方接口
  -> app.policy_knowledge.nfra
  -> 识别并补抓政策原文全文

国务院政策文件库
  -> app.policy_knowledge.govcn
  -> 按重点主题词抓取政策原文全文

两类来源
  -> app.policy_knowledge.store
  -> ../M-Agent-Files/knowledge/policy/policies.sqlite3
  -> platform 工具 policy_research / policy_materials / policy_search
  -> direct_report / writer1 / writer2
```

## 本地更新

```bash
python -m app.policy_knowledge.cli update
```

这是建议的每周更新入口，默认执行：

- 金融监管总局最近约 3 个月数据。
- 国务院重点主题政策原文。
- 金融监管总局每个栏目最多 8 页。
- 国务院每个主题词最多 1 页、每页 10 条。

统一更新命令会分别尝试两个来源。单个来源临时失败时，不会中断整次更新；命令会打印警告，并保留本地已有政策库继续可用。生产测试前应确认本机 DNS/网络能解析：

- `www.nfra.gov.cn`
- `sousuo.www.gov.cn`

如需分别调试数据源，可单独运行：

```bash
python -m app.policy_knowledge.cli update-nfra --days 92 --max-pages 8
python -m app.policy_knowledge.cli update-govcn --max-pages 1 --page-size 10
```

说明：

- `--days 92`：约等于最近 3 个月。
- `update-nfra --max-pages 8`：金融监管总局每个栏目最多翻 8 页，避免一次抓取过大。
- `update-govcn --max-pages 1`：国务院每个主题词最多抓 1 页。国务院主题词较多，默认先控制数据量。
- 重复文章会按 `source + doc_id` 去重。
- 如果同一篇文章重复出现，优先级为：政策原文 > 政策解读 > 监管动态。
- 如果政策解读文末带有原文链接，会继续抓取 `governmentDetail.html` 对应的正式政策原文。

## 本地检索

```bash
python -m app.policy_knowledge.cli search "小微企业金融服务" --limit 3
python -m app.policy_knowledge.cli search "小微企业金融服务" --limit 3 --category policy_original
python -m app.policy_knowledge.cli search "人工智能 银行业保险业" --limit 3
python -m app.policy_knowledge.cli search "促进消费 实体经济" --limit 3
python -m app.policy_knowledge.cli search "未来产业 人工智能" --limit 3
```

平台内置工具名：

```text
policy_research
policy_materials
policy_search
```

`policy_research` 是第一阶段统一政策挂靠判断入口，当前给 `direct_report`、`writer1`、`writer2` 使用。它只回答四件事：

- 能不能挂
- 挂哪条政策
- 为什么挂这条
- 摘哪一句作依据

如有必要，它还会返回 1-2 条备选政策。写作怎么开头、怎么自然转入微众、怎么结尾抬升，仍由各自 skill 决定。

`policy_materials` 是 SQLite 政策库材料包入口，当前主要给 `writer1` / `writer2` 使用。它会：

- 根据用户材料识别政策主题。
- 构造短检索词，而不是直接用全文搜索。
- 优先检索政策原文。
- 对候选政策做相关性打分和低相关过滤。
- 最多返回 2-3 条政策材料，每条包含“相关性说明 + 政策摘录”。

`policy_search` 是底层检索工具，适合调试或特殊 workflow 使用。

返回字段包括：

- `title`
- `publish_date`
- `url`
- `snippet`
- `source`
- `category`
- `doc_id`

常见 `category`：

- `policy_original`：政策原文，写作优先使用。
- `policy_interpretation`：政策解读、答记者问、政策问答。
- `regulatory_update`：监管动态、会议、部署、公告等。

## Skill 使用规则

写作类 skill 当前使用规则：

```text
direct_report：
用户素材
  -> 共享 policy_research（direct_report profile）
  -> 命中时只补 1 条主政策材料

writer1 / writer2：
用户素材
  -> bank_materials
  -> 共享 policy_research（brief profile）
  -> 命中时补 1 条主政策 + 最多 1-2 条备选
```

其中：

1. `direct_report`、`writer1`、`writer2` 现在都优先走共享 `policy_research`，统一回答“能不能挂、挂哪条、为什么、摘哪一句”。
2. `direct_report` 仍然更保守：案件、活动、获奖、直播、判决等时间节点稿件会被 profile 直接挡掉，默认直入主题。
3. `writer1` / `writer2` 的 profile 更宽，会在命中时返回主推荐和备选，但仍不把弱相关政策塞进上下文。
4. `policy_materials` 仍保留，作为兼容材料包工具和低层调试入口。
5. 如果本地政策库里没有贴切政策，系统默认不挂，不为了正式感硬补政策。

当前已授权使用 `policy_research` / `policy_search` 的 skill：

- `direct_report`
- `writer1`
- `writer2`

`direct_report` 当前执行顺序是：

```text
用户要求 + 素材标题 + 素材正文前段
  -> 共享 policy_research 判断这篇稿子是否适合挂政策
  -> 共享层用本地政策库筛政策原文并做相关性过滤
  -> 只有命中贴切政策时才补 1 条政策材料
```

这保证直报优先使用贴切政策原文，并尽量避免把弱相关政策塞进模型上下文。

当前调优原则是：

- 小微金融：允许使用像“银税互动”这类与小微融资支持直接相关的金融政策。
- 科技创新：如果本地库里只有区域批复、园区升级、泛科技治理类文件，而没有真正贴合“科技金融/服务科技企业”的政策，就宁可不挂，直接入题。

检索层还对“消费”类泛词做了基础保护：当查询包含 `服务消费`、`促进消费`、`扩大消费`、`消费品以旧换新` 等更具体词时，不再用单独的 `消费` 打分，避免金融消费者保护类文件误排到消费政策前面。

## 政策库治理字段

为支持共享判断层，SQLite 主库已新增最小治理字段：

- `is_enabled`：是否启用。禁用后，检索和共享判断默认跳过该条政策。
- `disabled_reason`：禁用原因，便于回看为什么被下线。
- `theme_tags_json`：主题标签，如 `small_micro`、`consumption`。
- `region_tags_json`：地域标签，如 `national`、`shenzhen`。
- `audience_tags_json`：适用对象标签，如 `banks`、`small_micro_enterprises`。
- `source_weight`：人工调节权重，处理同类政策排序时可小幅前置高价值条目。
- `review_note`：人工备注，记录使用边界或适用提醒。

## 数据清理和维护规则

当前政策知识库只保留一层主数据：

```text
../M-Agent-Files/knowledge/policy/policies.sqlite3
```

清理政策噪音时，以 SQLite 数据为准。

下一步规划在本机管理后台增加可视化政策库管理页面，基本流程为：

```text
查看政策列表
  -> 按来源、分类、关键词筛选
  -> 勾选无关政策
  -> 删除前预览
  -> 自动备份 SQLite
  -> 删除底层记录
```

删除保护规则：

- 删除前必须自动备份 SQLite。
- 删除操作只允许按 `source + doc_id` 定位，不开放任意 SQL。
- 删除后展示删除前数量、删除数量、删除后数量和备份路径。

## 后续增强

1. 增加每周一次的自动更新任务，建议固定在周一运行 `python -m app.policy_knowledge.cli update`。
2. 增加管理后台里的政策库数据管理页，支持查看、筛选、勾选删除和自动备份。
3. 增加管理后台里的“更新政策库 / 查看数据源 / 开关政策库”。
4. 增加更细的标签：小微、普惠、数字金融、科技金融、人工智能、消保、风险防控、广东/深圳等。
5. 增加语义检索或向量检索，解决关键词不完全匹配的问题。
6. 后续再评估是否扩展到人民银行、证监会、广东/深圳监管部门等来源。

## 国务院政策接入说明

国务院政策文件适合作为写作中的“上位政策依据”，尤其适合直报和简报开头部分挂接宏观背景、中央部署和产业政策方向。

来源优先级：

```text
国务院政策文件库/中国政府网政策栏目
  > 国务院/国务院办公厅政策原文
```

接入原则：

- 优先抓政策原文，不把新闻稿当作政策原文。
- 只收录与 M-Agent 写作场景相关的经济、产业、消费、实体经济政策。
- 暂不做全量国务院政策库，避免库太大、噪声太多。
