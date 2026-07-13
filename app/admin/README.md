# app/admin

M-Agent 本机管理后台。

## 职责

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
- 对公网开放。

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
