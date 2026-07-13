# app/admin

M-Agent 本机管理后台。

## 职责

- 查看项目总览、六个板块的最新情况和首要待办。
- 查看五层整体架构和各功能的稳定运行、上线优化、建设、规划、暂缓或关闭状态。
- 查看开放 TODO、最近 Git 更新和写作/审核任务数量。
- 查看写作 Bot、审核 Bot、运维 Bot 心跳状态。
- 查看已安装 skill。
- 开启或关闭 skill。
- 配置用户可用 skill。
- 查看最近任务摘要。

## 边界

后台只做配置和观察，不做业务执行。

禁止：

- 写入业务 prompt。
- 直接调用模型生成内容。
- 展示 `.env`、API Key 或密钥。
- 浏览本机任意文件。
- 接受任意 Git 命令或自动联网刷新远端。
- 对公网开放。

政策库条目数已进入项目总览；政策明细筛选、勾选删除和删除前备份仍属于 `TODO-007`，尚未上线。

架构图的拓扑和证据路径维护在 `app/admin/services.py`，实际状态由 TODO、Skill 开关、相关代码是否存在和 Bot 心跳组合生成。建设成熟度与当前在线状态分开显示，不使用模型自由判断完成度。

## 启动

```bash
python -m app.admin.server --port 8787
```

打开：

```text
http://127.0.0.1:8787
```

## 测试

```bash
pytest tests/test_admin_services.py tests/test_admin_server.py -v
```
