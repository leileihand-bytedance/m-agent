# 企业微信长连接 SDK 选型说明

> ⚠️ **已废弃**:SDK 已选定 AiBotSDK,本文档仅供参考,请勿按本文档选型。

## 一、当前前提

用户已经在企业微信后台创建了智能机器人，并选择长连接方式，已经获得：

```text
Bot ID
Secret
```

注意：Bot ID 和 Secret 属于敏感配置，不应写入项目文档、README、Git 仓库或聊天记录。后续只应写入本机 `.env` 文件。

## 二、第一阶段需要的能力

第一阶段只做“企业微信经验沉淀入口”，需要 SDK 支持：

1. 使用 Bot ID 和 Secret 建立 WebSocket 长连接。
2. 接收文本消息。
3. 接收文件消息。
4. 下载并解密文件。
5. 发送 Markdown 或文本回复。
6. 支持心跳和断线重连。

## 三、候选方案

### 1. Python：`wecom-aibot-sdk`

`wecom-aibot-sdk` 是企业微信智能机器人 Python SDK，基于 WebSocket 长连接通道，文档说明支持消息收发、流式回复、模板卡片、事件回调、文件下载解密等能力。

优点：

1. 与第一阶段后续文件解析、Markdown 写入、AI 调用更顺手。
2. Python 生态处理 `.docx`、`.pdf`、`.txt`、`.md` 更方便。
3. 适合做一个轻量后台小程序。

风险：

1. 这是 Python 版本 SDK，需要实际测试文件消息、下载解密和断线重连能力。
2. 如果 SDK 与官方 Node SDK 行为有差异，可能需要回到 Node 方案。

### 2. Node.js：`@wecom/aibot-node-sdk`

`@wecom/aibot-node-sdk` 是企业微信智能机器人 Node.js SDK，多个开源项目和插件都以它作为底层 SDK。

优点：

1. 更接近企业微信智能机器人官方 Node SDK 生态。
2. 企业微信长连接相关示例和插件更多。

风险：

1. 第一阶段后续要做文档解析、AI 调用、Markdown 文件写入，Node.js 也能做，但对当前项目来说不如 Python 直观。
2. 如果后续主要逻辑在 Python 侧，还需要跨语言或改写。

## 四、推荐选择

第一阶段推荐先用：

```text
Python + wecom-aibot-sdk
```

理由：

1. 项目当前 `app/main.py` 已按 Python 简化入口创建。
2. 第一阶段核心工作不是通用机器人平台，而是文件解析和经验沉淀。
3. Python 更适合快速处理文档解析、提示词调用和本地 Markdown 知识文件写入。
4. 如果 Python SDK 在企业微信文件消息上测试不通过，再切换到 Node SDK。

## 五、下一步验证

下一步不直接做完整业务逻辑，先验证最小连接：

1. 用户在本机创建 `.env`。
2. 填入 Bot ID 和 Secret。
3. 程序读取 `.env`。
4. 程序连接企业微信智能机器人长连接。
5. 用户向机器人发送一条文本消息。
6. 程序收到消息并回复一条固定文本。

只有文本收发验证成功后，再进入文件接收和解析。

## 六、本机配置方式

请复制：

```text
app/config.example.env
```

为：

```text
.env
```

然后在 `.env` 中填写：

```text
WECOM_BOT_ID=你的 Bot ID
WECOM_BOT_SECRET=你的 Secret
```

不要把 `.env` 发给 AI，也不要提交到 Git。

## 七、参考资料

1. `wecom-aibot-sdk` PyPI 页面：说明其基于 WebSocket 长连接，并支持消息收发、事件回调、文件下载解密等能力。
2. `@wecom/aibot-node-sdk` npm 包信息：企业微信智能机器人 Node.js SDK，基于 WebSocket 长连接通道。
3. 多个第三方企业微信机器人插件也采用 `@wecom/aibot-node-sdk` 作为底层长连接 SDK。
