# M-Agent 业务能力索引

本目录描述当前业务能力的使用范围、输入输出和边界。具体业务规则以对应 `skills/<skill_id>/SKILL.md` 为唯一来源。

## 当前能力

| 能力 | Skill / 模块 | 说明 |
|---|---|---|
| 直报写作 | `skills/direct_report/` | 根据链接、文字或文件生成直报，并支持基于上一稿继续修改 |
| 简报写作 | `skills/writer1/` | 单一 Skill 自动处理单素材、多素材整合和弱关联拆分 |
| 材料润色 | `skills/rewrite/` | 对用户粘贴文字做保守润色并继续改稿 |
| 综合调研整合 | `skills/research_synthesis/` | 按现成提纲整合多部门 Word/PDF/PPTX 材料并输出 Word |
| 深银协动态 | `skills/shenyinxie_news/` | 按指定半月搜索、筛选微众银行正面报道并生成 Word |
| 内参周报 | `skills/internal_weekly/` | 搜集上一自然周公开信息，输出可追溯内容核对稿和溯源清单 |
| 材料审核 | `app/review/` | 独立入口支持文字、Word、HTML、PPTX、格式和多文件审核 |

详细说明：

- [直报写作](direct-report/README.md)
- [简报写作](brief-writing.md)
- [简报质量回归方法](brief-quality-regression.md)
- [材料润色](rewrite.md)
- [综合调研整合](research-synthesis.md)
- [深银协动态](shenyinxie-news.md)
- [内参周报](internal-weekly.md)
- [材料审核](review.md)

## 多任务交互

写作和材料润色共用底座任务关系层。同一用户可以保留多篇稿件，并用标题关键词、任务序号或“切换到……”定位；自然语言续改、补充/替换/参考新材料、沿用旧结构另写一份、取消任务和回答上一轮追问均先关联到具体任务，再进入对应 Skill。目标不唯一时系统只追问一个区分问题，用户回答后沿用原要求和材料继续。

直报和简报改稿已进入写作持久队列。不同用户按企业微信 `userid` 隔离；同一用户可同时排队多项任务，但后台同一时刻只执行其中一项。审核 Bot 继续保持独立入口，其“已入队后追加/切换审核类型”仍由审核待办单独治理，不把写作任务关系规则直接套入审核业务。

## Skill 标准结构

```text
skills/<skill_id>/
├── SKILL.md          # 业务规则唯一来源
├── config.yaml       # 注册、工具、输入输出和 workflow
├── schema.py         # Pydantic 结构化模型
├── workflow.py       # 执行流程
├── prompts/          # 模型提示词
└── assets/           # 经批准的静态模板或资源，可选
```

共享写作规划、质量检查或版本支持可以放在 `skills/` 顶层公共模块，但不能绕过底座工具授权。

## 每个文件写什么

### `SKILL.md`

必须写：

- 适用和不适用场景。
- 可接受的材料类型。
- 业务判断和执行步骤。
- 输出要求和业务口径。
- 禁止事项、材料不足和失败处理。
- 自检清单。

不要写企业微信 SDK、任务队列、权限配置或本机路径。

### `config.yaml`

必须写：

- `id`、`name`、`description`、`enabled`。
- `triggers`、`workflow`、`allowed_tools`。
- `inputs`、`outputs` 和 `supports_revision`。

它是底座注册配置，不承载长篇业务说明。

### `schema.py`

使用 Pydantic 定义输入输出和需要追问的结构。不要把业务 prompt 写进字段说明。

### `workflow.py`

负责组织材料、调用受限工具、模型和确定性校验。业务 prompt 优先放在 `prompts/`，工具必须通过 `ToolGateway` 获取。

## 能力文档和 Skill 的区别

- `docs/capabilities/` 面向项目维护者，解释用户可以怎么用、能力边界是什么、入口在哪里。
- `SKILL.md` 面向该能力的实现和模型，保存完整业务规则。
- 两者发生冲突时，以代码、测试和 `SKILL.md` 的当前实现为准，并立即修正文档。

## 完成标准

新增或修改 Skill 时至少满足：

1. 注册配置完整且只能调用声明工具。
2. 输入输出结构化，材料不足时有明确追问。
3. 具备针对业务边界的自动化测试。
4. 不读取任务目录外文件，不泄露本机和其他用户信息。
5. 更新对应 `SKILL.md`；只有用户能力范围变化时才更新本目录的能力文档。
6. 通过相关测试和核心文档检查。

当前路线和未完成工作统一查看 `docs/development/TODO.md`，不在本索引复制待办。
