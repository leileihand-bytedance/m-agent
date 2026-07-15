# 本地状态报告机制

`STATUS-REPORT.md` 是本机开发日志，用来快速回看“做了什么功能、能力发生了什么变化、实际作用是什么、还剩什么边界和下一步”。它不是 Git 操作流水账，也不是 Git 中的项目事实来源，不能替代架构、TODO、模块 README 和 skill 文档。

## 记录内容

每个完成并验证的逻辑开发节点只记录一条，正文至少包括：

1. 完成功能：本次开发完成了什么或解决了什么问题。
2. 能力变化：对系统能力、业务流程或用户体验产生了什么实际作用。
3. 当前边界/下一步：哪些尚未接入、仍有什么风险，以及下一步做什么。
4. 关键验证：必要时写明自动化测试、真实链路或故障测试结论。

文件列表、文件数量、提交摘要和推送范围不能作为日志主体。Git 哈希只保留一行用于技术追溯。

## 事实来源优先级

1. 当前代码和自动化测试。
2. `docs/development/architecture.md`、模块 README、skill 文档。
3. `docs/development/TODO.md` 中的当前路线和状态。
4. Git 提交历史。
5. 本机 `STATUS-REPORT.md`，仅用于快速回看本机开发过程。

## 自动机制

首次克隆后运行：

```bash
uv run --locked python scripts/project_docs.py install-hooks
```

- pre-commit：执行 `uv run --locked python scripts/project_docs.py check --staged`，读取暂存区版本，按模块检查 TODO、对应核心文档、本机权限文件和本机绝对路径。无关计划文档不能放过行为变更。
- post-commit：只告警当前分支是否还有未推送提交，不写状态报告，避免同一开发节点产生重复日志。
- pre-push：推送前再次运行核心文档检查，并拒绝直接 `git push`。
- 受管推送：统一使用 `uv run --locked python scripts/project_docs.py push --summary "完成了什么功能" --impact "实际改变了什么能力" --next-step "当前边界或下一步"`。命令先获取远端并确认没有分叉；只有推送成功后才生成一条开发日志。
- 自动记录不写用户材料、业务原文、真实用户 ID、错误堆栈或本机任务路径。

`STATUS-REPORT.md` 和 `config/platform-policy.yaml` 已写入 `.gitignore` 并退出 Git 跟踪。状态报告负责回看开发过程；稳定的架构事实、功能规则和待办仍必须同步到对应核心文档。
