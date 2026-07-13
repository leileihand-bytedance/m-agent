# 停滞模块归档 - 2026-07-04

本目录保存 2026-07-04 从主线目录移出的停滞模块和历史材料。

## 归档范围

```text
app/agent/        # 早期统一 agent / 领导风格沉淀实验代码
app/main.py       # 早期领导风格沉淀 Bot 入口
app/config.py     # 早期领导映射配置
app/prompts/      # 早期 prompt
app/data/leaders/ # 早期领导风格材料
data/leaders/     # 早期领导风格档案
data/leader-mapping.json
scripts/diagnostic_review.py
docs/superpowers/plans/2026-05-26-*.md
docs/superpowers/specs/2026-05-26-*.md
```

## 当前结论

这些内容不属于当前 M-Agent 主线，不作为新开发入口。

当前主线是：

```text
app/platform/
app/admin/
app/writing/
app/review/
app/policy_knowledge/
app/bank_knowledge/
skills/
```

## 使用规则

- 不要从当前运行代码直接 import 本目录下的代码。
- 不要在本目录继续新增业务能力。
- 如需复用思路，先复制设计思路到当前主线文档，再在 `app/platform/` 或 `skills/` 中重新实现。
- 本目录中的 `data/` 和 `app/data/` 可能包含历史材料，不应提交到公共仓库。
