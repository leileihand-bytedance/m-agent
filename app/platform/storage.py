from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from uuid import uuid4

from app.platform.models import PlatformResult


@dataclass(frozen=True)
class JobContext:
    job_id: str
    job_dir: Path
    input_dir: Path
    work_dir: Path
    output_dir: Path
    meta_path: Path


@dataclass(frozen=True)
class StoredJobResult:
    job_id: str
    job_dir: Path
    channel: str
    sender_userid: str
    sender_name: str
    created_at: str
    skill_id: str | None
    needs_clarification: bool
    message: str
    output: dict[str, object]


class JobStore:
    def __init__(self, root_dir: Path, message_preview_chars: int = 120):
        self._root_dir = root_dir
        self._message_preview_chars = message_preview_chars

    def create_job(
        self,
        *,
        channel: str,
        sender_userid: str,
        message: str,
        sender_name: str = "",
    ) -> JobContext:
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        job_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
        job_dir = self._root_dir / job_id[:4] / job_id[4:6] / job_id
        input_dir = job_dir / "input"
        work_dir = job_dir / "work"
        output_dir = job_dir / "output"
        for directory in (input_dir, work_dir, output_dir):
            directory.mkdir(parents=True, exist_ok=True)

        meta_path = job_dir / "meta.json"
        meta_path.write_text(
            json.dumps(
                {
                    "job_id": job_id,
                    "channel": channel,
                    "sender_userid": sender_userid,
                    "sender_name": sender_name or sender_userid,
                    "created_at": created_at,
                    "message_preview": self._preview(message),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        return JobContext(
            job_id=job_id,
            job_dir=job_dir,
            input_dir=input_dir,
            work_dir=work_dir,
            output_dir=output_dir,
            meta_path=meta_path,
        )

    def write_result(self, job: JobContext, result: PlatformResult) -> Path:
        result_path = job.output_dir / "result.json"
        result_path.write_text(
            json.dumps(
                {
                    "skill_id": result.skill_id,
                    "needs_clarification": result.needs_clarification,
                    "message": result.message,
                    "output": result.output,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return result_path

    def find_latest_result_for_user(
        self,
        *,
        sender_userid: str,
        channel: str | None = None,
        successful_only: bool = False,
    ) -> StoredJobResult | None:
        if not self._root_dir.exists():
            return None

        result_paths = sorted(
            self._root_dir.glob("**/output/result.json"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for result_path in result_paths:
            job_dir = result_path.parent.parent
            meta = _read_json(job_dir / "meta.json")
            result = _read_json(result_path)
            if not meta or not result:
                continue
            if str(meta.get("sender_userid", "")) != sender_userid:
                continue
            if channel is not None and str(meta.get("channel", "")) != channel:
                continue
            if successful_only:
                if result.get("needs_clarification", False) or not result.get("skill_id"):
                    continue
                output = result.get("output", {})
                if not isinstance(output, dict) or not (
                    str(output.get("title", "")).strip() or str(output.get("body", "")).strip()
                ):
                    continue
            return StoredJobResult(
                job_id=str(meta.get("job_id", job_dir.name)),
                job_dir=job_dir,
                channel=str(meta.get("channel", "")),
                sender_userid=str(meta.get("sender_userid", "")),
                sender_name=str(meta.get("sender_name", meta.get("sender_userid", ""))),
                created_at=str(meta.get("created_at", "")),
                skill_id=str(result["skill_id"]) if result.get("skill_id") is not None else None,
                needs_clarification=bool(result.get("needs_clarification", False)),
                message=str(result.get("message", "")),
                output=result.get("output", {}) if isinstance(result.get("output", {}), dict) else {},
            )
        return None

    def _preview(self, message: str) -> str:
        normalized = " ".join(message.split())
        if len(normalized) <= self._message_preview_chars:
            return normalized
        return normalized[: self._message_preview_chars] + "..."


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
