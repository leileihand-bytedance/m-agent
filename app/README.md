# app 目录身份说明

`app/` 存放 M-Agent 的运行代码。当前项目已经从早期单一 Bot 原型，演进为“新底座 + skills 功能区 + 过渡入口”的结构。

真实运行数据位于项目外部的桌面 `M-Agent-Files/`，`app/` 和仓库内其他代码目录不得保存用户上传原件或系统生成结果。

## 当前主线

```text
app/platform/  # 新底座区
skills/        # 正式业务能力区
app/writing/   # 当前直报 Bot 入口适配层
app/admin/     # 本机管理后台
```

后续新增能力时，优先新增或修改 `skills/<skill_id>/`，不要把业务规则写进 `app/platform/` 或入口 Bot。

## 目录说明

### `app/platform/`

M-Agent 新底座。负责：

- 路由用户意图。
- 加载 skill。
- 检查用户权限。
- 创建任务目录。
- 控制工具调用权限。
- 调用 Pydantic AI 运行层。
- 通过统一文档服务安全解析 DOCX、PDF 和 PPTX，并把完整结果保存在任务 `work/`。

### `app/admin/`

本机管理后台。负责：

- 开关 skill。
- 配置用户可用 skill。
- 查看最近任务记录。
- 汇总六个项目板块的最新 Git 更新、开放待办、任务数量和 Bot 心跳。
- 用五层交互式关系图和状态清单展示入口、底座、业务功能、工具知识库、运维数据、能力关系及各能力建设状态。

它不是业务能力区，不写写作、审核规则。

### `app/writing/`

当前直报企业微信 Bot 的入口适配层。它负责连接企业微信和调用新底座。

直报写作规则不在这里，唯一来源是：

```text
skills/direct_report/
```

### `app/review/`

旧审核 Bot。当前继续独立运行，后续包装为：

```text
skills/review/
```

迁移前不要大改。

### 已归档停滞模块

早期统一 agent、领导风格沉淀 Bot、旧 prompt、领导风格历史材料和一次性诊断脚本已移出 `app/` 主线目录，统一归档到：

```text
archive/inactive-2026-07-04/
```

这些内容不作为当前开发入口。

新底座入口优先使用：

```bash
python -m app.platform.cli --check-config
python -m app.platform.demo "帮我根据这个链接写直报：https://..."
python -m app.writing.bot --check-config
```

## 常用验证

平台和直报入口：

```bash
python -m pytest tests/test_platform_registry.py tests/test_platform_router.py tests/test_platform_tools.py tests/test_platform_builtin_tools.py tests/test_platform_file_readers.py tests/test_platform_document_service.py tests/test_platform_data_paths.py tests/test_platform_pydantic_runtime.py tests/test_direct_report_workflow.py tests/test_platform_runtime.py tests/test_platform_demo.py tests/test_platform_wecom_gateway.py tests/test_platform_storage.py tests/test_platform_identity.py tests/test_platform_app.py tests/test_platform_cli.py tests/test_writing_platform_bot.py tests/test_writing_portal.py tests/test_brief_writer_workflows.py tests/test_installed_writer_skills.py -v
```

旧审核入口保护：

```bash
python tests/test_review_bot.py
```
