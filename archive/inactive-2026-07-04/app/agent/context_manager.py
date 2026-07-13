"""上下文管理器 - 维护会话状态"""

from dataclasses import dataclass, field
from datetime import datetime
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
