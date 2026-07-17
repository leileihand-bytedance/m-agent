# 共享政策研究层设计

> 状态：已实施。本文件保留设计背景，当前行为以代码、测试和核心架构文档为准。

## 背景

当前 M-Agent 已有两条与政策相关的写作链路，但职责并未统一：

1. `direct_report` 通过 `skills/direct_report/policy_research.py` 先做“该不该挂政策”的判断，再调用 `policy_search` 选一条候选政策。
2. `writer1` / `writer2` 通过 `policy_materials` 直接补充政策材料，再交给写作模型处理。

这两条链路都能工作，但会带来三个现实问题：

1. 同一份素材在直报和简报里可能得到不同的政策结论。
2. “能不能挂”“挂哪条”“为什么挂”这些公共判断分散在不同 skill 里，新增材料能力时会继续复制。
3. 政策库当前更像“检索库”，还不是“可治理、可禁用、可复用的判断底座”。

本次设计目标是把“政策挂靠判断”抽成一层共享能力，第一阶段只服务 `direct_report`、`writer1`、`writer2`。

## 目标

第一阶段只做以下目标：

1. 建立一个共享政策研究层，统一回答：
   - 能不能挂政策
   - 挂哪条政策
   - 为什么挂这条
   - 应摘哪一句作为政策依据
   - 如有必要，可给 1-2 条备选
2. 让 `direct_report`、`writer1`、`writer2` 都通过这层能力进行政策挂靠判断。
3. 保持“写作指导仍留在 skill 内部”，共享层不负责“怎么写开头、怎么转微众、怎么收尾”。
4. 对政策库做最小必要升级，让它更适合作为共享判断层的底座，而不是先做大规模扩源或重平台化改造。

## 非目标

第一阶段不做以下事项：

1. 不把政策研究层做成面向企业微信用户的独立 skill。
2. 不把政策研究逻辑放进 `app/platform/` 的路由或 runtime 自动前置执行。
3. 不引入向量数据库、外部检索服务或完整语义检索基础设施。
4. 不一次覆盖所有政策主题，也不一口气扩展到人民银行、证监会、广东/深圳地方政策等全部来源。
5. 不在共享层输出写作句式、过渡句、结尾抬升建议。

## 方案比较

### 方案 A：继续增强 `policy_materials`

做法：把 `app/policy_knowledge/materials.py` 从“政策材料包”继续扩成“政策判断器”。

优点：

1. 改动最小。
2. 可直接复用现有主题识别和排序逻辑。

缺点：

1. `policy_materials` 当前定位是“补充材料”，不是“判断是否挂靠”。
2. 一旦把挂靠判断、候选筛选、profile 差异、结果结构都塞进去，这个文件会继续膨胀。
3. 后续如果要独立出“政策查询 skill”，边界仍然模糊。

结论：适合短期补丁，不适合第一阶段的共享层目标。

### 方案 B：新增共享政策研究层，保留现有政策知识库层

做法：

1. `app/policy_knowledge/` 继续负责抓取、入库、检索、材料包。
2. 新增 `app/policy_research/`，负责素材主题识别、挂靠判断、候选重排和结果输出。
3. 平台新增薄工具 `policy_research`，供写作 skill 调用。

优点：

1. 边界清晰。
2. 不把业务判断写进平台底座。
3. 直报和简报可以共用同一个引擎，但保留不同使用 profile。
4. 后续如果要做独立 skill，可直接复用共享层。

缺点：

1. 需要一次小型重构，把直报专用判断和简报通用材料逻辑收口。

结论：本次采用方案 B。

### 方案 C：一步到位做政策中台

做法：现在就补齐标签、规则、人工审核状态、后台治理、版本追踪、向量检索、更多来源。

优点：

1. 最终形态完整。

缺点：

1. 超出第一阶段范围。
2. 容易把工作重心从“让直报和简报先共享一套可靠判断”转移到基础设施建设。

结论：作为第二阶段方向保留，不纳入本次设计。

## 设计原则

1. **共享层只做判断，不做写作。**
2. **底座不承载业务规则。** 业务判断放在共享研究层，不放进 `app/platform/runtime.py` 或 router。
3. **不同 skill 共用同一引擎，但允许使用不同 profile。**
4. **优先用本地政策库，不依赖即时联网搜索。**
5. **宁可不给，也不硬挂。** 当没有足够贴切的政策时，明确返回“不建议挂靠”。
6. **先治理小库，再谈大扩源。** 第一阶段优先提高库的可判定性和可维护性。

## 总体架构

第一阶段建议拆成四层：

```text
app/policy_knowledge/
  -> 抓取、入库、检索、材料包

app/policy_research/
  -> 共享政策判断层

app/platform/builtin_tools.py
  -> 暴露 policy_research 工具

skills/direct_report / writer1 / writer2
  -> 决定何时调用、如何使用判断结果
```

### 1. `app/policy_knowledge/` 继续负责什么

保留现有职责：

1. 金融监管总局 / 国务院政策抓取。
2. SQLite 存储。
3. 底层关键词检索。
4. `policy_materials` 材料包构造。

本层不负责：

1. 根据不同写作场景判断“该不该挂”。
2. 面向不同 skill 输出不同使用策略。
3. 决定主推荐和备选的业务解释口径。

### 2. 新增 `app/policy_research/`

建议新增以下文件：

```text
app/policy_research/
├── __init__.py
├── models.py       # 输入输出模型
├── profiles.py     # direct_report / brief 的差异化规则
└── service.py      # 统一入口和判断流程
```

职责：

1. 从用户指令和素材中识别主题。
2. 根据 `usage_profile` 判断该素材是否适合挂政策。
3. 从本地政策库获取候选。
4. 基于主题、来源、分类、素材类型、禁用状态等规则筛选与排序。
5. 输出主推荐、备选和拒绝原因。

不负责：

1. 输出写作句式。
2. 调整标题、开头、结尾等文风。
3. 直接修改各 skill 的 `planning_note`。

### 3. 平台工具层

在 `app/platform/builtin_tools.py` 中新增一个薄包装：

```python
def policy_research(
    *,
    user_instruction: str,
    materials: list[object],
    db_path: str | Path,
    usage_profile: str,
    limit: int = 3,
) -> dict[str, object]:
    ...
```

它只负责：

1. 调用共享研究服务。
2. 把结果转成 dict 返回给 skill。

它不负责：

1. 内联任何直报/简报专有规则。
2. 在平台层做自动前置调用。

### 4. skill 使用层

#### `direct_report`

继续保持保守策略：

1. 对案件、活动、获奖、直播、判决等素材，优先返回“不建议挂靠”。
2. 倾向只取一条主推荐政策。
3. 只有结论明确时才把政策材料注入模型上下文。

#### `writer1` / `writer2`

采用更宽的简报 profile：

1. 允许返回 1 条主推荐加 1-2 条备选。
2. 对宏观政策背景接受度高于直报。
3. 仍然不能把弱相关政策塞进上下文。

## 共享研究层输入输出

### 输入

建议使用 Pydantic 模型定义：

```python
class PolicyResearchRequest(BaseModel):
    user_instruction: str
    materials: list[dict[str, object]]
    usage_profile: Literal["direct_report", "brief"]
    limit: int = 3
```

### 输出

```python
class PolicyCandidate(BaseModel):
    title: str
    source: str
    category: str
    publish_date: str
    url: str
    snippet: str
    matched_terms: list[str]
    relevance_score: int
    selection_reason: str


class PolicyResearchResult(BaseModel):
    should_attach_policy: bool
    decision_reason: str
    matched_themes: list[str]
    retrieval_query: str
    confidence: float
    primary_policy: PolicyCandidate | None = None
    alternative_policies: list[PolicyCandidate] = []
```

### 结果解释

共享层只输出判断结果：

1. `should_attach_policy`
   - `true`：建议挂政策
   - `false`：不建议挂政策
2. `decision_reason`
   - 例如：`unsupported_material_type`、`no_qualified_policy`、`qualified_local_policy`
3. `matched_themes`
   - 例如：`["小微企业金融服务"]`
4. `retrieval_query`
   - 实际用于检索的查询词，方便调试
5. `primary_policy`
   - 主推荐
6. `alternative_policies`
   - 备选

共享层明确不返回：

1. “开头怎么写”
2. “怎么自然转入微众”
3. “结尾往哪个政策方向抬”

这些继续由各自 skill 的 `planning_note` 与 prompt 负责。

## 判断流程

建议流程如下：

```text
用户指令 + 原始素材
  -> 识别素材主题
  -> 判断素材类型是否适合挂政策
  -> 根据 usage_profile 确定筛选强度
  -> 从政策库检索候选
  -> 过滤禁用政策、弱相关政策、错误来源政策
  -> 生成主推荐和备选
  -> 返回结构化判断结果
```

### 第一步：主题识别

复用并上收现有 `policy_materials` 中可复用的主题识别能力，但输出应服务于判断层，而不是直接输出材料包。

第一阶段建议覆盖这些主题：

1. 小微企业金融服务
2. 普惠金融
3. 科技创新 / 科技金融
4. 稳外贸
5. 消费促进
6. 数据要素 / 跨境数据
7. 绿色金融
8. 风险防控 / 消费者保护

### 第二步：素材类型判断

这一层是第一阶段最关键的补足点。

建议在共享层显式判断素材是否属于以下类型：

1. `event_activity`
2. `award_or_recognition`
3. `lawsuit_or_case`
4. `product_or_service`
5. `mechanism_or_platform`
6. `comprehensive_progress`

`direct_report` profile 中，前 3 类默认更倾向拒挂。

### 第三步：候选检索

继续使用本地 SQLite 库，不引入新基础设施。

检索顺序：

1. 先按主题生成短查询词。
2. 优先检索 `policy_original`。
3. 当原文不足时，再补充 `policy_interpretation`。
4. `regulatory_update` 仅作补位，不作默认主推荐。

### 第四步：候选过滤与重排

共享层排序不应只依赖词频，应加入以下规则：

1. `is_enabled = 1` 才可参与排序。
2. `policy_original` 优先于 `policy_interpretation`。
3. `direct_report` profile 中，弱相关宏观文件要更严格降权。
4. 已标记为噪音或人工禁用的政策不得入选。
5. 对主题不符但关键词偶然命中的政策必须剔除。

### 第五步：主推荐与备选

输出策略：

1. `direct_report`
   - 最多 1 条主推荐 + 1 条备选
2. `brief`
   - 最多 1 条主推荐 + 2 条备选

如果候选不足或都不合格：

1. `should_attach_policy = false`
2. `primary_policy = null`
3. `alternative_policies = []`

## skill 接入方式

### `direct_report`

改造方向：

1. 现有 `skills/direct_report/policy_research.py` 下沉或迁移到共享层。
2. `workflow.py` 不再直接写主题与候选规则，而是调用 `policy_research` 工具。
3. 写作规划只读取共享层输出结果：
   - 是否挂政策
   - 主推荐标题
   - 政策摘录
   - 拒绝原因

### `writer1` / `writer2`

改造方向：

1. 不再默认直接把 `policy_materials` 的结果当成唯一政策输入。
2. 先做 `policy_research`。
3. 若研究层建议挂政策，再决定：
   - 仅注入主推荐
   - 或主推荐 + 部分备选
4. `policy_materials` 后续保留为“材料包工具”，用于兼容旧逻辑或特定 fallback，不再承担主判断职责。

## 政策库优化

第一阶段不做大扩源，但必须做三类升级。

### 1. 增加结构化标签字段

当前 `policy_documents` 主要保存：

1. `source`
2. `category`
3. `title`
4. `publish_date`
5. `url`
6. `text`

第一阶段建议增加：

1. `theme_tags_json`
2. `region_tags_json`
3. `audience_tags_json`
4. `source_weight`
5. `is_enabled`
6. `disabled_reason`
7. `review_note`

作用：

1. 让共享层不再完全依赖即席关键词。
2. 为后续后台管理和人工清噪预留结构。

### 2. 增加人工禁用能力

政策库当前文档已明确存在“需要清理噪音政策”的需求，但数据库层还没有显式禁用状态。

第一阶段建议支持：

1. 默认只检索 `is_enabled = 1` 的政策。
2. 允许通过 CLI 或后续后台把某条记录禁用。
3. 被禁用记录保留，不物理删除。

这比直接删数据更稳，便于回滚和复核。

### 3. 把排序从“命中”提升到“适配”

当前 `store.search()` 和 `build_policy_materials()` 的逻辑，仍以关键词命中和来源加权为主。

第一阶段建议把排序拆成两步：

1. **检索层**：返回主题可能相关的候选。
2. **研究层**：按素材类型、profile、标签、来源、分类和禁用状态做业务重排。

这样可以保持政策库层稳定，而把真正的业务判断集中在共享研究层。

## 政策库扩源策略

第一阶段原则：

1. 不急着大规模扩源。
2. 先把现有 `nfra` 和 `govcn` 两类来源用好。
3. 先把库内噪音、禁用、标签、排序做好。

第二阶段再评估：

1. 人民银行
2. 证监会
3. 广东 / 深圳地方政策
4. 向量检索

理由：

1. 现阶段的主要瓶颈是“判断层不统一”，不是“完全无库可用”。
2. 继续扩源会先放大噪音和治理成本。

## 错误处理

共享研究层需明确以下失败路径：

1. `blank_materials`
   - 没有有效素材
2. `unsupported_theme`
   - 没识别出政策主题
3. `unsupported_material_type`
   - 素材类型不适合挂政策
4. `no_qualified_policy`
   - 检索到了候选，但没有符合条件的政策
5. `policy_db_unavailable`
   - 本地政策库不可用

对 skill 的要求：

1. 不因政策研究失败而整体写作失败。
2. 政策层返回“不建议挂靠”时，写作层应继续生成不挂政策版本。

## 测试策略

第一阶段新增或调整以下测试：

### 1. 共享研究层测试

新增：

1. `tests/test_policy_research_service.py`
2. `tests/test_policy_research_profiles.py`

覆盖：

1. 该挂时返回主推荐
2. 不该挂时返回明确拒绝原因
3. `direct_report` 与 `brief` profile 输出不同
4. 人工禁用政策不会入选
5. 同主题多候选时主推荐排序稳定

### 2. policy 库层测试

扩展：

1. `tests/test_policy_knowledge_store.py`
2. `tests/test_policy_knowledge_materials.py`

覆盖：

1. 新字段默认值
2. 禁用状态过滤
3. 标签字段读写
4. 查询结果不返回 `is_enabled = 0` 记录

### 3. skill 接入测试

扩展：

1. `tests/test_direct_report_workflow.py`
2. `tests/test_brief_writer_workflows.py`

覆盖：

1. 直报在产品/机制稿中可挂政策
2. 直报在活动/案件/获奖稿中默认拒挂
3. 简报能接收主推荐或备选
4. 政策研究失败时 skill 仍能继续写作

## 实施分期

### Phase 1：共享判断层最小闭环

1. 新增 `app/policy_research/`
2. 新增 `policy_research` 工具
3. 直报接入共享层
4. 简报接入共享层
5. 政策库补标签和禁用状态字段

### Phase 2：治理和可观测性

1. 增加 CLI 的禁用/启用能力
2. 增加更新统计和数据源状态
3. 增加后台管理页

### Phase 3：扩源和检索增强

1. 增加更多来源
2. 评估语义检索
3. 评估独立政策查询 skill

## 验收标准

第一阶段完成时，应满足：

1. `direct_report`、`writer1`、`writer2` 都通过共享研究层做政策挂靠判断。
2. 共享层只返回“能不能挂、挂哪条、为什么、摘哪一句、备选”，不返回写作指导。
3. 直报与简报可共享一套判断引擎，但通过 profile 保持差异化。
4. 政策库已具备标签字段和禁用状态字段。
5. 自动化测试覆盖共享研究层、政策库升级和 skill 接入。

## 结论

第一阶段最合适的路线是：

1. 新增 `app/policy_research/` 作为共享政策判断层。
2. 保留 `app/policy_knowledge/` 作为抓取和检索底座。
3. 让直报和简报通过 `policy_research` 工具共享判断结果。
4. 先升级政策库的标签和禁用能力，不急着大规模扩源。

这条路线最符合当前项目的目录边界、安全约束和近期业务价值，也能为第二阶段的政策治理和独立查询能力留出清晰扩展点。
