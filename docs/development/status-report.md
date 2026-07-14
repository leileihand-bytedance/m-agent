# 本地状态报告机制

`STATUS-REPORT.md` 是本机开发日志，不是 Git 中的项目事实来源，也不能替代架构、TODO、模块 README 和 skill 文档。

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
- post-commit：自动把提交时间、提交摘要、变更文件数量和同步的核心文档写入本机 `STATUS-REPORT.md`，并告警当前分支是否还有未推送提交。
- pre-push：推送前再次运行核心文档检查，并拒绝直接 `git push`。统一使用 `uv run --locked python scripts/project_docs.py push --summary "本次做了什么改动"`。
- 受管推送：先获取远端并确认没有分叉；只有推送成功后才追加“Git 推送”记录，内容包括推送范围、提交摘要、通俗改动说明、影响模块和文件数量。
- 自动记录不写用户材料、业务原文、真实用户 ID、错误堆栈或本机任务路径。

`STATUS-REPORT.md` 和 `config/platform-policy.yaml` 已写入 `.gitignore` 并退出 Git 跟踪。如果需要详细说明，应写入对应核心文档或 Git 提交信息，而不是把敏感运行内容写入状态报告。
