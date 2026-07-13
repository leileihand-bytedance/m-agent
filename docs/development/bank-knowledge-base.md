# 微众银行信息库

## 定位

微众银行信息库用于给直报、简报等写作 skill 补充“微众银行自身背景、产品、能力、数据和标准表述”。

它和政策知识库分工不同：

```text
微众银行信息库：回答“微众银行是谁、做了什么、有哪些数据和案例”
政策知识库：回答“外部监管、国务院等政策依据是什么”
```

写作时推荐顺序：

```text
用户素材
  -> bank_materials
  -> policy_materials
  -> 必要时联网搜索
  -> llm_writer
```

这样可以先保证材料里有微众自身事实，再用外部政策做背景支撑。

## 当前数据

当前已导入桌面“微众银行信息库”文件夹中的 5 个文件：

```text
深圳前海微众银行简介（简）2025.doc
微众银行数字化企业金融服务情况介绍.docx
2025年报.pdf
深圳前海微众银行简介20251231.docx
2025ESG报告.pdf
```

导入后生成：

```text
data/bank_knowledge/bank.sqlite3
```

该数据库包含内部或敏感材料，已加入 `.gitignore`，不要提交。

## 当前能力

代码位置：

```text
app/bank_knowledge/
├── cli.py        # 导入和检索命令
├── ingest.py     # Word/PDF/TXT 解析和条目切分
├── materials.py  # 给写作 skill 用的材料包
└── store.py      # SQLite 存储和关键词检索
```

平台工具：

```text
bank_materials
bank_search
```

其中 `bank_materials` 是写作 skill 推荐使用的主入口。它会：

- 从用户要求和用户素材中识别主题。
- 检索少量高相关微众素材。
- 输出“相关性说明 + 来源文件 + 微众银行素材摘录”。
- 保留来源文件和页码，便于后续核对口径。

`bank_search` 是底层检索工具，适合调试或特殊 workflow 使用。

## 当前主题标签

已支持的主题包括：

- `profile`：微众银行基础介绍和标准表述
- `small_micro`：小微企业金融服务
- `inclusive_finance`：普惠金融
- `digital_finance`：数字金融、数字银行、金融科技
- `tech_finance`：科技金融、科创企业
- `ai_finance`：人工智能、大模型、智能体
- `foreign_trade`：微贸贷、稳外贸
- `consumption`：国补商户、促消费、以旧换新
- `consumer_protection`：消费者权益保护
- `anti_fraud`：反诈和账户风险
- `accessibility`：适老和无障碍服务
- `green_finance`：绿色金融
- `esg`：ESG 和可持续发展
- `financial_metric`：经营指标和规模数据
- `honor`：荣誉、案例、排名

## 常用命令

导入一个本地文件夹：

```bash
python -m app.bank_knowledge.cli import-folder "/path/to/authorized-bank-knowledge"
```

检索小微企业/微业贷：

```bash
python -m app.bank_knowledge.cli search "小微企业 微业贷 普惠金融" --limit 5
```

检索人工智能：

```bash
python -m app.bank_knowledge.cli search "人工智能 大模型 智能体 数字员工" --limit 5
```

检索国补商户/促消费：

```bash
python -m app.bank_knowledge.cli search "国补商户 促消费 以旧换新" --limit 5
```

如果本机 Python 缺少 PDF 解析库，请先按 `app/requirements.txt` 安装项目依赖，再执行导入。不要把个人运行时路径写入项目文档。

```bash
python -m app.bank_knowledge.cli import-folder "/path/to/authorized-bank-knowledge"
```

## Skill 接入状态

已授权并接入：

```text
skills/direct_report/
skills/writer1/
skills/writer2/
```

当前 workflow 顺序：

```text
读取用户链接或用户粘贴素材
  -> bank_materials
  -> policy_materials
  -> llm_writer
```

## 口径管理

信息库里可能存在不同文件、不同时间点的数据口径不一致。

已发现示例：

```text
国补商户贷款金额：
2025年报.pdf：累计为全国超5000家国补商户发放贷款超260亿元
微众银行数字化企业金融服务情况介绍.docx：累计发放贷款超过400亿元
```

处理原则：

1. 写作优先采用正式公开报告、年报、ESG 报告中的口径。
2. 介绍稿、情况报告可作为补充，但不能和正式报告口径混写。
3. 输出材料包必须保留来源文件和页码。
4. 后续可增加“来源优先级”和“口径冲突提示”机制。

## 后续增强

1. 增加来源优先级：年报/ESG > 正式简介 > 情况介绍稿。
2. 增加口径冲突检测：同一主题出现多个金额或数量时提示模型谨慎使用。
3. 增加管理后台开关和重新导入按钮。
4. 增加语义检索或向量检索，解决关键词不一致的问题。
5. 增加更精细的“标准表述库”，专门沉淀可直接复用的官方表达。
