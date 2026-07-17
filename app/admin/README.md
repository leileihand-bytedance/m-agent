# app/admin

M-Agent 本机管理后台。

## 职责

- 通过可缩放关系图和状态清单查看项目总览，包括五层整体架构、能力依赖及稳定运行、上线优化、建设、规划、暂缓或关闭状态。
- 查看六个板块的最新情况和首要待办。
- 查看开放 TODO、最近 Git 更新和写作/审核任务状态统计。
- 查看写作 Bot、审核 Bot、材料润色 Bot、运维 Bot 心跳状态。
- 查看已安装 skill。
- 开启或关闭 skill。
- 按需配置用户可用 skill；默认页面不读取、不展示用户权限。
- 按需查看最近任务摘要；默认页面不读取、不展示任务记录。

## 边界

后台只做配置和观察，不做业务执行。

禁止：

- 写入业务 prompt。
- 直接调用模型生成内容。
- 展示 `.env`、API Key 或密钥。
- 浏览本机任意文件。
- 接受任意 Git 命令或自动联网刷新远端。
- 对公网开放。

政策库条目数已进入知识库板块汇总；政策明细筛选、勾选删除和删除前备份仍属于 `TODO-007`，尚未上线。

架构节点、关系和证据路径维护在 `app/admin/services.py`，实际状态由 TODO、Skill 开关、相关代码是否存在和 Bot 心跳组合生成。当前架构图已经覆盖直报、简报、综合调研、材料润色、深银协动态，以及通用 Word/文字、静态 HTML、公文格式、多文件和单份 PPTX 低级错误审核；`TODO-031` 的审核共享核心单独显示为待建设。关系图支持状态筛选、缩放、拖动、点击节点查看详情，并保留状态清单作为备用视图。建设成熟度与当前在线状态分开显示，不使用模型自由判断完成度。

控制台专项测试会核对仓库中每个 `skills/*/config.yaml` 都已经映射到至少一个架构能力节点。新增 Skill 但未同步项目总览时，测试必须失败，避免控制台再次漏项。

交互图使用本地固化的 `vis-network 10.1.0`，文件和 MIT/Apache-2.0 许可证位于 `app/admin/static/vendor/`。页面不从 CDN 加载脚本，因此断网时仍可使用，也不会把项目架构信息发送到第三方站点。

用户权限和最近任务属于按需敏感区。默认访问 `/` 时不会读取或生成这两块内容；只有管理员主动点击“显示用户权限与任务记录”，进入 `/?show_sensitive=1` 后才加载。隐藏按钮会返回默认页面。

## 任务统计口径

- 写作任务总数按 `meta.json` 统计，成稿、待补充、失败、处理中或中断按不含正文的 `status.json` 分类；持久队列的 `queued`、`running` 与同步路径的 `processing` 都归入“进行中”，不会误计为未知状态。
- 审核任务总数兼容历史 `meta.md`、当前 `meta.json` 和已有 `output/report.md` 的归档；“已生成审核报告”只表示本机报告文件存在，不等于企业微信已成功发给用户。
- 管理台默认总览不读取 `output/result.json` 或材料正文。历史状态使用 `uv run --locked python scripts/backfill_task_status.py` 预演，确认后执行 `uv run --locked python scripts/backfill_task_status.py --apply`。

## 启动

```bash
uv run --locked python -m app.admin.server --port 8787
```

打开：

```text
http://127.0.0.1:8787
```

## 测试

```bash
uv run --locked pytest tests/test_admin_services.py tests/test_admin_server.py -v
```
