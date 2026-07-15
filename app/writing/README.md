# app/writing

当前企业微信写作 Bot 的入口适配层。

## 当前身份

这里负责连接企业微信写作 Bot，并把文本、Word/PDF/PPTX 文件消息和本机素材页提交交给新底座 `PlatformApp`。

当前链路：

```text
企业微信写作 Bot
  -> app/writing/bot.py
  -> app/writing/task_execution.py # 直报/简报持久任务适配
  -> app/writing/portal.py      # 结构化素材页与本地预览入口
  -> app/platform/app.py
  -> skills/direct_report/、skills/writer1/、skills/writer2/、skills/research_synthesis/ 或 skills/rewrite/
```

当前入口形态：

- 企业微信会话欢迎语当前默认引导用户直接发送链接，并说明是写简报还是写直报。
- 用户可直接粘贴文字并选择润色；`rewrite` 当前不接收文件或链接。
- 多份素材写简报时，入口按素材数量选择 `writer1` 或 `writer2`；做综合调研整合时，用户可连续发送 1 份现成提纲和多份部门素材，最后回复“开始写”。若系统仍需确认提纲，原文件和任务状态会继续保留，用户回答后自动续跑，不需要重新上传或再次回复“开始写”。
- 综合调研成功后不再在会话中铺开整篇长正文，而是先回复简短完成提示，再通过公共 `AttachmentDelivery` 回传采用现有公文基本格式的 `.docx` 初稿。Word 只允许从本次任务 `output/` 返回；写作入口输入仍受 20MB 总量限制，结果交付使用企业微信约 50MB 的 SDK 硬上限。
- 综合调研正文不是把各部门文件逐份拼接：工作流先按提纲形成可追溯材料台账，再按提纲问题跨部门归并；来源标签在综合段末保留一次，标题编号、原始文件名、遗漏一级主题和连续图片提醒在生成后确定性校正。
- 文本消息的即时提示会按实际路由到的 skill 变化，例如直报、单素材简报、多素材简报和综合调研整合分别使用不同话术。
- 企业微信新直报、单素材简报和多素材简报先返回队列任务编号，再由后台 worker 生成并主动发送初稿。任务入队前已经创建正式写作 job、复制文件快照；处理、会话收尾和发送分别记录检查点，Bot 重启后不会因为 intake 临时文件丢失而无法恢复。
- 写作队列使用独立 `runtime/task-execution/writing.sqlite3`，默认 1 个 worker、同一用户同一时刻只跑 1 项初稿。审核队列与写作队列分离，两个 worker 不会互相领取任务。
- 同一企业微信文字或文件消息按稳定消息 ID 去重；用户有初稿在途时，入口会提示等待，不接收下一批材料或改稿，避免不同批次互相覆盖。收到初稿后，上一稿改稿仍走现有实时会话链路。
- 初稿返回待澄清时，入口会恢复原材料上下文。用户确认“继续使用已读取素材写”后，直报和简报会忽略读取失败项并使用成功读取的素材继续。
- 开发者可在本机素材页一次性上传多个 Word/PDF/PPTX、粘贴链接、补充要求或文字素材。
- 服务端会拒绝非 `.docx` / `.pdf` / `.pptx` 文件，避免“上传成功但实际无法解析”。HTML 文件上传暂缓，网页仍通过链接读取。
- 提交后结果直接返回企业微信对话。
- 素材入口默认只监听 `127.0.0.1`，企业微信欢迎语不发送入口链接。本地 preview 仅允许回环地址访问；单次请求和企业微信单文件上限均为 20MB。
- 企业微信待组装文件和会话保存在 `M-Agent-Files/runtime/intake/`，默认 1800 秒有效；底层通过公共 `app/platform/intake.py` 统一原子状态、匿名用户目录、文件安全暂存、路径隔离和过期清理，写作 Bot 重启后会恢复有效会话。普通任务成功、处理失败、取消或过期后清理；需要用户补充说明的任务保留到用户回答并续跑完成。
- 写作状态机已转换为公共 `wait/submit/cancel/bypass` 协议；具体直报、简报、综合调研判断仍留在写作适配层。
- 每次写作任务最多接收 10 份文件，文件总大小不超过 20MB；数量上限和总大小上限会分别校验。
- 任务开始后，原文件复制到 `M-Agent-Files/tasks/writing/YYYY/MM/<job_id>/input/`，完整文档解析结果写入同一任务的 `work/documents/`。统一解析器默认安全上限为 50MB，可通过 `M_AGENT_DOCUMENT_MAX_MB` 调整，但入口 20MB 限制仍优先生效。
- DOCX 素材含图片时，图片只提取到任务 `work/` 用于确认存在性，不默认 OCR，也不嵌入综合调研稿；模型材料保留原位置提醒，正文在对应小节保留人工评估提示，连续的同部门提醒合并计数。
- 扫描 PDF 可只对识别为扫描页的页面按需 OCR；PPTX 在写作链路中仍主要作为文字和结构素材读取。底座可显式渲染页面，不等于已经支持 PPT 视觉审核或版式修改。
- 结果附件按文件大小设置等待时间并串行上传，完整“上传 + 发送”失败会重试；超限或仍失败时保留本机结果并向用户返回任务编号，详细错误进入运维事件。当前不自动压缩文档图片。
- 本地预览可直接打开：

```text
http://127.0.0.1:8790/compose/brief?preview=1
http://127.0.0.1:8790/compose/direct_report?preview=1
```

## 不再负责

这里不再维护直报写作规则、网页读取逻辑或模型 prompt。

直报业务规则唯一来源：

```text
skills/direct_report/
```

## 注意

不要改动原审核 Bot。

写作 Bot 使用：

```text
WRITING_BOT_ID
WRITING_BOT_SECRET
M_AGENT_PORTAL_BASE_URL
M_AGENT_DATA_DIR
M_AGENT_DOCUMENT_MAX_MB
M_AGENT_INTAKE_DIR
M_AGENT_WRITING_TASK_QUEUE_DB
M_AGENT_WRITING_TASK_WORKERS
M_AGENT_WRITING_TASK_POLL_SECONDS
M_AGENT_WRITING_TASK_RECOVERY_SECONDS
M_AGENT_WRITING_TASK_LEASE_SECONDS
```

`M_AGENT_DATA_DIR` 默认指向项目同级的桌面 `M-Agent-Files/`。用户上传、待组装文件、系统生成、知识库、会话和日志都保存在该目录，`app/writing/` 不得自行新增其他持久化目录。`M_AGENT_INTAKE_DIR` 仅用于明确覆盖待组装目录，正常部署不需要单独配置。

如果后续需要重新启用跨设备素材入口：

```text
M_AGENT_PORTAL_HOST=0.0.0.0
M_AGENT_PORTAL_PORT=8790
M_AGENT_PORTAL_BASE_URL=http://你的电脑局域网IP:8790
```

例如：

```text
M_AGENT_PORTAL_BASE_URL=http://192.168.1.23:8790
```

程序不会自动把素材页链接发给企业微信用户。若显式开放局域网访问，必须同时评估鉴权、网络边界和运维风险；不要把 `preview=1` 当作远程入口。

本地预览调试如果走 `local-preview-user`，也需要在 `config/platform-policy.yaml` 中给它授权 `direct_report`、`writer1`、`writer2`。需要调试综合调研整合时，再显式增加 `research_synthesis` 权限。

审核 Bot 使用 `app/review/` 的独立配置。

## 测试

```bash
uv run --locked pytest tests/test_writing_task_execution.py tests/test_writing_platform_bot.py tests/test_writing_portal.py tests/test_platform_task_execution.py tests/test_platform_document_service.py tests/test_platform_app.py tests/test_research_synthesis_workflow.py -v
uv run --locked python -m app.writing.bot --check-config
```
