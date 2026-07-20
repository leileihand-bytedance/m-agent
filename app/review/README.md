# `app/review` 审核模块

`app/review/` 是独立审核 Bot 的入口和业务实现。用户可用审核类型与边界见 [材料审核能力](../../docs/capabilities/review.md)，启动、日志和故障处理见 [Bot 运行维护](../../docs/operations/bots.md)。

## 模块职责

- 接收企业微信文字、DOCX、HTML 和 PPTX。
- 识别审核类型及格式审核等显式要求。
- 组装单文件、多文件和追加操作。
- 执行通用、内参、半月报、公文格式、HTML、PPTX 和联合审核。
- 生成审核消息、报告和标记 Word。
- 通过公共任务执行器、附件交付、状态索引和运维事件完成交付。
- 对外仍作为一个审核能力运行；内部八类审核使用稳定子能力 ID，分别记录日志、任务状态和运行指标。

## 主要代码

```text
app/review/
├── main.py                     # 企业微信入口和任务分流
├── task_execution.py           # 审核持久任务适配
├── capabilities.py             # 八类审核子能力注册表和任务类型映射
├── observability.py            # 不含正文的任务运行指标
├── intake.py                   # 审核材料和操作组装
├── core/                       # 共享问题、模型运行、证据、去重和指标
├── rules/                      # 规则目录和各审核类型静态profile
├── reviewer.py                 # 内参审核
├── general_reviewer.py         # 通用文字和 Word 审核
├── halfmonthly_reviewer.py     # 半月报审核
├── official_format_checker.py  # 公文格式审核
├── multi_file_reviewer.py      # 多文件联合审核
├── html_parser.py              # 静态 HTML 可见文字提取
├── ppt/                        # PPTX 提取、规则、模型审核和输出
├── error_marker.py             # Word 原文定位和标记
├── output_formatter.py         # 用户可见审核消息
└── bot_logging.py              # 按天和大小分片日志
```

当前八类子能力为：通用文字、通用 Word、静态 HTML、内参、半月报、公文格式、PPTX 和多文件联合审核。子能力注册只负责稳定身份、日志和统计边界，不改变用户意图识别，也不把专属规则合并到同一提示词。

内参审核静态规则当前位于 `app/data/rules.md`。通用语义规则文字位于 `rules_general.md`，规则ID、规则族、证据和定位政策统一登记在 `rules/catalog.py`；profile只选择规则，不复制完整规则或提示词。

纯文字、通用Word和静态HTML分别使用 `general_text`、`general_docx` 和 `general_html` profile。内参和半月报分别使用 `neican_docx`、`halfmonthly_docx` profile，并复用共享模型调用、固定预算重试、阶段指标、逐字证据和去重；内参两阶段结构、半月报领导职务与板块顺序等专属判断仍留在原模块。PPT、公文格式和多文件继续按各自流程运行；PPT不会直接使用Word提示词。

## 配置

审核 Bot 使用根目录 `.env`，示例见 `app/review/config.example.env`。主要配置组：

- `WECOM_REVIEW_*`：审核 Bot 凭证。
- `REVIEW_MODEL_*`、`REVIEW_ANTHROPIC_*`：审核独立模型。
- `M_AGENT_DATA_DIR`：仓库外运行数据根目录。
- `M_AGENT_REVIEW_*`：材料组装、队列、并发和恢复。
- `M_AGENT_REVIEW_RULES`：内参规则文件。
- `M_AGENT_LOG_MAX_MB`：日志分片上限。

生产审核使用 `M_AGENT_RUNTIME_ENV=production`，只能从 `main` 启动。开发分支联调使用 `M_AGENT_RUNTIME_ENV=test`、`M_AGENT_TEST_REVIEW_BOT_ID/SECRET` 和独立 `M_AGENT_TEST_DATA_DIR`；不会回退到 `WECOM_REVIEW_BOT_*`，也不会写生产审核目录。

真实值不能写入文档、代码、测试或 Git。

## 运行

```bash
uv run --locked python -m app.review.main --check-config
uv run --locked python -m app.review.main
```

任务和结果默认位于：

```text
M-Agent-Files/tasks/review/YYYY/MM/<job_id>/
M-Agent-Files/runtime/intake/review/
M-Agent-Files/runtime/task-execution/
M-Agent-Files/runtime/logs/
M-Agent-Files/runtime/logs/review-capabilities/<capability_id>/
```

审核总日志和按用户日志继续保留；进入具体审核任务后，同一条记录还会写入对应子能力日志，并带 `capability_id` 和 `task_id`。每个当前任务的 `meta.json` 只增加耗时、模型调用/失败、阶段耗时、降级阶段和问题数量等不含正文的 `observability` 字段。管理台按八类子能力汇总处理、交付、耗时、模型调用和问题数量；历史任务能根据已有任务类型或文档类型归类，无法可靠归类的旧记录只保留在审核总量中。

## 技术边界

- HTML 只做静态解析，不执行脚本或请求外部资源。
- PPTX 只处理任务目录内的标准文件；第一阶段不审核图片文字和视觉版式。
- Word 标记必须命中原文，模型伪造的证据和位置不能进入结果。
- 用户侧只收到简洁结果和必要的处理编号；内部路径、堆栈和详细异常进入运维事件。
- 八类审核均已使用审核专用 SQLite 持久任务。多文件联合审核在入队前把 2 至 5 份 DOCX、主文件序号和补充要求固化到正式任务目录，入口临时文件随后清理。
- 审核文字结果和附件逐项保存“已确认送达、明确未送达、送达未知”及判断依据；进程在部分发送后中断时，不会重新发送已经确认成功的结果，发送状态无法确认时会停止自动重发并进入本机受控恢复。恢复只交付原检查点中的结果，不会重新执行审核模型。
- 共享核心不包含文种判断；结构敏感和类型专属规则必须由静态profile隔离，不能因为规则名称相近而全局启用。

## 测试

```bash
uv run --locked pytest tests/test_review_shared_core.py tests/test_review_bot.py tests/test_review_task_execution.py tests/test_review_general.py tests/test_review_general_rules.py tests/test_review_halfmonthly.py tests/test_review_html.py tests/test_official_format_review.py tests/test_review_multi_file.py tests/test_review_ppt_*.py -v
```

真实文件、企业微信附件、进程重启和重复发送验收按 `docs/development/testing-and-delivery.md` 执行。
