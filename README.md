# M-Agent

M-Agent 当前正在从多个独立企业微信 Bot，演进为：

```text
统一企业微信入口
  -> app/platform/ 新底座
  -> skills/ 功能区
  -> 受限工具层
  -> 企业微信返回
```

## 当前状态

已经具备：

- 新底座：`app/platform/`
- 本机管理后台：`app/admin/`
- 直报 Bot 入口适配层：`app/writing/`
- 直报 skill：`skills/direct_report/`
- 简报 skill：`skills/writer1/`、`skills/writer2/`
- 旧审核 Bot：`app/review/`

## 目录身份

```text
app/platform/  # 新底座区，当前主线
app/admin/     # 本机管理后台
app/writing/   # 当前直报 Bot 入口适配层
app/review/    # 旧审核 Bot，后续包装为 review skill
skills/        # 正式业务能力区
docs/archive/  # 历史方案，不作为新开发依据
archive/inactive-2026-07-04/ # 已归档停滞模块，不作为开发入口
```

## 常用入口

检查新底座配置：

```bash
python -m app.platform.cli --check-config
```

本地测试一条消息：

```bash
python -m app.platform.demo "帮我根据这个链接写直报：https://example.com"
```

检查直报 Bot 配置：

```bash
python -m app.writing.bot --check-config
```

启动本机管理后台：

```bash
python -m app.admin.server --port 8787
```

旧审核 Bot：

```bash
python -m app.review.main --check-config
python -m app.review.main
```

## 开发前阅读

1. `AGENTS.md` 或 `CLAUDE.md`
2. `docs/development/README.md`
3. `docs/development/architecture.md`
4. `docs/development/directory-standard.md`
5. `docs/capabilities/README.md`

## 测试

平台和直报入口：

```bash
python -m pytest tests/test_platform_registry.py tests/test_platform_router.py tests/test_platform_tools.py tests/test_platform_builtin_tools.py tests/test_platform_file_readers.py tests/test_platform_pydantic_runtime.py tests/test_direct_report_workflow.py tests/test_platform_runtime.py tests/test_platform_demo.py tests/test_platform_wecom_gateway.py tests/test_platform_storage.py tests/test_platform_identity.py tests/test_platform_app.py tests/test_platform_cli.py tests/test_writing_platform_bot.py tests/test_writing_portal.py tests/test_brief_writer_workflows.py tests/test_installed_writer_skills.py -v
```

管理后台：

```bash
python -m pytest tests/test_admin_services.py tests/test_admin_server.py -v
```

旧审核入口保护：

```bash
python tests/test_review_bot.py
```

全仓回归：

```bash
python -m pytest tests -q
```
