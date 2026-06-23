"""档案管理器 - 管理领导风格档案的读写"""

from pathlib import Path
from dataclasses import dataclass
import json
from datetime import datetime


@dataclass
class ProfileEntry:
    content: str
    source: str
    confirmed_at: str


@dataclass
class RejectedPattern:
    pattern: str
    reason: str
    rejected_at: str


class ProfileManager:
    """档案管理器"""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def get_leader_dir(self, leader: str) -> Path:
        return self.data_dir / "leaders" / leader

    def ensure_leader_dir(self, leader: str) -> Path:
        leader_dir = self.get_leader_dir(leader)
        leader_dir.mkdir(parents=True, exist_ok=True)
        return leader_dir

    def write_to_profile(self, leader: str, content: str, source: str) -> bool:
        try:
            leader_dir = self.ensure_leader_dir(leader)
            profile_path = leader_dir / "profile.md"
            update_time = datetime.now().strftime("%Y-%m-%d %H:%M")

            with open(profile_path, "a", encoding="utf-8") as f:
                f.write(f"\n\n## 更新 {update_time}\n\n")
                f.write(f"- {content}\n")
                f.write(f"\n来源：{source}\n")

            self._append_update_log(leader, content, source, "直接写入")
            return True
        except Exception:
            return False

    def _append_update_log(self, leader: str, content: str, source: str, confirm_type: str) -> None:
        leader_dir = self.get_leader_dir(leader)
        log_path = leader_dir / "update-log.md"
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M")

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n## {update_time}\n\n")
            f.write(f"更新内容：{content}\n")
            f.write(f"来源材料：{source}\n")
            f.write(f"确认方式：{confirm_type}\n")

    def save_suggestion(self, leader: str, content: str) -> Path:
        leader_dir = self.ensure_leader_dir(leader)
        suggestions_dir = leader_dir / "suggestions"
        suggestions_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        suggestion_path = suggestions_dir / f"{timestamp}-style-suggestion.md"
        suggestion_path.write_text(content, encoding="utf-8")
        return suggestion_path

    def load_preferences(self, leader: str) -> dict:
        leader_dir = self.get_leader_dir(leader)
        prefs_path = leader_dir / "memory" / "preferences.json"

        if prefs_path.exists():
            return json.loads(prefs_path.read_text(encoding="utf-8"))
        return {"rejected_patterns": [], "rejected_suggestions": []}

    def save_preferences(self, leader: str, preferences: dict) -> bool:
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
        prefs = self.load_preferences(leader)
        return prefs.get("rejected_patterns", [])