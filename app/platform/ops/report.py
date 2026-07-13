from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

from app.platform.ops.events import read_ops_events


def previous_workday(today: date) -> date:
    current = today - timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


def build_daily_report(*, target_day: date, chat_log_dir: Path, ops_events_dir: Path) -> str:
    chat_entries = _read_chat_entries(chat_log_dir, target_day)
    events = read_ops_events(ops_events_dir, target_day)

    total = len(chat_entries)
    failed = sum(1 for item in chat_entries if item.get("error"))
    needs_clarification = sum(1 for item in chat_entries if item.get("needs_clarification") and not item.get("error"))
    success = sum(
        1
        for item in chat_entries
        if not item.get("error") and not item.get("needs_clarification")
    )
    skill_counter: Counter[str] = Counter(
        str(item.get("result_skill_id") or item.get("route_skill_id") or "未识别")
        for item in chat_entries
    )
    user_counter: Counter[str] = Counter(
        str(item.get("sender_name") or item.get("sender_userid") or "unknown")
        for item in chat_entries
    )
    event_counter: Counter[str] = Counter(event.subject for event in events)

    lines = [
        f"【M-Agent 工作日报】{target_day.isoformat()}",
        "",
        "一、写作入口",
        f"- 总请求数：{total}",
        f"- 成功完成：{success}",
        f"- 需用户补充：{needs_clarification}",
        f"- 失败：{failed}",
        "",
        "二、Skill 分布",
    ]
    lines.extend(_format_counter(skill_counter))
    lines.extend(["", "三、活跃用户"])
    lines.extend(_format_counter(user_counter, empty_text="- 暂无用户记录"))
    lines.extend(["", "四、异常和需关注事项"])
    lines.extend(_format_counter(event_counter, empty_text="- 暂无运维事件"))

    error_samples = [
        item
        for item in chat_entries
        if item.get("error")
    ][:5]
    if error_samples:
        lines.extend(["", "五、失败样例"])
        for item in error_samples:
            skill = item.get("result_skill_id") or item.get("route_skill_id") or "未识别"
            user = item.get("sender_name") or item.get("sender_userid") or "unknown"
            error = str(item.get("error") or "").strip()
            lines.append(f"- {user} / {skill}：{error[:160]}")

    return "\n".join(lines)


def _read_chat_entries(root_dir: Path, target_day: date) -> list[dict[str, Any]]:
    path = root_dir / f"{target_day.strftime('%Y%m%d')}.jsonl"
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def _format_counter(counter: Counter[str], *, empty_text: str = "- 暂无记录") -> list[str]:
    if not counter:
        return [empty_text]
    return [f"- {key}：{count}" for key, count in counter.most_common()]
