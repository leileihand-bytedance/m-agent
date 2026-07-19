# M-Agent 公共底座

本文档说明 `app/platform/` 对所有入口和 Skills 提供的公共能力、接口边界和扩展约束。完整实现分层见 [整体架构](../development/architecture.md)。

## 职责

底座负责：

- 把入口消息转换为统一请求。
- 识别意图并从已启用 Skill 中路由。
- 校验用户和 Skill 权限。
- 管理多消息材料组装、会话和任务状态。
- 安全解析 DOCX、PDF 和 PPTX。
- 通过 `ToolGateway` 限制 Skill 可用工具。
- 调用 Pydantic AI 和 Skill workflow。
- 提供持久任务、幂等、并发、租约、恢复和取消。
- 安全交付文字和附件，并记录不含正文的状态和运维事件。

底座不负责：

- 直报、简报、综合调研、深银协动态、内参周报、润色或审核的业务规则。
- 为某个 Skill 私自扩大文件或工具权限。
- 读取任务目录以外的任意本机文件。
- 向外部用户暴露 shell、密钥、日志或历史任务。

## 主要模块

```text
app/platform/
├── app.py                   # 平台应用服务
├── registry.py              # Skill 注册表
├── router.py                # 路由
├── identity.py              # 用户和 Skill 权限
├── intent.py                # 新任务、改稿和追问意图
├── intake.py                # 公共材料暂存和动作协议
├── task_execution.py        # 持久任务执行器
├── task_status.py           # 不含正文的任务状态索引
├── conversation.py          # 活跃稿件和版本链
├── storage.py               # 任务目录和结果记录
├── tools.py                 # ToolGateway
├── builtin_tools.py         # 受限基础工具
├── pydantic_runtime.py      # 模型运行层
├── attachment_delivery.py   # 附件交付
├── documents/               # 文档安全解析、OCR 和渲染
├── gateway/                 # 企业微信公共适配
└── ops/                     # 运维事件、心跳、告警和日报
```

## 公共协议

### Skill 注册

每个 Skill 通过 `skills/<skill_id>/config.yaml` 声明：

- ID、名称和触发词。
- workflow 入口。
- 允许使用的工具。
- 输入输出类型。
- 是否支持继续改稿。

底座只加载已登记且启用的 Skill。

### 工具授权

Skill 只能调用 `config.yaml` 中声明且由底座注册的工具。工具必须验证任务目录和输入类型，不能接受任意本机路径。

### 材料组装

公共 intake 使用 `wait`、`submit`、`cancel` 和 `bypass` 动作描述入口决定。底座负责安全暂存、恢复、过期和文件限制；具体“何时开始写”“哪份是正文”等业务判断仍由入口或 Skill 负责。

### 文档服务

统一文档服务接收任务目录内的 DOCX、PDF 和 PPTX，输出标准文档结构。扫描 PDF OCR、PDF/PPTX 页面渲染和图片提取均受页数、时间、像素和容量限制。支持读取不等于支持视觉审核或版式修改。

### 持久任务

任务执行器提供消息幂等、排队、并发限制、租约与 fencing token、心跳、取消和进程恢复。每种业务任务仍需单独登记 handler，并验证模型调用、文件生成和外部发送的幂等性。

### 数据和日志

任务、会话、队列、日志、知识库和运维状态位于 `M-Agent-Files/`。Git 仓库只保存代码、脱敏测试样本和静态配置示例。

`app/platform/runtime_environment.py` 统一区分生产和测试：生产 Bot 只能从 `main` 启动；任务分支只能使用专用测试 Bot 和独立测试数据根目录。测试模式不回退生产凭据，并在连接企业微信前确认所有运行数据路径都位于测试根目录。这是代码硬边界，不由 Skill 或模型自行判断。

## 安全边界

1. 用户消息、网页和文档全部视为不可信输入。
2. 安全依赖代码校验和目录隔离，不能只靠 prompt。
3. 网页读取拒绝本机、内网、云元数据和非 HTTP/HTTPS 地址；发布日期同时识别常见新闻元数据和研究报告常用的 `citation_publication_date`、`DC.date`。权威站点的非标准模板若在公共页头后提前闭合 HTML，工具会在原始页面确有段落、但主解析器没有取得段落时切换解析方式，恢复后续文章正文和可见发布日期。公开 JSON 索引只返回受限数量的标题、URL 和发布日期元数据，不自动打开其中链接，后续读取仍由 Skill 的来源策略控制。
4. 文档读取拒绝目录越界、宏和异常压缩包等高风险输入。
5. 外部模型只接收当前任务需要的最少材料。
6. 日志、状态索引和控制台默认不读取或展示材料正文。

## 扩展要求

- 新公共能力放入 `app/platform/`，并提供平台测试和架构说明。
- 新业务能力放入 `skills/`，不能把业务判断塞进入口或底座。
- 复杂流程只有在现有协议不足且有真实场景证明时，才评估 LangGraph 等状态图框架。
- 底座当前行为改变时更新本文档或整体架构；开发历史写入月度日志，不追加到本文档。

## 相关入口

- 技术入口：`app/platform/README.md`
- 当前架构：`docs/development/architecture.md`
- 目录规范：`docs/development/directory-standard.md`
- 测试规范：`docs/development/testing-and-delivery.md`
- 业务能力：`docs/capabilities/`
