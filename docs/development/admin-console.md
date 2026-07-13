# M-Agent 本机管理后台

本文档说明当前管理后台的定位、能力边界和后续开发规则。

## 定位

管理后台是 M-Agent 的本机控制台，不是对外服务平台。

当前用途：

- 查看项目总览，包括 Skill 数量、开放待办、写作/审核任务量和本地 Git 同步摘要。
- 查看底座、写作、审核、知识库、入口运维、管理后台六个板块的当前情况、最近更新和首要待办。
- 查看写作 Bot、审核 Bot、运维 Bot 的最近心跳，识别正常、超时或未运行。
- 直接读取 `docs/development/TODO.md`，按优先级展示当前开放待办。
- 查看已安装的 `skills/*`。
- 开启或关闭某个 skill。
- 配置企业微信用户可以使用哪些 skill。
- 查看最近任务记录摘要。
- 后续管理政策知识库底层数据，包括查看、筛选、勾选删除和备份；该能力尚未完成。

默认只监听：

```bash
127.0.0.1
```

不要把它直接暴露到公网或公司内网。

## 启动

```bash
python -m app.admin.server --port 8787
```

打开：

```text
http://127.0.0.1:8787
```

## 当前能力

### 项目总览

控制台首页自动聚合以下只读信息：

- `skills/*/config.yaml`：Skill 安装和启用数量。
- `docs/development/TODO.md`：开放待办、优先级、归属，以及优先匹配“待补、待完成、尚未完成、下一步”的未完成动作。
- 本机 Git 元信息：当前分支、最近提交、本地与已知远端记录差异和未提交变更数量；不显示未提交文件名，也不会自动联网 `fetch`。
- `M-Agent-Files/runtime/ops/heartbeats/`：写作、审核、运维 Bot 心跳。
- `M-Agent-Files/tasks/writing/`、`tasks/review/`：只统计任务数量；最近任务表仍只展示写作任务摘要。
- 政策库和微众银行信息库：只读取固定数据表的总条目数，不开放 SQL。

板块状态由对应开放待办的优先级生成：P0 显示“重点推进”，P1 显示“建设中”，P2/P3 显示“持续优化”，没有开放待办时显示“稳定”。“最新更新”来自对应代码目录最近一条 Git 提交，因此不需要再维护第二份项目进度表。

运行状态只说明心跳是否在配置阈值内，不代表每一次模型调用都成功。具体异常仍以运维 Bot 告警和日志为准。

### Skill 开关

后台读取：

```text
skills/<skill_id>/config.yaml
```

点击开启/关闭时，只修改其中的：

```yaml
enabled: true/false
```

说明：

- 开启 skill 只是让它进入路由候选。
- 该 skill 仍然需要有可运行的 `workflow`。
- 用户仍然需要在权限策略里被允许使用该 skill。

### 用户权限

后台读取和写入：

```text
config/platform-policy.yaml
```

用户权限格式：

```yaml
users:
  user-001:
    allowed_skills:
      - direct_report
```

后台页面中多个 skill 用英文逗号分隔：

```text
direct_report, writer1
```

### 最近任务

后台读取：

```text
../M-Agent-Files/tasks/writing/YYYY/MM/
```

只展示任务摘要，包括：

- job_id
- 用户 ID
- 命中的 skill
- 标题
- 简短消息

不展示 `.env`、API Key 或完整正文。

项目总览会另外统计审核任务数量，但不会读取或展示审核原文、段落预览和用户材料。

## 规划能力：政策库数据管理

目标：让使用者在本机后台里可视化清理底层政策库，减少无关政策进入直报、简报写作材料。

第一阶段只管理本地 SQLite 政策库：

```text
../M-Agent-Files/knowledge/policy/policies.sqlite3
```

后台页面应提供：

1. 政策列表：显示来源、标题、发布日期、分类、链接和入库编号。
2. 筛选搜索：支持按来源、分类、关键词筛选。
3. 勾选删除：支持批量勾选无关政策。
4. 删除预览：删除前展示待删除清单。
5. 自动备份：真正删除前先复制 SQLite 到 `../M-Agent-Files/knowledge/policy/backups/`。

建议数据流：

```text
管理后台页面
  -> app/admin/services.py
  -> app.policy_knowledge.store
  -> ../M-Agent-Files/knowledge/policy/policies.sqlite3
  -> 备份 SQLite
  -> 删除所选 source + doc_id
```

操作原则：

- 以后清理政策库时，优先从后台删除底层 SQLite 数据。
- 后台不提供任意 SQL 输入。
- 后台不提供任意文件浏览。
- 删除必须以 `source + doc_id` 为唯一定位依据。
- 删除必须先备份，再执行。
- 删除结果应显示：删除前数量、删除数量、删除后数量、备份文件路径。

第一阶段不做：

- 不做多人协同后台。
- 不做公网访问。
- 不做复杂权限系统。
- 不做 Obsidian 软件 UI 自动操作。
- 不直接影响企业微信用户侧能力。

## 安全边界

当前后台的安全设计：

- 默认只绑定本机地址。
- 只通过底座配置解析读取数据路径和心跳阈值，不展示或返回 `.env` 内容。
- 不展示密钥。
- 不提供任意文件浏览能力。
- Git 只执行代码内固定的只读查询，不接受页面传入命令，也不自动访问网络。
- 页面当前只能写入 skill 开关和用户权限配置；项目总览、待办、运行状态和更新记录均为只读。

后续如果要多人使用后台，必须先补：

- 登录鉴权。
- 操作审计。
- CSRF 防护。
- 更细粒度的权限。
- 内网部署方案。

政策库删除能力还必须补：

- 删除前确认页。
- 删除前自动备份。
- 删除后的 Wiki 重建结果展示。
- 删除失败时不覆盖原有 Wiki。

## 后续开发规则

改后台时优先改：

```text
app/admin/
tests/test_admin_*.py
docs/development/admin-console.md
```

不要在后台里直接实现写作、审核等业务能力。业务能力仍然放在：

```text
skills/<skill_id>/
```

后台只负责配置和观察。

## 测试

管理后台相关测试：

```bash
pytest tests/test_admin_services.py tests/test_admin_server.py -v
```

测试至少覆盖：

- TODO 字段解析、开放状态和优先级排序。
- Bot 心跳正常、超时、缺失和损坏文件降级。
- 项目总览只统计任务和知识库数量，不读取材料正文。
- 页面转义 Skill、TODO 和 Git 摘要，不渲染其中的 HTML。

政策库数据管理上线前至少覆盖：

- 列表筛选不会返回 `.env`、任务日志或其他本机文件内容。
- 删除操作只按 `source + doc_id` 删除指定政策。
- 删除前会生成备份文件。
- 删除后会触发 Wiki 重建。
- 删除空选择、重复选择、非法 `doc_id` 时返回可理解的错误。

涉及底座联动时再跑：

```bash
pytest tests/test_platform_registry.py tests/test_platform_router.py tests/test_writing_platform_bot.py -v
```
