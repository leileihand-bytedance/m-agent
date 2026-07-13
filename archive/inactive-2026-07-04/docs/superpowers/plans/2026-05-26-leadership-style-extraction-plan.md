# 领导风格提炼功能实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现"开始提炼"命令、AI模型调用、建议保存和用户确认处理，完成领导风格沉淀核心闭环。

**Architecture:** 采用简单模块化设计：在 `main.py` 中新增风格提炼和确认处理函数，调用 MiniMax API 生成建议，保存到 `data/leaders/某领导/suggestions/`，用户确认后更新 `profile.md` 和 `update-log.md`。SessionStore 增加状态管理支持。

**Tech Stack:** Python 3.11+, wecom-aibot-sdk 1.0.7, MiniMax-M2.7 API (HTTP 调用), Markdown 文件存储

---

## 文件结构

```
app/
├── main.py                    # 修改: 新增风格提炼和确认处理逻辑
├── prompts/
│   └── style_extraction.md    # 已存在: AI提示词模板
├── config.example.env         # 已存在
├── requirements.txt           # 修改: 新增 anthropic SDK 或 requests
data/
└── leaders/
    └── {领导名}/
        ├── source/            # 已存在: 原始材料
        ├── suggestions/       # 新增: AI提炼建议
        ├── profile.md         # 已存在: 风格档案(确认后写入)
        └── update-log.md      # 已存在: 更新记录
```

---

## Task 1: 添加环境配置和模型调用基础函数

**Files:**
- Modify: `app/main.py:1-20` (imports)
- Modify: `app/main.py:193-201` (load_config)
- Modify: `app/requirements.txt`

- [ ] **Step 1: 添加 anthropic SDK 到 requirements.txt**

```txt
wecom-aibot-sdk==1.0.7
anthropic>=0.25.0
```

Run: `echo -e "wecom-aibot-sdk==1.0.7\nanthropic>=0.25.0" > app/requirements.txt`

- [ ] **Step 2: 安装依赖**

Run: `cd /Users/op04/Desktop/M-Agent && pip install -r app/requirements.txt -q`

- [ ] **Step 3: 在 main.py 顶部添加配置字段**

找到 `AppConfig` dataclass (约第23行)，添加 `anthropic_api_key` 和 `anthropic_base_url` 字段：

```python
@dataclass(frozen=True)
class AppConfig:
    wecom_bot_id: str
    wecom_bot_secret: str
    model_name: str
    anthropic_api_key: str
    anthropic_base_url: str
    data_dir: Path
```

- [ ] **Step 4: 更新 load_config 函数**

修改 `load_config` (约第194行)，读取新配置：

```python
def load_config(env_path: Path = DEFAULT_ENV_PATH) -> AppConfig:
    values = parse_env_file(env_path)
    return AppConfig(
        wecom_bot_id=require_value(values, "WECOM_BOT_ID"),
        wecom_bot_secret=require_value(values, "WECOM_BOT_SECRET"),
        model_name=values.get("MODEL_NAME", "MiniMax-M2.7") or "MiniMax-M2.7",
        anthropic_api_key=require_value(values, "ANTHROPIC_API_KEY"),
        anthropic_base_url=values.get("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic") or "https://api.minimaxi.com/anthropic",
        data_dir=Path(values.get("M_AGENT_DATA_DIR", "data") or "data"),
    )
```

- [ ] **Step 5: 更新 config.example.env 添加新字段说明**

在 `app/config.example.env` 末尾添加注释说明：

```env
# 注意: ANTHROPIC_API_KEY 已移到标准配置，第一版直接使用此Key调用MiniMax API
```

- [ ] **Step 6: 提交**

```bash
git add app/requirements.txt app/main.py app/config.example.env
git commit -m "feat: 添加模型调用配置支持"
```

---

## Task 2: 添加风格提炼状态管理

**Files:**
- Modify: `app/main.py:73-98` (SessionStore)

- [ ] **Step 1: 添加状态枚举和状态相关方法到 SessionStore**

在 `SessionStore` class 中添加状态管理：

```python
class SessionState(Enum):
    IDLE = "idle"
    COLLECTING = "collecting"
    EXTRACTING = "extracting"
    WAITING_CONFIRMATION = "waiting_confirmation"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
```

在 `SessionStore.__init__` 后添加：

```python
def get_state(self, user_id: str) -> SessionState:
    return self._states.get(user_id, SessionState.IDLE)

def set_state(self, user_id: str, state: SessionState) -> None:
    self._states[user_id] = state

def clear_session(self, user_id: str) -> None:
    self._leaders_by_user.pop(user_id, None)
    self._next_leaders_by_user.pop(user_id, None)
    self._pending_materials_by_user.pop(user_id, None)
    self._states.pop(user_id, None)
```

更新 `__init__` 方法添加 `self._states: dict[str, SessionState] = {}`

- [ ] **Step 2: 添加待处理建议存储**

在 `SessionStore` 中添加：

```python
def remember_suggestion(self, user_id: str, suggestion_path: Path) -> None:
    self._suggestions_by_user[user_id] = suggestion_path

def pop_suggestion(self, user_id: str) -> Path | None:
    return self._suggestions_by_user.pop(user_id, None)
```

添加 `self._suggestions_by_user: dict[str, Path] = {}` 到 `__init__`

- [ ] **Step 3: 提交**

```bash
git add app/main.py
git commit -m "feat: 添加会话状态管理和建议存储支持"
```

---

## Task 3: 实现 AI 模型调用函数

**Files:**
- Modify: `app/main.py` (新增约100行)

- [ ] **Step 1: 编写调用模型的函数**

在 `main.py` 末尾（`main()` 函数之前）添加：

```python
def build_style_extraction_prompt(leader: str, materials: list[Path], existing_profile: str | None) -> str:
    """构建发给AI的风格提炼prompt"""
    prompt_path = Path(__file__).parent / "prompts" / "style_extraction.md"
    template = prompt_path.read_text(encoding="utf-8")

    material_contents = []
    for path in materials:
        content = path.read_text(encoding="utf-8")
        material_contents.append(f"## {path.name}\n\n{content}")

    return template.format(
        leader_name=leader,
        material_sources="\n\n".join(material_contents),
        existing_profile=existing_profile or "（暂无已确认的档案）",
    )


def call_model(config: AppConfig, prompt: str) -> str:
    """调用 MiniMax API 生成风格提炼建议"""
    import anthropic

    client = anthropic.Anthropic(
        api_key=config.anthroopic_api_key,
        base_url=config.anthroopic_base_url,
    )

    message = client.messages.create(
        model=config.model_name,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text


def extract_style_suggestion(ai_output: str) -> tuple[str, list[str], list[str], list[str], list[str], list[str], list[str]]:
    """从AI输出中解析出结构化建议"""
    sections = {}
    current_section = None
    current_content = []

    for line in ai_output.split("\n"):
        if line.startswith("## "):
            if current_section:
                sections[current_section] = "\n".join(current_content).strip()
            current_section = line[3:].strip()
            current_content = []
        else:
            current_content.append(line)

    if current_section:
        sections[current_section] = "\n".join(current_content).strip()

    sources = []
    if "材料来源" in sections:
        sources = [l.strip("- ").strip() for l in sections["材料来源"].split("\n") if l.strip()]

    observations = []
    if "本次观察到的风格倾向" in sections:
        observations = [l.strip("123456789.） ").strip() for l in sections["本次观察到的风格倾向"].split("\n") if l.strip() and l[0].isdigit()]

    suggestions = []
    if "建议写入 profile.md 的内容" in sections:
        suggestions = [l.strip("123456789.） ").strip() for l in sections["建议写入 profile.md 的内容"].split("\n") if l.strip() and l[0].isdigit()]

    accepted = []
    if "建议加入常用表达" in sections:
        accepted = [l.strip("123456789.） ").strip() for l in sections["建议加入常用表达"].split("\n") if l.strip() and l[0].isdigit()]

    avoided = []
    if "建议加入慎用表达" in sections:
        avoided = [l.strip("123456789.） ").strip() for l in sections["建议加入慎用表达"].split("\n") if l.strip() and l[0].isdigit()]

    not_recommended = []
    if "不建议沉淀的内容" in sections:
        not_recommended = [l.strip("123456789.） ").strip() for l in sections["不建议沉淀的内容"].split("\n") if l.strip() and l[0].isdigit()]

    questions = []
    if "需要用户确认的问题" in sections:
        questions = [l.strip("123456789.） ").strip() for l in sections["需要用户确认的问题"].split("\n") if l.strip() and l[0].isdigit()]

    return ai_output, sources, observations, suggestions, accepted, avoided, not_recommended, questions
```

注意：上面代码中 `anthroopic_api_key` 和 `anthroopic_base_url` 拼写有误，正确应该是 `anthropic_api_key` 和 `anthropic_base_url`——需要修正。

- [ ] **Step 2: 提交**

```bash
git add app/main.py
git commit -m "feat: 实现AI模型调用函数"
```

---

## Task 4: 实现"开始提炼"命令处理

**Files:**
- Modify: `app/main.py:545-592` (on_text 函数)

- [ ] **Step 1: 编写材料收集和开始提炼逻辑**

在 `on_text` 函数中，找到处理 `intent.material` 的分支（约第572行），在其后添加"开始提炼"的处理逻辑。同时修改 `idle` 状态下的默认回复，支持"开始提炼"命令：

找到 `on_text` 函数末尾的 `else` 分支，修改为：

```python
else:
    # 检查是否是"开始提炼"命令
    text_lower = content.strip().lower()
    if text_lower in ("开始提炼", "开始提取", "提炼", "提取"):
        leader = sessions.get_leader(sender)
        if not leader:
            reply = "请先指定领导，例如：沉淀领导风格：张总"
        else:
            # 检查是否有材料
            leader_dir = data_dir / "leaders" / leader / "source"
            materials = list(leader_dir.glob("*.md")) + list(leader_dir.glob("*.parsed.md"))
            if not materials:
                reply = "当前还没有收到可分析材料，请先发送文件或文字材料。"
            else:
                # 进入提炼状态
                sessions.set_state(sender, SessionState.EXTRACTING)
                # 读取已有profile
                profile_path = data_dir / "leaders" / leader / "profile.md"
                existing_profile = profile_path.read_text(encoding="utf-8") if profile_path.exists() else None
                # 构建prompt
                prompt = build_style_extraction_prompt(leader, materials, existing_profile)
                # 调用模型
                try:
                    ai_output = call_model(config, prompt)
                    # 解析输出
                    full_output, sources, observations, suggestions, accepted, avoided, not_recommended, questions = extract_style_suggestion(ai_output)
                    # 保存建议
                    from datetime import datetime
                    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    suggestion_path = data_dir / "leaders" / leader / "suggestions" / f"{timestamp}-style-suggestion.md"
                    suggestion_path.parent.mkdir(parents=True, exist_ok=True)
                    suggestion_path.write_text(full_output, encoding="utf-8")
                    sessions.remember_suggestion(sender, suggestion_path)
                    # 生成摘要回复
                    summary = f"本次建议沉淀 {len(suggestions)} 条：\n\n"
                    for i, s in enumerate(suggestions, 1):
                        summary += f"{i}. {s}\n"
                    summary += "\n请回复：\n- 确认全部\n- 确认 1、3\n- 不入库\n- 修改第 2 条为：……"
                    reply = summary
                    sessions.set_state(sender, SessionState.WAITING_CONFIRMATION)
                except Exception as exc:
                    reply = f"本次分析失败：{exc}。材料已保存，你可以稍后回复\"重新提炼\"。"
                    sessions.set_state(sender, SessionState.FAILED)
    else:
        reply = "我现在支持：沉淀领导风格、开始提炼、确认入库。你可以发送\"沉淀领导风格：张总\"开始。"
```

- [ ] **Step 2: 更新状态转换**

在 `analyze_text_message` 返回 `TextIntent` 且 `intent.leader and not intent.material` 时，设置状态为 `COLLECTING`：

在 `on_text` 函数中，`if intent.leader and not intent.material:` 分支内添加：

```python
sessions.set_state(sender, SessionState.COLLECTING)
```

- [ ] **Step 3: 提交**

```bash
git add app/main.py
git commit -m "feat: 实现开始提炼命令处理"
```

---

## Task 5: 实现"确认"命令处理

**Files:**
- Modify: `app/main.py:545-592` (on_text 函数)

- [ ] **Step 1: 添加确认处理逻辑**

在 `on_text` 函数中，`else` 分支处理"开始提炼"之后，添加确认命令的处理逻辑：

```python
# 检查是否是确认命令
if text_lower.startswith("确认"):
    suggestion_path = sessions.pop_suggestion(sender)
    if not suggestion_path or not suggestion_path.exists():
        reply = "没有正在等待确认的建议，请先发送\"开始提炼\"。"
    else:
        # 读取建议文件
        suggestion_content = suggestion_path.read_text(encoding="utf-8")
        full_output, sources, observations, suggestions, accepted, avoided, not_recommended, questions = extract_style_suggestion(suggestion_content)
        leader = sessions.get_leader(sender)
        # 解析确认内容
        confirm_all = "确认全部" in content or "确认全部" in text_lower
        if confirm_all:
            confirmed_items = suggestions
        else:
            # 解析"确认 1、3"格式
            import re
            numbers = re.findall(r"确认\s*([0-9、，,]+)", content)
            confirmed_items = []
            if numbers:
                for part in numbers[0].split("、"):
                    part = part.strip("，,")
                    if part.isdigit():
                        idx = int(part) - 1
                        if 0 <= idx < len(suggestions):
                            confirmed_items.append(suggestions[idx])
        # 更新 profile.md
        profile_path = data_dir / "leaders" / leader / "profile.md"
        if confirmed_items:
            # 追加到profile
            update_time = datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(profile_path, "a", encoding="utf-8") as f:
                f.write(f"\n\n## 更新 {update_time}\n\n")
                for item in confirmed_items:
                    f.write(f"- {item}\n")
                f.write(f"\n来源：{', '.join(sources)}\n")
                f.write(f"确认方式：{content}\n")
        # 更新 update-log.md
        log_path = data_dir / "leaders" / leader / "update-log.md"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write(f"更新内容：{', '.join(confirmed_items) if confirmed_items else '无'}\n")
            f.write(f"来源材料：{', '.join(sources)}\n")
            f.write(f"确认方式：{content}\n")
        reply = f"已更新{leader}领导风格卡，并记录本次更新来源。"
        sessions.set_state(sender, SessionState.COMPLETED)
elif "不入库" in content or "不入库" in text_lower:
    suggestion_path = sessions.pop_suggestion(sender)
    if suggestion_path:
        reply = "已取消本次沉淀，材料已保存。"
    else:
        reply = "没有正在等待确认的建议。"
    sessions.set_state(sender, SessionState.CANCELLED)
```

注意：需要在文件顶部添加 `from enum import Enum`

- [ ] **Step 2: 添加 Enum 导入**

在 `main.py` 顶部 import 区添加：

```python
from enum import Enum
```

- [ ] **Step 3: 提交**

```bash
git add app/main.py
git commit -m "feat: 实现确认命令处理"
```

---

## Task 6: 更新 README 和测试文档

**Files:**
- Modify: `app/README.md`
- Modify: `README.md`

- [ ] **Step 1: 更新 app/README.md**

在文档末尾添加"开始提炼"和"确认"命令的说明：

```markdown
## 开始提炼测试

当已经收集了材料后，发送：

```text
开始提炼
```

预期结果：
1. 机器人回复"正在分析本次材料"
2. AI 生成风格提炼建议
3. 机器人返回建议摘要，包含编号列表
4. 等待用户确认

## 确认入库测试

收到建议后，发送：

```text
确认全部
```

或：

```text
确认 1、3
```

预期结果：
1. 机器人回复"已更新X总领导风格卡"
2. `data/leaders/某领导/profile.md` 追加新内容
3. `data/leaders/某领导/update-log.md` 追加更新记录

也可以发送：

```text
不入库
```

预期结果：不修改任何文件，但材料已保存。
```

- [ ] **Step 2: 更新根目录 README.md**

添加 Phase 4-5 功能说明：

```markdown
## 近期完成

- 企业微信长连接 ✓
- 文本消息收发 ✓
- 文件接收与解析 ✓
- 开始提炼命令 ✓ (进行中)
- 确认入库处理 ✓ (进行中)
```

- [ ] **Step 3: 提交**

```bash
git add app/README.md README.md
git commit -m "docs: 更新提炼和确认功能说明"
```

---

## Task 7: 整体测试

**Files:**
- 历史说明：原计划中的 `app/test_main.py` 已删除。后续正式测试统一放在 `tests/` 目录。

- [ ] **Step 1: 运行配置检查**

```bash
cd /Users/op04/Desktop/M-Agent && python app/main.py --check-config
```

预期：显示配置检查通过、模型名和数据目录

- [ ] **Step 2: 运行单元测试**

```bash
cd /Users/op04/Desktop/M-Agent && python -m pytest tests -q
```

预期：所有测试通过

- [ ] **Step 3: 手动验证完整流程**

1. 启动程序：`python app/main.py`
2. 企业微信发送：`沉淀领导风格：张总`
3. 企业微信发送一段文字：`今天会上强调服务小微，要注重风险控制`
4. 企业微信发送：`开始提炼`
5. 检查 `data/leaders/张总/suggestions/` 有新建议文件
6. 企业微信发送：`确认全部`
7. 检查 `data/leaders/张总/profile.md` 有更新
8. 检查 `data/leaders/张总/update-log.md` 有记录

---

## 验证清单

- [ ] 配置检查通过
- [ ] 单元测试全部通过
- [ ] "沉淀领导风格：张总" 进入收集状态
- [ ] 发送材料后保存到 `source/`
- [ ] "开始提炼" 调用 AI 模型
- [ ] 建议保存到 `suggestions/`
- [ ] "确认全部" 更新 `profile.md` 和 `update-log.md`
- [ ] "确认 1、3" 只写入指定条目
- [ ] "不入库" 不修改任何文件
- [ ] README 说明已更新
