# 企业微信经验沉淀入口开发计划 v0.1

## 一、目标

本计划用于指导第一阶段原型实现。

第一阶段只做一个简单可维护的小程序：用户通过企业微信智能机器人提交材料，程序解析材料并生成领导风格提炼建议，通过企业微信返回给用户确认。用户确认后，程序更新该领导的风格档案。

第一阶段不搭复杂 Agent 框架，不创建 `agents/templates/knowledge/runs/services` 这类多层目录。

## 二、实际目录结构

第一阶段采用：

```text
M-Agent/
  README.md
  AGENTS.md
  CLAUDE.md
  .gitignore

  docs/

  app/
    README.md
    config.example.env
    main.py
    prompts/
      style_extraction.md

  data/
    leaders/
      example-leader/
        source/
        suggestions/
        profile.md
        update-log.md
```

说明：

1. `app` 是唯一程序目录。
2. `app/prompts/style_extraction.md` 同时承担 Agent 定义和输出模板作用。
3. `data/leaders/某领导/source` 保存原始材料和解析文本。
4. `data/leaders/某领导/suggestions` 保存 AI 提炼建议。
5. `data/leaders/某领导/profile.md` 保存用户确认后的风格档案。
6. `data/leaders/某领导/update-log.md` 保存更新记录。

## 三、技术原则

1. 企业微信接入层复用成熟长连接 SDK。
2. 经验沉淀逻辑自建，不把 OpenClaw、Hermes、LangBot 作为第一版主系统。
3. 第一版不使用数据库。
4. 第一版不做网页后台。
5. 第一版不做完整写稿流程。
6. AI 提炼建议必须先返回用户确认，不能自动写入 `profile.md`。
7. 每次写入必须记录来源和确认方式。

## 四、阶段拆分

### 阶段 0：项目骨架

目标：建立简单项目结构。

任务：

1. 创建 `README.md`。
2. 创建 `.gitignore`。
3. 创建 `app/README.md`。
4. 创建 `app/config.example.env`。
5. 创建 `app/prompts/style_extraction.md`。
6. 创建 `data/leaders/example-leader/source/`。
7. 创建 `data/leaders/example-leader/suggestions/`。
8. 创建 `data/leaders/example-leader/profile.md`。
9. 创建 `data/leaders/example-leader/update-log.md`。

验收标准：

1. 用户能看懂每个目录的用途。
2. 敏感配置不会进入仓库。
3. 暂不创建复杂框架目录。

### 阶段 1：企业微信文本收发

目标：程序能连接企业微信智能机器人，并处理基础文本指令。

任务：

1. 选择企业微信智能机器人长连接 SDK。
2. 读取配置中的 Bot ID、Secret 等信息。
3. 建立长连接。
4. 监听文本消息。
5. 支持基础回复。

第一版指令：

```text
沉淀领导风格：张总
开始提炼
确认全部
确认 1、3
不入库
取消
```

验收标准：

1. 发送“沉淀领导风格：张总”，机器人能回复并进入收集状态。
2. 发送未知指令，机器人能给出简短帮助。

### 阶段 2：文件接收与保存

目标：程序能接收文件并保存到对应领导目录。

任务：

1. 接收企业微信文件消息。
2. 下载文件。
3. 根据当前会话中的领导姓名保存文件。
4. 保存文件元信息。

保存位置：

```text
data/leaders/张总/source/
```

验收标准：

1. 上传文件后，原始文件能保存到 `source`。
2. 未指定领导时上传文件，机器人提示先发送“沉淀领导风格：姓名”。

### 阶段 3：文件解析

目标：程序能从常见文件中提取文本。

第一版支持：

1. `.docx`
2. `.pdf`
3. `.txt`
4. `.md`

任务：

1. 解析文件文本。
2. 保存解析结果到 `source`。
3. 解析失败时返回清楚提示。

验收标准：

1. `.docx` 能解析。
2. `.pdf` 能解析或给出明确失败提示。
3. `.txt` 和 `.md` 能读取。
4. 解析后的文本文件能保存。

### 阶段 4：领导风格提炼

目标：AI 能根据材料生成结构化建议。

任务：

1. 编写 `app/prompts/style_extraction.md`。
2. 读取本次解析文本。
3. 读取已有 `profile.md`。
4. 调用 AI 生成风格提炼建议。
5. 保存建议到 `suggestions`。
6. 通过企业微信返回摘要。

建议输出包含：

```text
材料来源
本次观察到的风格倾向
建议写入 profile.md 的内容
建议加入常用表达
建议加入慎用表达
不建议沉淀的内容
需要用户确认的问题
```

验收标准：

1. 能生成结构化建议。
2. 能区分“建议沉淀”和“不建议沉淀”。
3. 能返回给企业微信。
4. 不自动更新 `profile.md`。

### 阶段 5：用户确认与写入

目标：用户确认后，程序才更新风格档案。

任务：

1. 解析“确认全部”。
2. 解析“确认 1、3”。
3. 处理“不入库”。
4. 将确认内容写入 `profile.md`。
5. 将更新记录写入 `update-log.md`。

验收标准：

1. “确认全部”会写入全部可沉淀内容。
2. “确认 1、3”只写入指定条目。
3. “不入库”不会修改 `profile.md`。
4. 每次写入都有来源和确认方式记录。

### 阶段 6：测试与说明

目标：确保第一版闭环可手动验证。

至少测试：

1. 指定领导并上传文件。
2. 未指定领导直接上传文件。
3. 没有材料就发送“开始提炼”。
4. 成功生成建议。
5. 确认全部。
6. 部分确认。
7. 不入库。
8. 文件解析失败。

验收标准：

1. `app/README.md` 说明如何配置、启动和测试。
2. 每个失败场景不会错误写入 `profile.md`。
3. 关键路径有日志或可查看记录。

## 五、暂缓事项

以下内容暂不进入第一版：

1. 完整写稿 Agent 流程。
2. `agents/`、`templates/`、`knowledge/`、`runs/` 多层目录。
3. 数据库。
4. 网页后台。
5. 公司知识和政策知识自动沉淀。
6. OCR 和语音转文字。
7. 多用户复杂权限系统。
8. 与 OpenClaw、Hermes、LangBot 深度集成。

## 六、给执行 AI 的口令

```text
请基于 docs/wecom-learning-gateway-development-plan-v0.1.md 实现第一阶段简化原型。

执行要求：
1. 只创建 docs/app/data 三类核心目录，不创建 agents/templates/knowledge/runs/services 多层框架。
2. app 是唯一程序目录。
3. data 保存领导材料、提炼建议和确认后的 profile。
4. app/prompts/style_extraction.md 同时承担 Agent 定义和输出模板作用。
5. 企业微信接入层可以使用成熟长连接 SDK。
6. 不做完整写稿流程，不做网页后台，不做数据库。
7. AI 生成建议后必须返回用户确认，不能自动写入 profile.md。
8. 每次写入 profile.md 都要同步记录 update-log.md。
9. 完成后更新 README 和 app/README.md，说明如何配置、启动、测试。
```
