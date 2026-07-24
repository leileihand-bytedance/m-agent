# app/writing

当前企业微信写作 Bot 的入口适配层。

## 当前身份

这里负责连接企业微信写作 Bot，并把文本、Word/PDF/PPTX 文件消息和本机素材页提交交给新底座 `PlatformApp`。

当前链路：

```text
企业微信写作 Bot
  -> app/writing/bot.py
  -> app/writing/task_execution.py # 直报/简报/综合调研/深银协动态/内参周报持久任务适配
  -> app/writing/portal.py      # 结构化素材页与本地预览入口
  -> app/platform/app.py
  -> skills/direct_report/、skills/writer1/、skills/research_synthesis/、skills/shenyinxie_news/、skills/internal_weekly/ 或 skills/rewrite/
```

当前入口形态：

- 企业微信会话欢迎语当前默认引导用户直接发送链接，并说明是写简报还是写直报。
- 用户可直接粘贴文字并选择润色；`rewrite` 当前不接收文件或链接。
- 简报对用户只展示一个“简报写作”能力，内部也只使用 `writer1` 一个 ID 和 `skills/writer1/` 一套工作流；工作流根据材料关系采用单素材、多素材整合或建议拆分。历史持久化记录中的退役 ID 会在读取时归一，不需要保留第二套 Skill。做综合调研整合时，用户可连续发送 1 份现成提纲和多份部门素材，最后回复“开始写”。若系统仍需确认提纲，原文件和任务状态会继续保留，用户回答后自动续跑，不需要重新上传或再次回复“开始写”。
- 单素材和多素材简报在生成前先完成九类题材判断、语义策划和事实台账选择，生成后检查 1200 字上限、数字依据和高风险强结论。直报和简报续改共用底座 `RevisionEngine`；只改标题或指定段落时，由代码恢复未点名内容，避免模型顺手重写全文。
- 简报默认返回标题和正文；用户明确要求“输出 Word”“导出 Word”或“正式文档”时，`writer1` 使用项目内受控母版生成 `.docx`，企业微信先发同一文字稿再发附件。期号、日期、责任编辑和联系电话可在首次要求或后续对话中修改并随版本保存，签发人固定为母版值；只导出或只改这些版式字段时，任务关系层承接当前稿，工作流不调用模型重写正文。生产运行不读取桌面模板。
- 直报默认继续在企业微信对话中返回标题和正文。用户明确要求“导出 Word”“输出 Word”，或明确填写期号、总期号、日期时，`direct_report` 复制 Skill 内批准母版并原位替换标题、正文和指定版头字段；只导出或只填版头时不调用模型改写正文。写作队列先主动发送标题正文，再通过公共附件交付返回 `.docx`，两个交付项分别保存检查点。
- 综合调研成功后不再在会话中铺开整篇长正文，而是先回复简短完成提示，再通过公共 `AttachmentDelivery` 回传采用现有公文基本格式的 `.docx` 初稿。Word 只允许从本次任务 `output/` 返回；写作入口输入仍受 20MB 总量限制，结果交付使用企业微信约 50MB 的 SDK 硬上限。
- 综合调研正文不是把各部门文件逐份拼接：工作流先按提纲形成可追溯材料台账，再按提纲问题跨部门归并；来源标签在综合段末保留一次，标题编号、原始文件名、遗漏一级主题和连续图片提醒在生成后确定性校正。
- 文本消息的即时提示会按用户能力变化，例如直报、简报和综合调研整合使用不同话术；简报不再区分单素材版和多素材版提示。
- 深银协动态不要求用户提供素材，但必须明确月份和上半月/下半月；未明确时入口保留原任务并追问，用户答复后继续由同一 Skill 执行，确认前不搜索。入口路由后由底座使用当前 DeepSeek 模型配置调用原生 Web Search。Skill 先检索原权威白名单，专题全文不足 3 篇时再检索已核验的行业/广东主流媒体；网页正文还要经过日期与域名硬门槛、DeepSeek 结构化报送价值判断，综合稿只允许做原文摘编并在 Word 中标注。成品直接复制用户确认案例的净化母版并替换对应位置，正文按原文自然段写入，可点击链接随稿交付；生产运行不读取桌面案例。`SEARCH_API_*` 只用于显式覆盖独立搜索供应商，不能把 DeepSeek 模型地址拼接到 MiniMax 搜索端点。
- 内参周报不要求用户提供素材。用户发送“生成本周内参周报”后，入口把独立 `internal_weekly` Skill 提交到写作持久队列，并明确提示该任务需要检索、核验多类来源，耗时明显长于普通稿件；周一 15:00 收盘前会先生成上周五板块内容，并在资本市场固定位置用红色粗体标注“今日资本市场内容待收盘后更新”，15:00 后生成上周加当日完整版本。用户也可发送“生成一下今天的资本市场综述”，收盘后只生成可替换的当日行情更新块。初次回传简短完成说明、带原文链接和核验信息的 Markdown 内容核对稿以及 JSON 溯源清单；只有用户明确确认当前版本核对无误并要求生成 Word 时，Skill 才从净化母版生成洁净版 `.docx`，不重新搜索或调用模型。普通“生成 Word”会先要求用户明确批准。母版保留水印、红线、页眉页脚、页码和自动目录域；后台 Microsoft Word 在任务交付前更新整个目录并保存，代码核对目录标题、页码和书签完整后才返回附件，用户打开成品无需再手工更新目录。其板块规则和信源表属于 Skill 自身，不调用审核模块；同一大会或论坛的互补正式成果合并为一条，监管动态以实际监管机构为主体；同业动向按境内民营/数字银行、国际及香港数字银行、银行科技子公司分组检索，机构官网、投资者关系和法定披露优先，达到统一评分才入选、最多 5 条且排除营销宣传；市场观察在固定资本市场综述后按七类主题独立检索影响增长、通胀、利率、汇率、流动性、风险偏好或银行资产负债环境的国内外大事，按统一评分达标才入选、最多 5 条且不凑数；前沿观点依据可逐字核验的研报原文生成中文压缩摘要并在结尾标明出处，资本市场综述缺少任一固定数据组时不让模型补齐。
- 企业微信新直报、简报、综合调研、深银协动态和内参周报先返回对应写作类型的已受理提示，再由后台 worker 生成并主动发送结果。主动文字使用企业微信 `aibot_send_msg` 支持的 Markdown 类型，附件使用媒体消息；简报正式 Word 和综合调研分别按“完成文字 + Word 附件”两个交付项保存检查点；内参周报初次生成按“完成说明 + Markdown 核对稿 + JSON 溯源清单”保存，批准后 Word 按“完成说明 + `.docx` 附件”保存。后台始终保留任务编号，但正常受理和重复提交提示不向用户展示。任务入队前已经创建正式写作 job、复制文件快照；处理、会话收尾和发送分别记录检查点，Bot 重启后不会因为 intake 临时文件丢失而无法恢复。文本和附件逐项保存“已确认送达、明确未送达、送达未知”及判断依据；已经确认的部分不会重复发送，未知状态暂停自动重发并交由本机管理员恢复。普通运行日志额外记录任务、交付项类型和状态，不记录正文、文件名、路径或用户 ID。
- 写作队列使用独立 `runtime/task-execution/writing.sqlite3`，默认 1 个 worker、同一用户同一时刻只跑 1 项任务；不同用户按 `userid` 隔离并由全局 worker 上限调度。审核队列与写作队列分离，两个 worker 不会互相领取任务。直报、统一简报、综合调研、深银协动态和内参周报使用持久队列；即时润色保持实时多轮交互。
- 任务卡片、版本、材料台账、父子关系和待确认状态保存在 `runtime/task-relations/task-relations.sqlite3`。同一用户可以同时保留和排队多篇直报、简报或润色任务；可用标题关键词、任务序号或“切换到……”定位，不再把所有后续消息绑定到一个最近稿件。
- 同一企业微信文字或文件消息按稳定消息 ID 去重，重复消息检查在创建正式 job 和任务卡片前完成。普通续改以及补充、替换、参考新材料均可进入持久队列；目标稿本身仍在生成时，系统保留原要求和材料并提示等待，完成后用户回复“继续”即可恢复。
- “这一段只保留一个案例”“把新数据补到数字金融那篇第二段”“沿用上一版结构另写一份”等自然表达先经过公共任务关系层。任务序号、明确标题或“这个直报/简报”等文种指向优先；“再改、继续改、接着改”默认承接用户当前稿，不能被旧稿的主题关键词抢走。公共层确定具体任务后，公共改稿引擎再把要求解析成标题、指定段落或全文范围，执行未修改内容保护和结果自检；直报、简报各自继续执行本 Skill 的文种质量规则。确实无法唯一定位时只追问一个区分问题；用户回复稿件名称或纠正“另写一份”后沿用已上传材料，不要求重新发送。
- 初稿返回待澄清时，入口会恢复原材料上下文。用户确认“继续使用已读取素材写”后，直报和简报会忽略读取失败项并使用成功读取的素材继续。
- 开发者可在本机素材页一次性上传多个 Word/PDF/PPTX、粘贴链接、补充要求或文字素材。
- 服务端会拒绝非 `.docx` / `.pdf` / `.pptx` 文件，避免“上传成功但实际无法解析”。HTML 文件上传暂缓，网页仍通过链接读取。
- 提交后结果直接返回企业微信对话。
- 素材入口默认只监听 `127.0.0.1`，企业微信欢迎语不发送入口链接。本地 preview 仅允许回环地址访问；单次请求和企业微信单文件上限均为 20MB。
- 企业微信待组装文件和会话保存在 `M-Agent-Files/runtime/intake/`，默认 1800 秒有效；底层通过公共 `app/platform/intake.py` 统一原子状态、匿名用户目录、文件安全暂存、路径隔离和过期清理，写作 Bot 重启后会恢复有效会话。普通任务成功、处理失败、取消或过期后清理；需要用户补充说明的任务保留到用户回答并续跑完成。
- 写作状态机已转换为公共 `wait/submit/cancel/bypass` 协议；具体直报、简报、综合调研、深银协动态和内参周报判断仍留在写作适配层。
- 每次写作任务最多接收 10 份文件，文件总大小不超过 20MB；数量上限和总大小上限会分别校验。
- 任务开始后，原文件复制到 `M-Agent-Files/tasks/writing/YYYY/MM/<job_id>/input/`，完整文档解析结果写入同一任务的 `work/documents/`。统一解析器默认安全上限为 50MB，可通过 `M_AGENT_DOCUMENT_MAX_MB` 调整，但入口 20MB 限制仍优先生效。
- DOCX 素材含图片时，图片只提取到任务 `work/` 用于确认存在性，不默认 OCR，也不嵌入综合调研稿；模型材料保留原位置提醒，正文在对应小节保留人工评估提示，连续的同部门提醒合并计数。
- 扫描 PDF 可只对识别为扫描页的页面按需 OCR；PPTX 在写作链路中仍主要作为文字和结构素材读取。底座可显式渲染页面，不等于已经支持 PPT 视觉审核或版式修改。
- 结果附件按文件大小设置等待时间并串行上传；最终发送前的明确上传失败可重试，发送已经发起但回执未知时停止自动重发。超限、明确失败或状态未知时都保留本机结果，并向用户返回“处理编号”供管理员定位，详细错误进入运维事件。当前不自动压缩文档图片。
- 综合调研、深银协动态和内参周报属于附件必交付任务；成功结果缺少应有附件、附件为空或超出任务输出目录时，任务按处理失败进入重试和运维告警，不能只发完成提示后标记成功。直报和简报仍允许按用户要求只返回会话文字，不强制生成 Word。
- 底座统一限制模型单次请求时间和最多尝试次数。超时、限流、连接失败、鉴权失败、服务不可用和结构化响应错误转换为固定安全错误码；模型层重试耗尽后，后台任务不会再从头执行整篇写作，避免重复生成和放大故障。
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

## 运行

生产写作 Bot 使用 macOS 常驻服务，首次安装和日常管理在 `main` 执行：

```bash
uv run --locked python scripts/bot_services.py install writing
uv run --locked python scripts/bot_services.py status writing
uv run --locked python scripts/bot_services.py restart writing
```

写作代码、写作 Skill 或写作模型配置更新后需要重启 `writing`；公共底座或依赖更新后重启 `all`。直接执行 `uv run --locked python -m app.writing.bot` 只用于前台排障，必须先执行 `scripts/bot_services.py stop writing`，避免重复连接同一企业微信 Bot。

## 注意

不要改动原审核 Bot。

写作 Bot 使用：

```text
WRITING_BOT_ID
WRITING_BOT_SECRET
M_AGENT_PORTAL_BASE_URL
M_AGENT_DATA_DIR
M_AGENT_DOCUMENT_MAX_MB
M_AGENT_MODEL_TIMEOUT_SECONDS
M_AGENT_MODEL_MAX_ATTEMPTS
M_AGENT_MODEL_RETRY_BACKOFF_SECONDS
M_AGENT_INTAKE_DIR
M_AGENT_WRITING_TASK_QUEUE_DB
M_AGENT_TASK_RELATION_DB
M_AGENT_WRITING_TASK_WORKERS
M_AGENT_WRITING_TASK_POLL_SECONDS
M_AGENT_WRITING_TASK_RECOVERY_SECONDS
M_AGENT_WRITING_TASK_LEASE_SECONDS
SEARCH_API_KEY
SEARCH_API_BASE_URL
```

生产入口还使用 `M_AGENT_RUNTIME_ENV=production`，并且只能从 `main` 启动。任务分支需要真实企业微信联调时，必须改为 `M_AGENT_RUNTIME_ENV=test`，配置 `M_AGENT_TEST_WRITING_BOT_ID`、`M_AGENT_TEST_WRITING_BOT_SECRET` 和独立 `M_AGENT_TEST_DATA_DIR`；测试模式不会回退到 `WRITING_BOT_*`。

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

本地预览调试如果走 `local-preview-user`，需要在 `config/platform-policy.yaml` 中给它授权 `direct_report`、`writer1`。需要调试综合调研整合或内参周报时，再显式增加 `research_synthesis` 或 `internal_weekly` 权限。

审核 Bot 使用 `app/review/` 的独立配置。

## 测试

```bash
uv run --locked pytest tests/test_writing_task_execution.py tests/test_writing_platform_bot.py tests/test_writing_portal.py tests/test_platform_task_relations.py tests/test_platform_task_execution.py tests/test_platform_document_service.py tests/test_platform_app.py tests/test_research_synthesis_workflow.py tests/test_shenyinxie_news_*.py tests/test_internal_weekly_*.py -v
uv run --locked pytest tests/test_platform_runtime_environment.py -v
uv run --locked python -m app.writing.bot --check-config
```
