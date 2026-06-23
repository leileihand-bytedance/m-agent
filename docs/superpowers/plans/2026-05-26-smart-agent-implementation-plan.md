# 智能体风格沉淀系统实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 M-Agent 升级为智能体，实现意图识别、上下文感知、偏好学习功能。

**Architecture:** 采用模块化智能体架构：Intent Classifier 判断消息类型 → Context Manager 维护状态 → Agent Core 执行动作 → Profile Manager 读写档案。领导标识（01-04）通过配置文件映射到具体领导。

**Tech Stack:** Python 3.11+, wecom-aibot-sdk 1.0.7, anthropic SDK, Markdown 文件存储

---

## 文件结构

```
app/
├── main.py                    # 修改: 重构为调用智能体
├── agent/
│   ├── __init__.py            # 新增: 模块导出
│   ├── intent_classifier.py   # 新增: AI意图分类
│   ├── context_manager.py     # 新增: 上下文管理
│   ├── agent_core.py          # 新增: 智能体核心
│   └── profile_manager.py     # 新增: 档案管理
├── config.py                  # 新增: 配置管理
├── prompts/
│   ├── style_extraction.md    # 已存在
│   └── intent_classify.md     # 新增: 意图分类prompt
data/
└── leader-mapping.json        # 新增: 领导映射配置
```

---

## Task 1: 创建项目骨架和领导映射配置

**Files:**
- Create: `app/agent/__init__.py`
- Create: `app/agent/intent_classifier.py`
- Create: `app/agent/context_manager.py`
- Create: `app/agent/agent_core.py`
- Create: `app/agent/profile_manager.py`
- Create: `app/config.py`
- Create: `app/prompts/intent_classify.md`
- Create: `data/leader-mapping.json`
- Modify: `data/leaders/` (确保01-04目录存在)

- [ ] **Step 1: 创建 app/agent 目录**

```bash
mkdir -p app/agent
touch app/agent/__init__.py
```

- [ ] **Step 2: 创建领导映射配置 data/leader-mapping.json**

```json
{
  "01": "张总",
  "02": "李总",
  "03": "王总",
  "04": "刘总"
}
```

Run: `cat > data/leader-mapping.json << 'EOF'
{
  "01": "张总",
  "02": "李总",
  "03": "王总",
  "04": "刘总"
}
EOF`

- [ ] **Step 3: 创建 app/agent/__init__.py**

```python
"""智能体模块"""

from .intent_classifier import IntentClassifier
from .context_manager import ContextManager
from .agent_core import AgentCore
from .profile_manager import ProfileManager

__all__ = [
    "IntentClassifier",
    "ContextManager",
    "AgentCore",
    "ProfileManager",
]
```

- [ ] **Step 4: 创建 app/config.py**

```python
"""配置管理"""

from pathlib import Path
from dataclasses import dataclass
import json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = ROOT / ".env"


@dataclass(frozen=True)
class AppConfig:
    wecom_bot_id: str
    wecom_bot_secret: str
    model_name: str
    anthropic_api_key: str
    anthropic_base_url: str
    data_dir: Path


def load_leader_mapping() -> dict[str, str]:
    """加载领导映射配置"""
    mapping_path = ROOT / "data" / "leader-mapping.json"
    if mapping_path.exists():
        return json.loads(mapping_path.read_text(encoding="utf-8"))
    return {}


def resolve_leader(identifier: str, mapping: dict[str, str]) -> str | None:
    """解析领导标识

    Args:
        identifier: 领导标识，如 "01" 或 "张总"
        mapping: 标识到名称的映射

    Returns:
        领导名称，如 "张总"，或 None
    """
    # 直接查找
    if identifier in mapping:
        return mapping[identifier]

    # 反向查找（标识可能是名称）
    for key, value in mapping.items():
        if value == identifier:
            return value

    return None
```

- [ ] **Step 5: 创建 app/prompts/intent_classify.md**

```markdown
# 意图分类提示词

你是一个消息意图分类器。请判断用户发送的消息属于哪种意图。

## 消息类型

1. **raw_material** - 原材料：描述事件、领导言论、具体场景的内容，需要分析提炼
2. **conclusion** - 结论：表达偏好、风格建议、使用"要"、"不要"、"偏好"等词的内容
3. **command** - 指令：如"开始提炼"、"确认"、"不入库"、"取消"等
4. **question** - 询问：用户提问或请求帮助
5. **unknown** - 未知：无法明确判断，需要追问

## 判断标准

**原材料特征：**
- 描述一个事件或场景
- 包含"今天"、"会上"、"领导说"等时间或人物
- 没有明确的偏好或建议词

**结论特征：**
- 使用"要"、"不要"、"应该"、"不应该"
- 表达风格偏好："材料要简洁"、"报告要有数据"
- 是建议而不是描述

**指令特征：**
- 包含"开始"、"确认"、"提炼"、"取消"等动词
- 是动作要求而不是信息

## 输出格式

请只输出一个词：raw_material / conclusion / command / question / unknown

不要输出其他内容。
```

- [ ] **Step 6: 确保 01-04 目录存在**

```bash
mkdir -p data/leaders/01/source data/leaders/01/suggestions
mkdir -p data/leaders/02/source data/leaders/02/suggestions
mkdir -p data/leaders/03/source data/leaders/03/suggestions
mkdir -p data/leaders/04/source data/leaders/04/suggestions
```

- [ ] **Step 7: 提交**

```bash
git add app/agent/ app/config.py app/prompts/intent_classify.md data/leader-mapping.json
git commit -m "feat: 创建智能体骨架和领导映射配置"
```

---

## Task 2: 实现 IntentClassifier（意图分类器）

**Files:**
- Create: `app/agent/intent_classifier.py`

- [ ] **Step 1: 编写 IntentClassifier 类**

```python
"""意图分类器 - AI判断用户消息意图"""

from pathlib import Path
from dataclasses import dataclass
from enum import Enum

import anthropic


class Intent(Enum):
    RAW_MATERIAL = "raw_material"      # 原材料
    CONCLUSION = "conclusion"           # 结论
    COMMAND = "command"                # 指令
    QUESTION = "question"               # 询问
    UNKNOWN = "unknown"                # 未知


@dataclass
class IntentResult:
    intent: Intent
    leader: str | None
    content: str | None
    confidence: float = 1.0
    reason: str = ""


COMMAND_KEYWORDS = {"开始提炼", "确认", "不入库", "取消", "提炼", "重新提炼"}
QUESTION_KEYWORDS = {"什么是", "怎么", "如何", "为什么", "?"}


class IntentClassifier:
    """意图分类器"""

    def __init__(self, api_key: str, base_url: str, model: str = "MiniMax-M2.7"):
        self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url)
        self.model = model
        self.prompt_path = Path(__file__).parent.parent / "prompts" / "intent_classify.md"
        self.prompt_template = self.prompt_path.read_text(encoding="utf-8")

    def classify(self, message: str, leader_mapping: dict[str, str]) -> IntentResult:
        """分类消息意图

        Args:
            message: 用户消息
            leader_mapping: 领导标识映射

        Returns:
            IntentResult: 意图分类结果
        """
        # 先检查是否是指令
        for keyword in COMMAND_KEYWORDS:
            if keyword in message:
                return IntentResult(
                    intent=Intent.COMMAND,
                    leader=self._extract_leader(message, leader_mapping),
                    content=message,
                    reason=f"包含指令关键词: {keyword}",
                )

        # 检查是否是询问
        for keyword in QUESTION_KEYWORDS:
            if keyword in message:
                return IntentResult(
                    intent=Intent.QUESTION,
                    leader=self._extract_leader(message, leader_mapping),
                    content=message,
                    reason=f"包含询问关键词: {keyword}",
                )

        # 调用AI分类
        return self._ai_classify(message, leader_mapping)

    def _extract_leader(self, message: str, mapping: dict[str, str]) -> str | None:
        """从消息中提取领导标识"""
        for key in mapping.keys():
            if key in message:
                return mapping[key]
        return None

    def _ai_classify(self, message: str, leader_mapping: dict[str, str]) -> IntentResult:
        """使用AI分类消息"""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=50,
                messages=[{"role": "user", "content": self.prompt_template + f"\n\n消息：{message}"}],
            )

            intent_str = response.content[0].text.strip().lower()

            # 解析AI返回的意图
            if "raw_material" in intent_str:
                intent = Intent.RAW_MATERIAL
            elif "conclusion" in intent_str:
                intent = Intent.CONCLUSION
            elif "command" in intent_str:
                intent = Intent.COMMAND
            elif "question" in intent_str:
                intent = Intent.QUESTION
            else:
                intent = Intent.UNKNOWN

            leader = self._extract_leader(message, leader_mapping)

            return IntentResult(
                intent=intent,
                leader=leader,
                content=message,
                reason="AI分类",
            )
        except Exception as e:
            # 如果AI调用失败，使用启发式规则
            return self._fallback_classify(message, leader_mapping, str(e))

    def _fallback_classify(self, message: str, mapping: dict[str, str], error: str) -> IntentResult:
        """AI调用失败时的后备分类"""
        # 启发式规则
        conclusion_indicators = ["要", "不要", "应该", "不应该", "偏好", "必须", "不得"]

        for indicator in conclusion_indicators:
            if indicator in message:
                leader = self._extract_leader(message, mapping)
                return IntentResult(
                    intent=Intent.CONCLUSION,
                    leader=leader,
                    content=message,
                    confidence=0.7,
                    reason=f"启发式: 包含'{indicator}'",
                )

        return IntentResult(
            intent=Intent.RAW_MATERIAL,
            leader=self._extract_leader(message, mapping),
            content=message,
            confidence=0.5,
            reason=f"后备分类: AI失败 ({error})",
        )
```

- [ ] **Step 2: 编写测试**

```python
# tests/test_intent_classifier.py
import pytest
from app.agent.intent_classifier import IntentClassifier, Intent, IntentResult


class TestIntentClassifier:
    def test_extract_leader_from_message(self):
        classifier = IntentClassifier("test-key", "http://test")
        mapping = {"01": "张总", "02": "李总"}

        result = classifier.classify("01 今天会上领导强调服务小微", mapping)
        assert result.leader == "张总"

    def test_command_intent(self):
        classifier = IntentClassifier("test-key", "http://test")
        mapping = {"01": "张总"}

        result = classifier.classify("01 开始提炼", mapping)
        assert result.intent == Intent.COMMAND

    def test_conclusion_intent(self):
        classifier = IntentClassifier("test-key", "http://test")
        mapping = {}

        # 启发式判断：包含"要"
        result = classifier._fallback_classify("材料要有数据", mapping, "test")
        assert result.intent == Intent.CONCLUSION
```

- [ ] **Step 3: 验证导入**

```bash
cd /Users/op04/Desktop/M-Agent && python -c "from app.agent import IntentClassifier; print('OK')"
```

- [ ] **Step 4: 提交**

```bash
git add app/agent/intent_classifier.py tests/test_intent_classifier.py
git commit -m "feat: 实现IntentClassifier"
```

---

## Task 3: 实现 ContextManager（上下文管理器）

**Files:**
- Create: `app/agent/context_manager.py`

- [ ] **Step 1: 编写 ContextManager 类**

```python
"""上下文管理器 - 维护会话状态"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from enum import Enum


class SessionState(Enum):
    IDLE = "idle"                      # 空闲
    AWAITING_LEADER = "awaiting_leader"  # 等待指定领导
    COLLECTING = "collecting"          # 收集材料
    EXTRACTING = "extracting"          # 提炼中
    WAITING_CONFIRM = "waiting_confirm"  # 等待确认


@dataclass
class InteractionRecord:
    timestamp: datetime
    user_message: str
    ai_response: str
    intent: str
    leader: str | None


@dataclass
class ContextManager:
    """上下文管理器"""
    _current_leader: str | None = None
    _state: SessionState = SessionState.IDLE
    _pending_content: str | None = None
    _interaction_history: list[InteractionRecord] = field(default_factory=list)
    _pending_suggestion_path: str | None = None

    def set_leader(self, leader: str) -> None:
        """设置当前领导"""
        self._current_leader = leader

    def get_leader(self) -> str | None:
        """获取当前领导"""
        return self._current_leader

    def set_state(self, state: SessionState) -> None:
        """设置状态"""
        self._state = state

    def get_state(self) -> SessionState:
        """获取状态"""
        return self._state

    def set_pending_content(self, content: str | None) -> None:
        """设置待处理内容"""
        self._pending_content = content

    def get_pending_content(self) -> str | None:
        """获取待处理内容"""
        return self._pending_content

    def add_interaction(self, user_message: str, ai_response: str, intent: str, leader: str | None) -> None:
        """添加交互记录"""
        record = InteractionRecord(
            timestamp=datetime.now(),
            user_message=user_message,
            ai_response=ai_response,
            intent=intent,
            leader=leader,
        )
        self._interaction_history.append(record)
        # 只保留最近10条记录
        if len(self._interaction_history) > 10:
            self._interaction_history = self._interaction_history[-10:]

    def get_recent_leader(self) -> str | None:
        """获取最近处理过的领导"""
        for record in reversed(self._interaction_history):
            if record.leader:
                return record.leader
        return None

    def set_pending_suggestion(self, path: str | None) -> None:
        """设置待确认的建议文件路径"""
        self._pending_suggestion_path = path

    def get_pending_suggestion(self) -> str | None:
        """获取待确认的建议文件路径"""
        return self._pending_suggestion_path

    def clear(self) -> None:
        """清除上下文"""
        self._current_leader = None
        self._state = SessionState.IDLE
        self._pending_content = None
        self._pending_suggestion_path = None
```

- [ ] **Step 2: 编写测试**

```python
# tests/test_context_manager.py
import pytest
from app.agent.context_manager import ContextManager, SessionState


class TestContextManager:
    def test_set_and_get_leader(self):
        ctx = ContextManager()
        ctx.set_leader("张总")
        assert ctx.get_leader() == "张总"

    def test_state_transitions(self):
        ctx = ContextManager()
        ctx.set_state(SessionState.COLLECTING)
        assert ctx.get_state() == SessionState.COLLECTING

    def test_recent_leader_from_history(self):
        ctx = ContextManager()
        ctx.add_interaction("test", "response", "material", "张总")
        assert ctx.get_recent_leader() == "张总"
```

- [ ] **Step 3: 验证导入**

```bash
cd /Users/op04/Desktop/M-Agent && python -c "from app.agent import ContextManager; print('OK')"
```

- [ ] **Step 4: 提交**

```bash
git add app/agent/context_manager.py tests/test_context_manager.py
git commit -m "feat: 实现ContextManager"
```

---

## Task 4: 实现 ProfileManager（档案管理器）

**Files:**
- Create: `app/agent/profile_manager.py`

- [ ] **Step 1: 编写 ProfileManager 类**

```python
"""档案管理器 - 管理领导风格档案的读写"""

from pathlib import Path
from dataclasses import dataclass
import json
from datetime import datetime


@dataclass
class ProfileEntry:
    """档案条目"""
    content: str
    source: str
    confirmed_at: str


@dataclass
class RejectedPattern:
    """被拒绝的模式"""
    pattern: str
    reason: str
    rejected_at: str


class ProfileManager:
    """档案管理器"""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def get_leader_dir(self, leader: str) -> Path:
        """获取领导目录"""
        return self.data_dir / "leaders" / leader

    def ensure_leader_dir(self, leader: str) -> Path:
        """确保领导目录存在"""
        leader_dir = self.get_leader_dir(leader)
        leader_dir.mkdir(parents=True, exist_ok=True)
        return leader_dir

    def write_to_profile(self, leader: str, content: str, source: str) -> bool:
        """写入档案

        Args:
            leader: 领导名称
            content: 要写入的内容
            source: 来源材料

        Returns:
            bool: 是否成功
        """
        try:
            leader_dir = self.ensure_leader_dir(leader)
            profile_path = leader_dir / "profile.md"

            update_time = datetime.now().strftime("%Y-%m-%d %H:%M")

            with open(profile_path, "a", encoding="utf-8") as f:
                f.write(f"\n\n## 更新 {update_time}\n\n")
                f.write(f"- {content}\n")
                f.write(f"\n来源：{source}\n")

            # 同时更新 update-log.md
            self._append_update_log(leader, content, source, "直接写入")

            return True
        except Exception:
            return False

    def _append_update_log(self, leader: str, content: str, source: str, confirm_type: str) -> None:
        """追加更新日志"""
        leader_dir = self.get_leader_dir(leader)
        log_path = leader_dir / "update-log.md"
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M")

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n## {update_time}\n\n")
            f.write(f"更新内容：{content}\n")
            f.write(f"来源材料：{source}\n")
            f.write(f"确认方式：{confirm_type}\n")

    def save_suggestion(self, leader: str, content: str) -> Path:
        """保存AI建议

        Args:
            leader: 领导名称
            content: 建议内容

        Returns:
            Path: 建议文件路径
        """
        leader_dir = self.ensure_leader_dir(leader)
        suggestions_dir = leader_dir / "suggestions"
        suggestions_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        suggestion_path = suggestions_dir / f"{timestamp}-style-suggestion.md"
        suggestion_path.write_text(content, encoding="utf-8")

        return suggestion_path

    def load_preferences(self, leader: str) -> dict:
        """加载偏好设置

        Returns:
            dict: 包含 rejected_patterns 等
        """
        leader_dir = self.get_leader_dir(leader)
        prefs_path = leader_dir / "memory" / "preferences.json"

        if prefs_path.exists():
            return json.loads(prefs_path.read_text(encoding="utf-8"))

        return {"rejected_patterns": [], "rejected_suggestions": []}

    def save_preferences(self, leader: str, preferences: dict) -> bool:
        """保存偏好设置"""
        try:
            leader_dir = self.ensure_leader_dir(leader)
            memory_dir = leader_dir / "memory"
            memory_dir.mkdir(exist_ok=True)

            prefs_path = memory_dir / "preferences.json"
            prefs_path.write_text(json.dumps(preferences, ensure_ascii=False, indent=2), encoding="utf-8")

            return True
        except Exception:
            return False

    def record_rejection(self, leader: str, suggestion: str, reason: str = "") -> None:
        """记录被拒绝的建议"""
        prefs = self.load_preferences(leader)

        if "rejected_suggestions" not in prefs:
            prefs["rejected_suggestions"] = []

        prefs["rejected_suggestions"].append({
            "content": suggestion,
            "reason": reason,
            "rejected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })

        self.save_preferences(leader, prefs)

    def get_rejected_patterns(self, leader: str) -> list[str]:
        """获取被拒绝的模式列表"""
        prefs = self.load_preferences(leader)
        return prefs.get("rejected_patterns", [])
```

- [ ] **Step 2: 编写测试**

```python
# tests/test_profile_manager.py
import pytest
import tempfile
from pathlib import Path
from app.agent.profile_manager import ProfileManager


class TestProfileManager:
    def test_write_to_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            pm = ProfileManager(Path(tmp))
            result = pm.write_to_profile("张总", "材料要有数据", "用户直接提供")
            assert result is True

            leader_dir = Path(tmp) / "leaders" / "张总"
            profile_path = leader_dir / "profile.md"
            assert profile_path.exists()

    def test_save_and_load_preferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            pm = ProfileManager(Path(tmp))
            pm.save_preferences("张总", {"rejected_patterns": ["过于乐观"]})

            prefs = pm.load_preferences("张总")
            assert "过于乐观" in prefs["rejected_patterns"]
```

- [ ] **Step 3: 验证导入**

```bash
cd /Users/op04/Desktop/M-Agent && python -c "from app.agent import ProfileManager; print('OK')"
```

- [ ] **Step 4: 提交**

```bash
git add app/agent/profile_manager.py tests/test_profile_manager.py
git commit -m "feat: 实现ProfileManager"
```

---

## Task 5: 实现 AgentCore（智能体核心）

**Files:**
- Create: `app/agent/agent_core.py`
- Modify: `app/main.py` (重构为调用智能体)

- [ ] **Step 1: 编写 AgentCore 类**

```python
"""智能体核心 - 协调各模块工作"""

from pathlib import Path
from dataclasses import dataclass

from .intent_classifier import IntentClassifier, Intent, IntentResult
from .context_manager import ContextManager, SessionState
from .profile_manager import ProfileManager
from ..config import AppConfig, load_leader_mapping, resolve_leader


@dataclass
class AgentResponse:
    """智能体响应"""
    message: str  # 回复给用户的消息
    action: str    # 执行的动作
    success: bool
    leader: str | None = None


class AgentCore:
    """智能体核心"""

    def __init__(self, config: AppConfig):
        self.config = config
        self.leader_mapping = load_leader_mapping()

        self.intent_classifier = IntentClassifier(
            api_key=config.anthropic_api_key,
            base_url=config.anthropic_base_url,
            model=config.model_name,
        )

        self.context_manager = ContextManager()
        self.profile_manager = ProfileManager(config.data_dir)

    def process(self, user_message: str, sender: str) -> AgentResponse:
        """处理用户消息

        Args:
            user_message: 用户发送的消息
            sender: 发送者ID

        Returns:
            AgentResponse: 智能体响应
        """
        # 分类消息意图
        intent_result = self.intent_classifier.classify(user_message, self.leader_mapping)

        # 记录交互
        self.context_manager.add_interaction(
            user_message=user_message,
            ai_response="",
            intent=intent_result.intent.value,
            leader=intent_result.leader,
        )

        # 根据意图处理
        if intent_result.intent == Intent.RAW_MATERIAL:
            return self._handle_raw_material(intent_result)

        elif intent_result.intent == Intent.CONCLUSION:
            return self._handle_conclusion(intent_result)

        elif intent_result.intent == Intent.COMMAND:
            return self._handle_command(intent_result)

        elif intent_result.intent == Intent.QUESTION:
            return AgentResponse(
                message="我可以帮你提炼领导风格。直接发送材料或文件即可。",
                action="answer_question",
                success=True,
            )

        else:
            return AgentResponse(
                message="我没有理解你的意思。请发送材料、文件或使用指令（如：开始提炼、确认）。",
                action="ask_clarify",
                success=False,
            )

    def _handle_raw_material(self, intent_result: IntentResult) -> AgentResponse:
        """处理原材料"""
        leader = intent_result.leader

        # 如果没有指定领导，检查上下文
        if not leader:
            leader = self.context_manager.get_recent_leader()

        # 如果还是没有，询问用户
        if not leader:
            self.context_manager.set_state(SessionState.AWAITING_LEADER)
            self.context_manager.set_pending_content(intent_result.content)
            return AgentResponse(
                message="这是谁的风格？请回复（如：01）",
                action="ask_leader",
                success=True,
                leader=None,
            )

        # 保存材料
        leader_dir = self.profile_manager.ensure_leader_dir(leader)
        source_dir = leader_dir / "source"
        source_dir.mkdir(exist_ok=True)

        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        material_path = source_dir / f"{timestamp}-material.md"
        material_path.write_text(intent_result.content, encoding="utf-8")

        # 更新上下文
        self.context_manager.set_leader(leader)
        self.context_manager.set_state(SessionState.COLLECTING)
        self.context_manager.set_pending_content(intent_result.content)

        return AgentResponse(
            message=f"已收到"{leader}"的材料。\n要现在提炼吗？发送\"开始提炼\"即可。",
            action="store_material",
            success=True,
            leader=leader,
        )

    def _handle_conclusion(self, intent_result: IntentResult) -> AgentResponse:
        """处理结论（直接写入类容）"""
        leader = intent_result.leader

        if not leader:
            leader = self.context_manager.get_recent_leader()

        if not leader:
            self.context_manager.set_state(SessionState.AWAITING_LEADER)
            self.context_manager.set_pending_content(intent_result.content)
            return AgentResponse(
                message="这是谁的风格？请回复（如：01）",
                action="ask_leader",
                success=True,
                leader=None,
            )

        # 直接写入
        success = self.profile_manager.write_to_profile(
            leader=leader,
            content=intent_result.content,
            source="用户直接提供",
        )

        if success:
            self.context_manager.set_leader(leader)
            return AgentResponse(
                message=f"已写入"{leader}"档案：\n- {intent_result.content}",
                action="write_profile",
                success=True,
                leader=leader,
            )
        else:
            return AgentResponse(
                message="写入失败，请稍后重试。",
                action="write_profile",
                success=False,
                leader=leader,
            )

    def _handle_command(self, intent_result: IntentResult) -> AgentResponse:
        """处理指令"""
        message = intent_result.content.lower()

        # 提取领导
        leader = intent_result.leader
        if not leader:
            leader = self.context_manager.get_leader()

        if not leader:
            return AgentResponse(
                message="请先指定领导，例如：01",
                action="ask_leader",
                success=False,
                leader=None,
            )

        # 处理具体指令
        if "开始提炼" in message or "提炼" in message:
            return self._handle_start_extraction(leader)

        elif "确认" in message:
            return self._handle_confirmation(intent_result.content, leader)

        elif "不入库" in message:
            return self._handle_rejection(leader)

        elif "取消" in message:
            self.context_manager.clear()
            return AgentResponse(
                message="已取消当前操作。",
                action="cancel",
                success=True,
                leader=leader,
            )

        else:
            return AgentResponse(
                message="我理解这是一个指令，但不知道要做什么。请使用：开始提炼、确认、不入库。",
                action="unknown_command",
                success=False,
                leader=leader,
            )

    def _handle_start_extraction(self, leader: str) -> AgentResponse:
        """处理开始提炼"""
        leader_dir = self.profile_manager.get_leader_dir(leader)
        materials = list(leader_dir.glob("source/*.md"))

        if not materials:
            return AgentResponse(
                message="还没有材料，请先发送材料。",
                action="check_materials",
                success=False,
                leader=leader,
            )

        # 读取已有profile和偏好
        profile_path = leader_dir / "profile.md"
        existing_profile = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
        preferences = self.profile_manager.load_preferences(leader)

        # 构建prompt并调用AI
        prompt = self._build_extraction_prompt(leader, materials, existing_profile, preferences)

        try:
            from ..main import call_model
            ai_output = call_model(self.config, prompt)

            # 保存建议
            suggestion_path = self.profile_manager.save_suggestion(leader, ai_output)
            self.context_manager.set_pending_suggestion(str(suggestion_path))
            self.context_manager.set_state(SessionState.WAITING_CONFIRM)

            # 解析建议并生成摘要
            from ..main import extract_style_suggestion
            _, _, suggestions, _, _, _, _ = extract_style_suggestion(ai_output)

            summary = f"为"{leader}"提炼了 {len(suggestions)} 条建议：\n\n"
            for i, s in enumerate(suggestions, 1):
                summary += f"{i}. {s}\n"

            summary += "\n请回复：\n- 确认全部\n- 确认 1、3\n- 不入库"

            return AgentResponse(
                message=summary,
                action="extraction_complete",
                success=True,
                leader=leader,
            )

        except Exception as e:
            return AgentResponse(
                message=f"提炼失败：{e}",
                action="extraction_failed",
                success=False,
                leader=leader,
            )

    def _handle_confirmation(self, content: str, leader: str) -> AgentResponse:
        """处理确认"""
        suggestion_path = self.context_manager.get_pending_suggestion()

        if not suggestion_path or not Path(suggestion_path).exists():
            return AgentResponse(
                message="没有正在等待确认的建议，请先发送\"开始提炼\"。",
                action="check_pending",
                success=False,
                leader=leader,
            )

        # 读取建议
        suggestion_content = Path(suggestion_path).read_text(encoding="utf-8")

        from ..main import extract_style_suggestion
        _, _, suggestions, _, _, _, _ = extract_style_suggestion(suggestion_content)

        # 解析确认内容
        if "确认全部" in content:
            confirmed = suggestions
        else:
            import re
            numbers = re.findall(r"确认\s*([0-9、，,]+)", content)
            confirmed = []
            if numbers:
                for part in numbers[0].split("、"):
                    part = part.strip("，,")
                    if part.isdigit():
                        idx = int(part) - 1
                        if 0 <= idx < len(suggestions):
                            confirmed.append(suggestions[idx])

        # 写入profile
        for item in confirmed:
            self.profile_manager.write_to_profile(leader, item, Path(suggestion_path).name)

        self.context_manager.set_state(SessionState.IDLE)
        self.context_manager.set_pending_suggestion(None)

        return AgentResponse(
            message=f"已更新"{leader}"档案，{len(confirmed)} 条建议已写入。",
            action="confirm_write",
            success=True,
            leader=leader,
        )

    def _handle_rejection(self, leader: str) -> AgentResponse:
        """处理拒绝"""
        suggestion_path = self.context_manager.get_pending_suggestion()

        if suggestion_path and Path(suggestion_path).exists():
            # 记录被拒绝的建议
            suggestion_content = Path(suggestion_path).read_text(encoding="utf-8")
            self.profile_manager.record_rejection(leader, suggestion_content)

        self.context_manager.set_state(SessionState.IDLE)
        self.context_manager.set_pending_suggestion(None)

        return AgentResponse(
            message="已取消本次沉淀，材料已保存。",
            action="reject",
            success=True,
            leader=leader,
        )

    def _build_extraction_prompt(
        self,
        leader: str,
        materials: list[Path],
        existing_profile: str,
        preferences: dict,
    ) -> str:
        """构建提炼prompt"""
        prompt_template = (Path(__file__).parent.parent / "prompts" / "style_extraction.md").read_text(encoding="utf-8")

        material_contents = []
        for path in materials:
            content = path.read_text(encoding="utf-8")
            material_contents.append(f"## {path.name}\n\n{content}")

        # 添加避免模式
        avoid_instruction = ""
        if preferences.get("rejected_suggestions"):
            avoid_instruction = "\n\n## 避免以下已被用户拒绝的建议：\n"
            for item in preferences["rejected_suggestions"][-5:]:  # 只取最近5条
                avoid_instruction += f"- {item['content']}\n"

        return prompt_template.format(
            leader_name=leader,
            material_sources="\n\n".join(material_contents),
            existing_profile=existing_profile or "（暂无已确认的档案）",
        ) + avoid_instruction
```

- [ ] **Step 2: 验证导入**

```bash
cd /Users/op04/Desktop/M-Agent && python -c "from app.agent import AgentCore; print('OK')"
```

- [ ] **Step 3: 提交**

```bash
git add app/agent/agent_core.py
git commit -m "feat: 实现AgentCore"
```

---

## Task 6: 重构 main.py 调用智能体

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: 替换 on_text 函数**

找到 `on_text` 函数（约545行），替换为：

```python
# 导入智能体
from agent import AgentCore

# 初始化智能体（延迟初始化，避免循环导入）
_agent_core: AgentCore | None = None

def get_agent_core() -> AgentCore:
    global _agent_core
    if _agent_core is None:
        _agent_core = AgentCore(config)
    return _agent_core

async def on_text(frame):
    content = frame.get("body", {}).get("text", {}).get("content", "")
    sender = get_sender_id(frame)

    # 使用智能体处理
    agent = get_agent_core()
    response = agent.process(content, sender)

    stream_id = generate_req_id("m-agent")
    await ws_client.reply_stream(frame, stream_id, response.message, True)
    print(f"已回复：{response.action} - {response.message[:50]}...", flush=True)
```

- [ ] **Step 2: 提交**

```bash
git add app/main.py
git commit -m "refactor: 重构为使用智能体架构"
```

---

## Task 7: 整体测试

**Files:**
- Test: `app/test_agent.py`

- [ ] **Step 1: 编写集成测试**

```python
# tests/test_agent_integration.py
import pytest
from pathlib import Path
from app.agent import AgentCore, IntentClassifier, ContextManager, ProfileManager
from app.config import AppConfig, load_leader_mapping


class TestAgentIntegration:
    def test_intent_classification(self):
        config = AppConfig(
            wecom_bot_id="test",
            wecom_bot_secret="test",
            model_name="MiniMax-M2.7",
            anthropic_api_key="test",
            anthropic_base_url="http://test",
            data_dir=Path("data"),
        )

        classifier = IntentClassifier(config.anthropic_api_key, config.anthropic_base_url)
        mapping = load_leader_mapping()

        # 测试原材料分类
        result = classifier.classify("01 今天会上领导强调服务小微", mapping)
        assert result.leader == "张总"

        # 测试结论分类
        result = classifier.classify("材料要有数据", mapping)
        # 后备分类应该识别为结论
        assert result.intent.value in ["conclusion", "raw_material"]
```

- [ ] **Step 2: 运行测试**

```bash
cd /Users/op04/Desktop/M-Agent && python -m pytest tests/ -v
```

- [ ] **Step 3: 手动验证**

```bash
python app/main.py
```

企业微信发送测试消息，验证各场景。

---

## 验证清单

- [ ] IntentClassifier 能正确分类消息意图
- [ ] ContextManager 能维护会话状态
- [ ] ProfileManager 能读写档案和学习偏好
- [ ] AgentCore 能协调各模块工作
- [ ] main.py 能调用智能体
- [ ] 领导映射配置正确
- [ ] 01-04 目录结构正确