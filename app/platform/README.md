# app/platform

M-Agent 新底座区，当前主线。

## 职责

- 接收入口层传来的标准消息。
- 加载 `skills/*/config.yaml`。
- 在已启用 skill 中做路由。
- 检查用户是否有权限。
- 维护按入口和用户隔离的多任务卡片，识别续改、补料、派生、新建、追问恢复、切换和取消。
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
app/platform/intake.py    # 公共材料引用、任务动作、提交协议和安全暂存
app/platform/task_relations.py # 多任务卡片、任务关系、材料角色、语义兜底和判断指标
app/platform/task_execution.py # 持久任务、幂等、并发、租约、恢复和取消
app/platform/delivery_state.py # 企业微信文本和附件统一交付状态
app/platform/delivery_recovery.py # 本机受控交付恢复，不重新运行模型
app/platform/attachment_delivery.py # 企业微信结果附件交付和失败兜底
app/platform/documents/   # 统一安全解析、扫描 PDF OCR 和页面渲染
```

## 测试

```bash
uv run --locked pytest tests/test_platform_registry.py tests/test_platform_router.py tests/test_platform_tools.py tests/test_platform_builtin_tools.py tests/test_platform_file_readers.py tests/test_platform_document_service.py tests/test_platform_document_enrichment.py tests/test_platform_data_paths.py tests/test_platform_intake.py tests/test_platform_intake_protocol.py tests/test_platform_task_relations.py tests/test_platform_task_execution.py tests/test_platform_task_status.py tests/test_platform_delivery_state.py tests/test_platform_delivery_recovery.py tests/test_platform_attachment_delivery.py tests/test_platform_pydantic_runtime.py tests/test_platform_runtime.py tests/test_platform_demo.py tests/test_platform_wecom_gateway.py tests/test_platform_storage.py tests/test_platform_identity.py tests/test_platform_app.py tests/test_platform_cli.py -v
```
