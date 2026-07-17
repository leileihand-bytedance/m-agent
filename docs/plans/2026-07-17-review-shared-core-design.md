# 审核共享核心与规则包设计

## 目标

在不改变现有审核入口、任务队列和用户交付形式的前提下，统一各审核器重复维护的问题结构、模型调用、证据、去重和指标，并通过静态规则配置隔离通用规则与类型专属规则。

共享核心属于审核业务域，位于 `app/review/`，不进入 `app/platform/`。它不负责判断内参板块、半月报领导职务、PPT口径或公文格式。

## 当前问题

- 通用审核、半月报、格式检查和输出模块曾从内参审核器 `reviewer.py` 导入通用问题结构，依赖方向不清晰。
- 各审核器分别维护模型调用、结果解析、失败统计、原文证据和去重，修复容易分叉。
- 统一格式入口同时运行文字标点和目录规则，缺少按材料选择规则的静态配置。
- PPT采用页码和对象定位，多文件采用文件和段落双边证据，不能强行转换成Word段落模型。

## 目标结构

```text
app/review/
├── core/
│   ├── models.py          # 通用问题、证据及兼容 Finding/ReviewResult
│   ├── model_output.py    # 结构化模型结果解析
│   ├── model_runtime.py   # 模型调用、固定预算重试和阶段指标
│   ├── evidence.py        # 单点逐字证据与格式适配入口
│   ├── dedupe.py          # 候选去重原语
│   └── metrics.py         # 调用、失败、耗时和降级指标
├── rules/
│   ├── catalog.py         # 全部活动规则ID及适用元数据
│   └── profiles.py        # 各审核类型的静态规则组合
├── general_reviewer.py    # 通用审核流程和专属判断
├── reviewer.py            # 内参专属流程
├── halfmonthly_reviewer.py
├── ppt/
├── official_format_checker.py
└── multi_file_reviewer.py
```

## 规则矩阵

| 规则族 | 当前规则 | 分类 | 证据和定位 | 当前启用范围 |
|---|---|---|---|---|
| 引号配对 | `quote-pair`、`ppt-quote-pair` | 通用概念、媒介适配 | 单点逐字；段落或PPT对象 | 文字/Word/HTML、PPT |
| 连续标点 | `consecutive-punct`、`ppt-consecutive-punctuation` | 通用概念、媒介适配 | 单点逐字 | 文字/Word/HTML、PPT |
| 数字单位和中英标点 | `num-unit`、`mixed-punct` | 条件通用 | 中文正文段落 | 文字/Word/HTML/内参/半月报 |
| 目录 | `toc-no-ordinal`、`toc-seq-skip`、`toc-mismatch` | 结构条件或内参专属 | 目录段落及正文结构 | 当前Word审核；后续细分profile |
| 占位符 | `general-placeholder`、`ppt-placeholder` | 通用概念、媒介适配 | 单点逐字 | 通用材料、PPT |
| 错字、语病、标点 | `general-typo`、`general-grammar`、`general-punctuation`及PPT对应规则 | 条件通用 | 单点逐字并按材料复核 | 通用材料；PPT独立提示词 |
| 名称一致性 | `general-name-error`、`ppt-name` | 条件通用 | 通用材料单点/上下文；PPT双边证据 | 按材料启用 |
| 不完整和重复 | `general-incomplete`、`general-duplicate`、`content-incomplete`、`content-duplicate` | 结构条件 | 段落上下文或双段证据 | 通用、内参、半月报分别判断 |
| 日期和逻辑 | `general-invalid-date`、`general-date-range-logic`、`general-logic-inconsistency` | 条件通用 | 单段事实或通篇上下文 | 通用材料 |
| 通用文档结构 | 标题层级、附件引用和附件名称规则 | 通用审核专属 | 段落和附件清单 | 通用材料 |
| 内参结构 | 标题正文、目录正文、板块、范围和时效规则 | 内参专属 | 目录、标题和正文条目 | 内参 |
| 半月报结构 | 日期范围、五大板块顺序、职务、编号和正文格式 | 半月报专属 | 段落、编号和Word实际格式 | 半月报 |
| PPT一致性 | 页内语言、名称和跨页数据/内容一致性 | PPT专属 | 页码、对象和双边逐字证据 | PPT |
| 公文格式 | 页面、标题、各级标题和正文字体段落规则 | 公文格式专属 | Word实际格式属性 | 显式格式审核 |
| 多文件关系 | 附件缺失、重复、未引用、名称和跨文件逻辑 | 多文件专属 | 文件和段落双边证据 | 联合审核 |

`catalog.py` 是规则ID、规则族、适用层级、执行方式、证据和定位政策的权威索引。模型规则的完整判断文字仍由对应规则文档维护，确定性规则仍由代码实现；后续迁移不能把正则或业务提示复制进 profile。

## 兼容策略

- `Finding` 和 `ReviewResult` 移入共享核心，但从 `reviewer.py` 继续兼容导出，避免旧调用方一次性失效。
- `ReviewIssue + EvidenceRef` 是格式无关合同；现有Word、文字和HTML输出继续使用段落 `Finding`，后续由适配器逐类迁移。
- 纯文字、通用Word和HTML分别使用静态 profile；第一批启用规则与原来完全相同，不改变规则总数和提示词规则集合。
- 每次模型请求保留原有模型名、参数、超时和调用预算；共享运行层只统一计数、耗时和失败记录。
- 未迁移的审核器保留原流程。PPT不导入Word提示词，公文格式不引入模型调用。

## 回退

每个审核类型以独立 profile 和调用入口切换。发生有效问题、误报、定位、耗时或调用次数回归时，只回退该审核类型；其他审核器不受影响。首批迁移没有改变用户入口和任务数据结构，回退只涉及通用审核器的公共接口导入。
