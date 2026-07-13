# app/platform

M-Agent 新底座区，当前主线。

## 职责

- 接收入口层传来的标准消息。
- 加载 `skills/*/config.yaml`。
- 在已启用 skill 中做路由。
- 检查用户是否有权限。
- 创建任务目录和结果记录。
- 通过 `ToolGateway` 控制工具调用。
- 调用 skill workflow 和 Pydantic AI 运行层。

## 边界

这里不写具体业务规则。

不要在这里写：

- 直报怎么写。
- 简报怎么写。
- 审核规则是什么。
- 改稿风格是什么。

这些内容应放在：

```text
skills/<skill_id>/
```

## 主要入口

```text
app/platform/app.py       # PlatformApp
app/platform/cli.py       # 本地配置检查和消息测试
app/platform/demo.py      # 本地 demo
app/platform/gateway/     # 企业微信消息核心适配
app/platform/documents/   # DOCX/PDF/PPTX 统一安全解析和标准材料结构
```

## 测试

```bash
pytest tests/test_platform_registry.py tests/test_platform_router.py tests/test_platform_tools.py tests/test_platform_builtin_tools.py tests/test_platform_file_readers.py tests/test_platform_document_service.py tests/test_platform_data_paths.py tests/test_platform_pydantic_runtime.py tests/test_platform_runtime.py tests/test_platform_demo.py tests/test_platform_wecom_gateway.py tests/test_platform_storage.py tests/test_platform_identity.py tests/test_platform_app.py tests/test_platform_cli.py -v
```
