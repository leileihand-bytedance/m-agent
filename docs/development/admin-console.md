# M-Agent 本机管理后台

本文档说明当前管理后台的定位、能力边界和后续开发规则。

## 定位

管理后台是 M-Agent 的本机控制台，不是对外服务平台。

当前用途：

- 查看已安装的 `skills/*`。
- 开启或关闭某个 skill。
- 配置企业微信用户可以使用哪些 skill。
- 查看最近任务记录摘要。
- 后续管理政策知识库底层数据，包括查看、筛选、勾选删除和备份。

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
data/platform/jobs/
```

只展示任务摘要，包括：

- job_id
- 用户 ID
- 命中的 skill
- 标题
- 简短消息

不展示 `.env`、API Key 或完整正文。

## 规划能力：政策库数据管理

目标：让使用者在本机后台里可视化清理底层政策库，减少无关政策进入直报、简报写作材料。

第一阶段只管理本地 SQLite 政策库：

```text
data/policy_knowledge/policies.sqlite3
```

后台页面应提供：

1. 政策列表：显示来源、标题、发布日期、分类、链接和入库编号。
2. 筛选搜索：支持按来源、分类、关键词筛选。
3. 勾选删除：支持批量勾选无关政策。
4. 删除预览：删除前展示待删除清单。
5. 自动备份：真正删除前先复制 SQLite 到 `data/policy_knowledge/backups/`。

建议数据流：

```text
管理后台页面
  -> app/admin/services.py
  -> app.policy_knowledge.store
  -> data/policy_knowledge/policies.sqlite3
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
- 不读取 `.env`。
- 不展示密钥。
- 不提供任意文件浏览能力。
- 只能写入 skill 开关和用户权限配置。

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
