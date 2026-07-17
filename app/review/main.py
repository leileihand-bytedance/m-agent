"""智能审核 Bot 入口 (独立进程).

功能:
  - 接入企业微信长连接(独立 Bot,只做审核)
  - 接收文件消息 → 检查后缀(.docx/.html/.htm/.pptx)
  - Word、静态 HTML 和 PPTX 分别走各自登记的审核流程
  - 存档到 M-Agent-Files/tasks/review/YYYY/MM/<日期-序号>/
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from time import perf_counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping
from uuid import uuid4

# 让 import app.* 找得到
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from app.review.bot_logging import setup_logging, redirect_stdout_to_logging, log_extra
from app.review.notification import AdminNotifier, NotificationConfig
from app.review.user_registry import UserRegistry, RegistrationFlow
from app.review import load_rules, format_review_result  # noqa: E402
from app.review.reviewer import ReviewResult, Finding  # noqa: E402
from app.review.document_type import detect_document_type, DocumentType, document_type_label  # noqa: E402
from app.review.intake import ReviewIntakeStore, is_format_review_request  # noqa: E402
from app.platform.models import UploadedFile  # noqa: E402
from app.platform.attachment_delivery import (  # noqa: E402
    AttachmentDelivery,
    AttachmentDeliveryConfig,
    DeliveryRequest,
)
from app.platform.ops.events import OpsEventLogger  # noqa: E402
from app.platform.ops.heartbeat import write_heartbeat  # noqa: E402
from app.platform.data_paths import DataPaths, configured_path  # noqa: E402
from app.platform.runtime_environment import (  # noqa: E402
    RuntimeEnvironment,
    RuntimeEnvironmentError,
    bot_credentials,
    prepare_runtime_environment,
    validate_bot_startup,
)
from app.platform.gateway.wecom import extract_message_id  # noqa: E402
from app.platform.task_status import write_task_status  # noqa: E402
from app.platform.task_execution import (  # noqa: E402
    ClaimLimits,
    PersistentTaskExecutor,
    TaskLifecycleObserver,
    TaskRepository,
)
from app.review.task_execution import (  # noqa: E402
    GENERAL_HTML_REVIEW_TASK_TYPE,
    GENERAL_TEXT_REVIEW_TASK_TYPE,
    GENERAL_REVIEW_COST_CLASS,
    GENERAL_REVIEW_TASK_TYPE,
    HALF_MONTHLY_REVIEW_TASK_TYPE,
    NEICAN_REVIEW_TASK_TYPE,
    OFFICIAL_FORMAT_REVIEW_TASK_TYPE,
    PPT_REVIEW_TASK_TYPE,
    REVIEW_TASK_TYPES,
    GeneralReviewTaskService,
    GeneralReviewWorkspace,
    PreparedReviewDelivery,
)


# 全局 logger,在 main() 中初始化
logger = logging.getLogger("review_bot")
_DEFAULT_DATA_PATHS = DataPaths.from_values({}, project_root=_ROOT)


class ReviewDeliveryStatusUncertain(RuntimeError):
    """企业微信发送已发起，但回执不足以判断用户是否收到。"""

_FOLLOWUP_REVIEW_REQUEST_RE = re.compile(
    r"^(?:也|再)?(?:请|麻烦)?(?:帮我)?(?:(?:审(?:核)?|看)(?:一下|下)?(?:这个|这份|该)?(?:材料|文件|文档|附件)?|"
    r"(?:做|进行)(?:一下|下)?(?:文字|内容)(?:审(?:核)?|审查|检查|校对)|"
    r"看看(?:(?:这个|这份|该)?(?:材料|文件|文档|附件))?(?:有无|有没有)?问题|"
    r"看(?:(?:这个|这份|该)?(?:材料|文件|文档|附件))?(?:有无|有没有)?问题)$"
)
_ACK_SMALLTALK_RE = re.compile(r"^(?:好|好的|收到|知道了|明白了|行|行的|ok|okay|ok了|嗯|嗯嗯)$", re.IGNORECASE)
_THANKS_SMALLTALK_RE = re.compile(r"^(?:谢谢|谢谢你|谢谢啦|谢谢哈|多谢|辛苦了)$")
# ============================================================
# 配置加载
# ============================================================

@dataclass(frozen=True)
class ReviewConfig:
    wecom_bot_id: str
    wecom_bot_secret: str
    rules_path: Path
    reviews_dir: Path
    logs_dir: Path
    admin_user_id: str
    admin_name: str
    notification_cooldown: int
    direct_admin_notifications: bool
    require_registration: bool
    ops_events_dir: Path = _DEFAULT_DATA_PATHS.ops_events
    ops_heartbeat_dir: Path = _DEFAULT_DATA_PATHS.heartbeats
    user_registry_path: Path = _DEFAULT_DATA_PATHS.user_registry
    intake_dir: Path = _DEFAULT_DATA_PATHS.intake / "review"
    intake_ttl_seconds: int = 1800
    auto_batch_seconds: float = 8.0
    log_max_bytes: int = 20 * 1024 * 1024
    max_file_size_mb: int = 10
    reply_ack_timeout_seconds: float = 30.0
    task_queue_db: Path = _DEFAULT_DATA_PATHS.task_queue_db.parent / "review.sqlite3"
    task_worker_count: int = 1
    task_poll_seconds: float = 0.25
    task_recovery_seconds: float = 5.0
    task_lease_seconds: int = 120
    runtime_mode: str = "production"
    data_root: Path | None = None


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def require_value(values: Mapping[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise ValueError(f"Missing required config: {key}")
    return value


def _env_int(values: Mapping[str, str], key: str, default: int) -> int:
    """读取整数环境变量,失败返回默认值."""
    try:
        return int(values.get(key, "").strip() or default)
    except ValueError:
        return default


def _env_float(values: Mapping[str, str], key: str, default: float) -> float:
    """读取浮点数环境变量,失败返回默认值."""
    try:
        return float(values.get(key, "").strip() or default)
    except ValueError:
        return default


def _env_bool(values: Mapping[str, str], key: str, default: bool) -> bool:
    """读取布尔环境变量."""
    value = (values.get(key, "").strip()).lower()
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off", ""):
        return default
    return default


def load_config(env_path: Path | None = None) -> ReviewConfig:
    if env_path is None:
        env_path = _ROOT / ".env"
    runtime = prepare_runtime_environment(parse_env_file(env_path), project_root=_ROOT)
    values = runtime.values
    bot_id, bot_secret = bot_credentials(
        runtime,
        production_keys=("WECOM_REVIEW_BOT_ID", "WECOM_REVIEW_BOT_SECRET"),
        test_keys=("M_AGENT_TEST_REVIEW_BOT_ID", "M_AGENT_TEST_REVIEW_BOT_SECRET"),
    )
    if runtime.mode == "production":
        bot_id = require_value(values, "WECOM_REVIEW_BOT_ID")
        bot_secret = require_value(values, "WECOM_REVIEW_BOT_SECRET")
    data_paths = DataPaths.from_values(values, project_root=_ROOT)

    rules_path = Path(values.get("M_AGENT_REVIEW_RULES", "app/data/rules.md") or "app/data/rules.md")
    if not rules_path.is_absolute():
        rules_path = _ROOT / rules_path

    reviews_dir = configured_path(
        values, "M_AGENT_REVIEWS_DIR", data_paths.review_tasks, project_root=_ROOT
    )
    logs_dir = configured_path(
        values, "M_AGENT_LOGS_DIR", data_paths.logs, project_root=_ROOT
    )
    ops_events_dir = configured_path(
        values, "M_AGENT_OPS_EVENTS_DIR", data_paths.ops_events, project_root=_ROOT
    )
    ops_heartbeat_dir = configured_path(
        values, "M_AGENT_OPS_HEARTBEAT_DIR", data_paths.heartbeats, project_root=_ROOT
    )
    user_registry_path = configured_path(
        values, "M_AGENT_USER_REGISTRY_PATH", data_paths.user_registry, project_root=_ROOT
    )
    intake_dir = configured_path(
        values,
        "M_AGENT_REVIEW_INTAKE_DIR",
        data_paths.intake / "review",
        project_root=_ROOT,
    )
    task_queue_db = configured_path(
        values,
        "M_AGENT_REVIEW_TASK_DB",
        data_paths.task_queue_db.parent / "review.sqlite3",
        project_root=_ROOT,
    )

    return ReviewConfig(
        wecom_bot_id=bot_id,
        wecom_bot_secret=bot_secret,
        rules_path=rules_path,
        reviews_dir=reviews_dir,
        logs_dir=logs_dir,
        ops_events_dir=ops_events_dir,
        ops_heartbeat_dir=ops_heartbeat_dir,
        user_registry_path=user_registry_path,
        intake_dir=intake_dir,
        intake_ttl_seconds=max(60, _env_int(values, "M_AGENT_REVIEW_INTAKE_TTL", 1800)),
        auto_batch_seconds=max(
            1.0,
            _env_float(values, "M_AGENT_REVIEW_AUTO_BATCH_SECONDS", 8.0),
        ),
        log_max_bytes=max(1, _env_int(values, "M_AGENT_LOG_MAX_MB", 20)) * 1024 * 1024,
        admin_user_id=values.get("REVIEW_ADMIN_USER_ID", "").strip(),
        admin_name=values.get("REVIEW_ADMIN_NAME", "").strip() or "管理员",
        notification_cooldown=_env_int(values, "REVIEW_NOTIFICATION_COOLDOWN", 300),
        direct_admin_notifications=_env_bool(values, "REVIEW_DIRECT_ADMIN_NOTIFY", False),
        require_registration=_env_bool(values, "REVIEW_REQUIRE_REGISTRATION", False),
        reply_ack_timeout_seconds=max(
            5.0,
            _env_float(values, "REVIEW_REPLY_ACK_TIMEOUT_SECONDS", 30.0),
        ),
        task_queue_db=task_queue_db,
        task_worker_count=max(1, _env_int(values, "M_AGENT_REVIEW_TASK_WORKERS", 1)),
        task_poll_seconds=max(
            0.05,
            _env_float(values, "M_AGENT_REVIEW_TASK_POLL_SECONDS", 0.25),
        ),
        task_recovery_seconds=max(
            1.0,
            _env_float(values, "M_AGENT_REVIEW_TASK_RECOVERY_SECONDS", 5.0),
        ),
        task_lease_seconds=max(
            30,
            _env_int(values, "M_AGENT_REVIEW_TASK_LEASE_SECONDS", 120),
        ),
        runtime_mode=runtime.mode,
        data_root=runtime.data_root,
    )


# ============================================================
# 拒接非审核消息
# ============================================================

REJECT_MESSAGE = (
    "本入口接收 .docx、.html/.htm、.pptx 文件或直接发送文字，请发送需要审核的内容"
)


def is_docx_filename(filename: str | None) -> bool:
    """判断文件名是否为 .docx."""
    if not filename:
        return False
    return filename.lower().endswith(".docx")


def is_pptx_filename(filename: str | None) -> bool:
    """判断文件名是否为可进入独立 PPT 审核的 .pptx。"""
    if not filename:
        return False
    return filename.lower().endswith(".pptx")


def is_html_filename(filename: str | None) -> bool:
    """判断文件名是否为支持的 HTML 文件。"""
    if not filename:
        return False
    return Path(filename).suffix.lower() in {".html", ".htm"}


def is_supported_review_filename(filename: str | None) -> bool:
    """判断文件名是否属于审核入口支持的上传格式。"""
    return (
        is_docx_filename(filename)
        or is_html_filename(filename)
        or is_pptx_filename(filename)
    )


def _review_file_rejection_message(filename: str) -> str:
    if filename.lower().endswith(".ppt"):
        return "❌ 暂不支持旧版 .ppt，请另存为 .pptx 后再发送。"
    return (
        "❌ 本入口仅接收 .docx、.html/.htm 或 .pptx 文件，"
        f"你发的是：{filename}"
    )


def _build_file_ack(pending_mode: str | None) -> str:
    """文件到达后立即回复；自动归集等待不暴露给用户。"""
    if pending_mode == "format":
        return "收到文件啦，正在按公文模板检查实际格式，请稍等……"
    if pending_mode == "multi":
        return "收到文件，正在加入本次联合审核……"
    return (
        "收到文件啦，正在加紧审核，请稍等"
        "（模型反应有点慢，你可以先干点别的，一会儿再来看）……"
    )


# ============================================================
# .docx 解析(复用 app.review.parser,这里直接 import)
# ============================================================

from app.review.parser import parse_docx as _parse_docx  # noqa: E402


def _extract_primary_inference_texts(
    files: tuple[UploadedFile, ...],
) -> tuple[str, ...]:
    """读取本次暂存 Word 的文字，只用于判断主文件，不产生审核意见。"""
    import tempfile

    texts: list[str] = []
    for file in files:
        temporary_path: Path | None = None
        try:
            if file.stored_path and Path(file.stored_path).is_file():
                path = Path(file.stored_path)
            else:
                with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as temporary:
                    temporary.write(file.read_bytes())
                    temporary_path = Path(temporary.name)
                path = temporary_path
            parsed = _parse_docx(path)
            texts.append("\n".join(parsed.paragraphs))
        except Exception as exc:
            logger.warning("主文件内容识别读取失败: %s: %s", file.filename, exc)
            texts.append("")
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    return tuple(texts)


async def _settle_auto_review_batch(
    store: ReviewIntakeStore,
    *,
    channel: str,
    sender_userid: str,
    expected_revision: int,
    delay_seconds: float,
):
    """等待短暂静默窗口；新文件到达后，旧版本自然失效。"""
    await asyncio.sleep(delay_seconds)
    snapshot = store.auto_batch_snapshot(
        channel=channel,
        sender_userid=sender_userid,
        expected_revision=expected_revision,
    )
    if snapshot.action == "stale":
        return snapshot
    file_texts = await asyncio.to_thread(
        _extract_primary_inference_texts,
        snapshot.files,
    )
    return store.finalize_auto_batch(
        channel=channel,
        sender_userid=sender_userid,
        expected_revision=expected_revision,
        file_texts=file_texts,
    )


# ============================================================
# 存档管理
# ============================================================

def _next_review_index(reviews_dir: Path, date_str: str) -> int:
    """取当天下一个序号(从 001 开始)."""
    month_dir = reviews_dir / date_str[:4] / date_str[4:6]
    if not month_dir.exists():
        return 1
    existing = [
        d for d in month_dir.iterdir()
        if d.is_dir() and d.name.startswith(date_str)
    ]
    return len(existing) + 1


def _safe_source_name(original_filename: str) -> str:
    """把原始文件名清洗成可安全存入 input/ 目录的文件名."""
    original = original_filename or "uploaded.docx"
    path = Path(original)
    stem = path.stem or "uploaded"
    ext = path.suffix or ".docx"
    if not re.fullmatch(r"\.[A-Za-z0-9]+", ext):
        ext = ".docx"
    safe_stem = re.sub(r'[^\w一-鿿\-_]', '_', stem)
    return safe_stem + ext


def save_review(
    *,
    reviews_dir: Path,
    file_bytes: bytes | None,
    original_filename: str,
    sender: str,
    msgid: str,
    result: ReviewResult,
    parsed_paragraphs: list[str],
    text_content: str | None = None,
    doc_type: DocumentType = DocumentType.NEI_CAN,
) -> Path:
    """保存审核记录到统一审核任务目录.

    目录结构:
      tasks/review/2026/06/20260613-001/
        input/原文件
        output/report.md
        meta.json
    """
    date_str = datetime.now().strftime("%Y%m%d")
    idx = _next_review_index(reviews_dir, date_str)
    review_dir = reviews_dir / date_str[:4] / date_str[4:6] / f"{date_str}-{idx:03d}"
    return save_review_to_directory(
        review_dir=review_dir,
        file_bytes=file_bytes,
        original_filename=original_filename,
        sender=sender,
        msgid=msgid,
        result=result,
        parsed_paragraphs=parsed_paragraphs,
        text_content=text_content,
        doc_type=doc_type,
    )


def save_review_to_directory(
    *,
    review_dir: Path,
    file_bytes: bytes | None,
    original_filename: str,
    sender: str,
    msgid: str,
    task_id: str | None = None,
    result: ReviewResult,
    parsed_paragraphs: list[str],
    text_content: str | None = None,
    doc_type: DocumentType = DocumentType.NEI_CAN,
    paragraph_pages: list[int | None] | None = None,
    mark_processing_completed: bool = True,
) -> Path:
    """把审核产物写入指定任务目录，供持久队列恢复时复用。"""
    input_dir = review_dir / "input"
    output_dir = review_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. 保存原始文件
    safe_name = _safe_source_name(original_filename)
    source_path = input_dir / safe_name
    if file_bytes is not None:
        source_path.write_bytes(file_bytes)
    else:
        source_path.write_text(text_content or "", encoding="utf-8")

    # 2. 保存 report.md
    report_path = output_dir / "report.md"
    report_path.write_text(
        format_review_result(
            result,
            original_filename,
            doc_type=doc_type,
            paragraph_pages=paragraph_pages,
        ),
        encoding="utf-8",
    )

    # 3. 保存结构化元信息
    meta_path = review_dir / "meta.json"
    existing_meta: dict[str, object] = {}
    if meta_path.is_file():
        try:
            loaded_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded_meta, dict):
                existing_meta = loaded_meta
        except (OSError, json.JSONDecodeError):
            existing_meta = {}
    existing_meta.update(
        {
            "task_id": task_id or review_dir.name,
            "original_filename": original_filename,
            "sender_userid": sender,
            "message_id": msgid,
            "reviewed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "document_type": doc_type.value,
            "total_rules": result.total_rules,
            "passed_rules": result.passed_rules,
            "finding_count": len(result.findings),
            "paragraph_preview": [p[:80] for p in parsed_paragraphs[:10]],
        }
    )
    meta_path.write_text(
        json.dumps(existing_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if mark_processing_completed:
        write_task_status(review_dir, processing_status="completed")

    return review_dir


def archive_multi_file_review(
    *,
    reviews_dir: Path,
    sender: str,
    msgid: str,
    bundle,
) -> tuple[Path, list[Path]]:
    """把一次联合审核保存为一个任务，并生成各文件的标注文档。"""
    from app.review.error_marker import mark_errors_in_docx  # noqa: E402

    date_str = datetime.now().strftime("%Y%m%d")
    idx = _next_review_index(reviews_dir, date_str)
    task_dir = reviews_dir / date_str[:4] / date_str[4:6] / f"{date_str}-{idx:03d}"
    input_dir = task_dir / "input"
    output_dir = task_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    marked_paths: list[Path] = []
    report_sections = [
        "# 多文件联合审核报告",
        "",
        f"- 文件数：{len(bundle.documents)}",
        f"- 跨文件问题数：{bundle.cross_file_finding_count}",
        f"- 主文件：{next(document.source.filename for document in bundle.documents if document.source.file_index == bundle.primary_file_index)}",
        "",
    ]
    file_meta: list[dict[str, object]] = []
    for order, document in enumerate(bundle.documents, start=1):
        safe_name = f"{order:02d}_{_safe_source_name(document.source.filename)}"
        source_path = input_dir / safe_name
        source_path.write_bytes(document.source.path.read_bytes())
        report_sections.extend(
            [
                f"## {document.source.filename}",
                "",
                format_review_result(
                    document.result,
                    document.source.filename,
                    doc_type=document.doc_type,
                ),
                "",
            ]
        )
        marked_name = ""
        if document.result.findings:
            marked_path = output_dir / f"marked_{safe_name}"
            mark_errors_in_docx(source_path, marked_path, document.result.findings)
            marked_paths.append(marked_path)
            marked_name = marked_path.name
        file_meta.append(
            {
                "order": order,
                "filename": document.source.filename,
                "is_primary": document.source.file_index == bundle.primary_file_index,
                "document_type": document.doc_type.value,
                "finding_count": len(document.result.findings),
                "input_file": safe_name,
                "marked_file": marked_name,
            }
        )

    if bundle.warnings:
        report_sections.extend(["## 降级说明", ""])
        report_sections.extend(f"- {warning}" for warning in bundle.warnings)
        report_sections.append("")
    (output_dir / "report.md").write_text("\n".join(report_sections), encoding="utf-8")
    (task_dir / "meta.json").write_text(
        json.dumps(
            {
                "task_id": task_dir.name,
                "sender_userid": sender,
                "message_id": msgid,
                "reviewed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "document_type": "multi_file",
                "file_count": len(bundle.documents),
                "cross_file_finding_count": bundle.cross_file_finding_count,
                "primary_file_index": bundle.primary_file_index,
                "files": file_meta,
                "warning_count": len(bundle.warnings),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_task_status(task_dir, processing_status="completed")
    return task_dir, marked_paths


def build_multi_file_review_reply(bundle, *, marked_file_count: int) -> str:
    """联合审核只回数量摘要，具体问题放在对应 Word 批注中。"""
    primary_filename = next(
        document.source.filename
        for document in bundle.documents
        if document.source.file_index == bundle.primary_file_index
    )
    lines = [f"多文件联合审核完成，共 {len(bundle.documents)} 份文件："]
    lines.append(f"主文件：{primary_filename}")
    lines.extend(
        f"{document.source.filename}：{len(document.result.findings)} 处"
        for document in bundle.documents
    )
    lines.append(f"跨文件问题：{bundle.cross_file_finding_count} 处")
    if marked_file_count:
        lines.append(f"共生成 {marked_file_count} 份带批注的文档，将继续发送。")
    else:
        lines.append("没有发现需要标注的问题。")
    if bundle.warnings:
        lines.append("跨文件语义检查出现降级，逐文件审核和确定性附件检查已完成。")
    return "\n".join(lines)


# ============================================================
# 消息处理
# ============================================================

def get_sender_id(frame: Mapping[str, object]) -> str:
    body = frame.get("body")
    if not isinstance(body, Mapping):
        return "unknown"
    sender = body.get("from")
    if isinstance(sender, Mapping):
        value = sender.get("userid")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def get_string_value(values: Mapping[str, object], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = values.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def get_file_info(frame: Mapping[str, object]) -> Mapping[str, object] | None:
    body = frame.get("body")
    if not isinstance(body, Mapping):
        return None
    file_info = body.get("file")
    if not isinstance(file_info, Mapping):
        return None
    return file_info


@dataclass(frozen=True)
class FilePayload:
    url: str
    aes_key: str | None
    filename: str | None


@dataclass(frozen=True)
class RecentSubmission:
    kind: str
    created_at: float


class RecentSubmissionTracker:
    """记录用户刚提交过待审核内容，用于忽略紧随其后的催审短句。"""

    def __init__(self, *, ttl_seconds: float = 90.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._entries: dict[str, RecentSubmission] = {}

    def remember(self, userid: str, kind: str, *, now: float | None = None) -> None:
        self._entries[userid] = RecentSubmission(kind=kind, created_at=now or perf_counter())

    def forget(self, userid: str) -> None:
        self._entries.pop(userid, None)

    def has_recent_submission(self, userid: str, *, now: float | None = None) -> bool:
        entry = self._entries.get(userid)
        if entry is None:
            return False
        current = now or perf_counter()
        if current - entry.created_at > self.ttl_seconds:
            self._entries.pop(userid, None)
            return False
        return True

    def should_ignore_text_review(self, userid: str, text: str, *, now: float | None = None) -> bool:
        return self.has_recent_submission(userid, now=now) and _is_followup_review_request_text(text)


def extract_file_payload(frame: Mapping[str, object]) -> FilePayload | None:
    file_info = get_file_info(frame)
    if file_info is None:
        return None
    url = get_string_value(file_info, ("url", "download_url", "downloadUrl", "file_url", "fileUrl"))
    if url is None:
        return None
    aes_key = get_string_value(file_info, ("aeskey", "aes_key", "aesKey"))
    filename = get_string_value(file_info, ("filename", "file_name", "fileName", "name"))
    return FilePayload(url=url, aes_key=aes_key, filename=filename)


def extract_text_content(frame: Mapping[str, object]) -> str | None:
    """从文本消息中提取 content."""
    body = frame.get("body")
    if not isinstance(body, Mapping):
        return None
    text_info = body.get("text")
    if not isinstance(text_info, Mapping):
        return None
    return get_string_value(text_info, ("content",))


def _is_followup_review_request_text(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？,.!?；;:：]+", "", text.strip())
    if not normalized:
        return False
    return _FOLLOWUP_REVIEW_REQUEST_RE.fullmatch(normalized) is not None


def _is_official_format_review_request_text(text: str) -> bool:
    """只识别用户明确提出的短格式审核指令。"""
    return is_format_review_request(text)


def _resolve_smalltalk_text_reply(text: str) -> str | None:
    normalized = re.sub(r"[\s，。！？,.!?；;:：]+", "", text.strip())
    if not normalized:
        return None
    if _THANKS_SMALLTALK_RE.fullmatch(normalized):
        return "不客气。"
    if _ACK_SMALLTALK_RE.fullmatch(normalized):
        return "收到。"
    return None


def _resolve_instruction_only_text_reply(
    recent_submission_tracker: RecentSubmissionTracker,
    userid: str,
    text: str,
    *,
    now: float | None = None,
) -> str | None:
    if not _is_followup_review_request_text(text):
        return None
    if recent_submission_tracker.has_recent_submission(userid, now=now):
        return "收到，我会按你刚发的内容继续审核，请稍等……"
    return "收到，请把需要审核的文字、.docx、.html/.htm或.pptx文件发给我，我来帮你看。"


def _resolve_text_registration_reply(
    registration_flow: RegistrationFlow,
    userid: str,
    text: str,
) -> tuple[bool, str]:
    """判断文字消息是否应先被注册流程接管."""
    is_registration, reply = registration_flow.handle_name_message(userid, text)
    if is_registration:
        return True, reply
    if registration_flow.should_ask_name(userid):
        return True, registration_flow.ask_name_message()
    return False, ""


def _build_enter_welcome_text(
    registration_flow: RegistrationFlow,
    userid: str,
) -> str:
    """构造用户进入会话时的欢迎语."""
    if registration_flow.should_ask_name(userid):
        return registration_flow.ask_name_message()
    return (
        "你好，需要我帮你审核什么呢？请直接发送 .docx、.html/.htm、.pptx 文件"
        "或直接发送文字，我会认真审核。"
    )


def _split_text_into_paragraphs(text: str) -> list[str]:
    """把用户输入的文本拆成段落.

    每个手工换行都视为独立段落,过滤纯空段.

    企业微信文字可能只用一个空行分隔正文和附件清单。如果先按空行
    切分,附件清单里的多行会被错误合成一个大段,导致跨行规则漏判。
    """
    return [line.strip() for line in text.splitlines() if line.strip()]


def build_user_review_reply(
    result: ReviewResult,
    filename: str,
    *,
    doc_type: DocumentType,
) -> str | None:
    """生成发给用户的文字回复.

    - 有问题: 不再重复发错误列表文字，改为只回标注文档
    - 无问题: 发简短通过话术
    """
    if result.findings:
        return None
    if doc_type == DocumentType.OFFICIAL_FORMAT:
        return "没有发现公文格式问题，可以走审批了。"
    return "没有发现问题，可以走审批了。"


_REVIEW_TASK_TYPE_BY_DOCUMENT_TYPE = {
    DocumentType.GENERAL: GENERAL_REVIEW_TASK_TYPE,
    DocumentType.HALF_MONTHLY: HALF_MONTHLY_REVIEW_TASK_TYPE,
    DocumentType.NEI_CAN: NEICAN_REVIEW_TASK_TYPE,
    DocumentType.OFFICIAL_FORMAT: OFFICIAL_FORMAT_REVIEW_TASK_TYPE,
}
_DOCUMENT_TYPE_BY_REVIEW_TASK_TYPE = {
    task_type: doc_type
    for doc_type, task_type in _REVIEW_TASK_TYPE_BY_DOCUMENT_TYPE.items()
}
_DOCUMENT_TYPE_BY_REVIEW_TASK_TYPE[GENERAL_TEXT_REVIEW_TASK_TYPE] = DocumentType.GENERAL
_DOCUMENT_TYPE_BY_REVIEW_TASK_TYPE[GENERAL_HTML_REVIEW_TASK_TYPE] = DocumentType.GENERAL


def _review_task_type_for_document_type(doc_type: DocumentType) -> str:
    """把单项审核文档类型映射到稳定的持久任务类型。"""
    try:
        return _REVIEW_TASK_TYPE_BY_DOCUMENT_TYPE[doc_type]
    except KeyError as exc:
        raise ValueError(f"不支持的单项审核文档类型：{doc_type}") from exc


def _document_type_for_review_task_type(task_type: str) -> DocumentType:
    try:
        return _DOCUMENT_TYPE_BY_REVIEW_TASK_TYPE[task_type]
    except KeyError as exc:
        raise ValueError(f"不支持的单项审核任务类型：{task_type}") from exc


def _queued_review_acceptance_message(
    *,
    review_label: str,
    created: bool,
    input_label: str,
) -> str:
    if created:
        return f"收到，正在进行{review_label}，完成后会自动发送结果。"
    return f"{input_label}已经在处理中，无需重复提交。完成后会自动发送结果。"


async def _reply_queued_review_acceptance(
    ws_client: object,
    frame: object,
    stream_id: str,
    *,
    review_label: str,
    created: bool,
    input_label: str,
    acknowledgment_already_sent: bool = False,
) -> bool:
    """即时收件回执已经覆盖用户预期时，不再重复发送入队提示。"""
    if acknowledgment_already_sent:
        return False
    await ws_client.reply_stream(
        frame,
        stream_id,
        _queued_review_acceptance_message(
            review_label=review_label,
            created=created,
            input_label=input_label,
        ),
        True,
    )
    return True


def _review_label(doc_type: DocumentType) -> str:
    label = document_type_label(doc_type)
    return label if label.endswith("审核") else f"{label}审核"


def _prepare_review_reply_file(
    review_dir: Path | None,
    original_filename: str,
    findings: list[Finding],
) -> Path | None:
    """准备回传给用户的审核文档.

    - 有问题: 基于 input/ 原文生成到 output/ 的 `marked_原文件名.docx`
    - 无问题: 不回传文档
    """
    if review_dir is None:
        return None

    source_name = _safe_source_name(original_filename)
    source_path = review_dir / "input" / source_name
    if not source_path.exists():
        return None

    if not findings:
        return None

    from app.review.error_marker import mark_errors_in_docx  # noqa: E402

    source_path_obj = Path(source_name)
    marked_path = review_dir / "output" / f"marked_{source_path_obj.stem}{source_path_obj.suffix}"
    mark_errors_in_docx(source_path, marked_path, findings)
    return marked_path


async def _review_text(
    text: str,
    config: ReviewConfig,
) -> str:
    """对纯文字做通用审核,返回格式化文本结果."""
    result, _ = await _review_text_result(text, config)
    if result.findings and result.findings[0].rule_id == "__empty_text__":
        return "❌ 发送的内容为空,无法审核。"

    from app.review.document_type import DocumentType  # noqa: E402
    from app.review.output_formatter import format_review_result  # noqa: E402

    return format_review_result(result, "文字消息", doc_type=DocumentType.GENERAL)


async def _review_text_result(
    text: str,
    config: ReviewConfig,
) -> tuple[ReviewResult, list[str]]:
    """对纯文字做通用审核,返回结构化结果和段落."""
    from app.review.general_reviewer import review_general  # noqa: E402

    paragraphs = _split_text_into_paragraphs(text)
    if not paragraphs:
        return (
            ReviewResult(
                findings=[
                    Finding(
                        rule_id="__empty_text__",
                        paragraph_index=0,
                        line_number=1,
                        original_text="",
                        description="发送的内容为空,无法审核。",
                        target_text="",
                    )
                ],
                total_rules=0,
                passed_rules=0,
                filename="文字消息",
            ),
            [],
        )

    general_rules_text = load_rules("app/review/rules_general.md")
    result = await review_general(paragraphs, general_rules_text, "文字消息")
    return result, paragraphs


async def _start_neican_review(
    *,
    phase1_runner,
    phase2_runner,
    paragraphs: list[str],
    rules_text: str,
    filename: str,
    file_path: Path | None,
) -> tuple[ReviewResult, asyncio.Task[tuple[ReviewResult, float]], dict[str, float]]:
    """启动内参两阶段审核。

    返回：
    1. 第一阶段结果（用于尽快反馈给用户）
    2. 后台进行中的第二阶段任务
    3. 当前已知耗时数据
    """
    wall_start = perf_counter()

    def _runner_accepts_file_path(runner) -> bool:
        try:
            return "file_path" in inspect.signature(runner).parameters
        except (TypeError, ValueError):
            return False

    async def _timed_phase2() -> tuple[ReviewResult, float]:
        phase2_started_at = perf_counter()
        if _runner_accepts_file_path(phase2_runner):
            result = await phase2_runner(
                paragraphs,
                rules_text,
                filename,
                file_path=file_path,
            )
        else:
            result = await phase2_runner(paragraphs, rules_text, filename)
        return result, (perf_counter() - phase2_started_at) * 1000

    phase2_task = asyncio.create_task(_timed_phase2(), name="review-neican-phase2")
    await asyncio.sleep(0)

    phase1_started_at = perf_counter()
    try:
        if _runner_accepts_file_path(phase1_runner):
            phase1_result = await phase1_runner(
                paragraphs,
                rules_text,
                filename,
                file_path=file_path,
            )
        else:
            phase1_result = await phase1_runner(paragraphs, rules_text, filename)
    except Exception:
        phase2_task.cancel()
        await asyncio.gather(phase2_task, return_exceptions=True)
        raise

    return (
        phase1_result,
        phase2_task,
        {
            "wall_start": wall_start,
            "phase1_ms": (perf_counter() - phase1_started_at) * 1000,
        },
    )


async def _process_queued_single_review(
    workspace: GeneralReviewWorkspace,
    *,
    config: ReviewConfig,
    neican_rules_text: str,
) -> PreparedReviewDelivery:
    """执行一项已冻结输入的审核，并在任务目录内准备唯一交付结果。"""
    if workspace.task_type == PPT_REVIEW_TASK_TYPE:
        from app.review.ppt import format_ppt_review_messages, review_pptx  # noqa: E402

        result = await review_pptx(
            workspace.input_file,
            task_dir=workspace.task_dir,
        )
        output_dir = workspace.task_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        result_path = output_dir / "result.json"
        temporary = output_dir / f".result.{uuid4().hex}.tmp"
        await asyncio.to_thread(
            temporary.write_text,
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        await asyncio.to_thread(temporary.replace, result_path)
        return PreparedReviewDelivery.multipart_text(
            format_ppt_review_messages(result)
        )

    doc_type = _document_type_for_review_task_type(workspace.task_type)
    if workspace.task_type == GENERAL_TEXT_REVIEW_TASK_TYPE:
        content = await asyncio.to_thread(workspace.input_file.read_text, encoding="utf-8")
        result, paragraphs = await _review_text_result(content, config)
        await asyncio.to_thread(
            save_review_to_directory,
            review_dir=workspace.task_dir,
            file_bytes=None,
            original_filename=workspace.filename,
            sender=workspace.sender_userid,
            msgid="",
            task_id=workspace.task_id,
            result=result,
            parsed_paragraphs=paragraphs,
            text_content=content,
            doc_type=DocumentType.GENERAL,
            mark_processing_completed=False,
        )
        if result.findings and result.findings[0].rule_id == "__empty_text__":
            return PreparedReviewDelivery.text("发送的内容为空，无法审核。")
        return PreparedReviewDelivery.text(
            format_review_result(result, "文字消息", doc_type=DocumentType.GENERAL)
        )

    if workspace.task_type == GENERAL_HTML_REVIEW_TASK_TYPE:
        from app.review.general_reviewer import review_general  # noqa: E402
        from app.review.html_parser import parse_html  # noqa: E402

        try:
            parsed_html = await asyncio.to_thread(parse_html, workspace.input_file)
        except ValueError as exc:
            message = str(exc)
            if "没有可审核的可见文字" in message:
                return PreparedReviewDelivery.text(
                    "HTML文件中没有可审核的可见文字，请提供包含静态正文的HTML文件。"
                )
            if "编码无法识别" in message:
                return PreparedReviewDelivery.text(
                    "HTML文件编码无法识别，请转换为UTF-8后重新发送。"
                )
            raise

        result = await review_general(
            parsed_html.paragraphs,
            load_rules("app/review/rules_general.md"),
            workspace.filename,
            whole_document_logic_min_chars=(
                0 if len(parsed_html.paragraphs) >= 2 else 200
            ),
        )
        file_bytes = await asyncio.to_thread(workspace.input_file.read_bytes)
        await asyncio.to_thread(
            save_review_to_directory,
            review_dir=workspace.task_dir,
            file_bytes=file_bytes,
            original_filename=workspace.filename,
            sender=workspace.sender_userid,
            msgid="",
            task_id=workspace.task_id,
            result=result,
            parsed_paragraphs=parsed_html.paragraphs,
            doc_type=DocumentType.GENERAL,
            paragraph_pages=parsed_html.paragraph_pages,
            mark_processing_completed=False,
        )
        return PreparedReviewDelivery.text(
            format_review_result(
                result,
                workspace.filename,
                doc_type=DocumentType.GENERAL,
                paragraph_pages=parsed_html.paragraph_pages,
            )
        )

    parsed = await asyncio.to_thread(_parse_docx, workspace.input_file)
    if doc_type != DocumentType.OFFICIAL_FORMAT:
        detected_type = detect_document_type(workspace.filename, parsed.paragraphs)
        if detected_type != doc_type:
            raise ValueError("审核任务类型与文件识别结果不一致")

    if doc_type == DocumentType.GENERAL:
        from app.review.general_reviewer import review_general  # noqa: E402

        result = await review_general(
            parsed.paragraphs,
            load_rules("app/review/rules_general.md"),
            workspace.filename,
        )
    elif doc_type == DocumentType.HALF_MONTHLY:
        from app.review.halfmonthly_reviewer import review_halfmonthly  # noqa: E402

        result = await review_halfmonthly(
            parsed.paragraphs,
            load_rules("app/review/rules_halfmonthly.md"),
            workspace.filename,
            numbering=parsed.numbering,
            file_path=workspace.input_file,
        )
    elif doc_type == DocumentType.OFFICIAL_FORMAT:
        from app.review.official_format_checker import review_official_format  # noqa: E402

        result = await asyncio.to_thread(
            review_official_format,
            workspace.input_file,
            workspace.filename,
        )
    else:
        from app.review.reviewer import review_phase1, review_phase2  # noqa: E402

        phase1_result, phase2_task, timings = await _start_neican_review(
            phase1_runner=review_phase1,
            phase2_runner=review_phase2,
            paragraphs=parsed.paragraphs,
            rules_text=neican_rules_text,
            filename=workspace.filename,
            file_path=workspace.input_file,
        )
        phase2_result, phase2_ms = await phase2_task
        findings = [*phase1_result.findings, *phase2_result.findings]
        findings.sort(key=lambda finding: finding.paragraph_index)
        result = ReviewResult(
            findings=findings,
            total_rules=phase1_result.total_rules + phase2_result.total_rules,
            passed_rules=phase1_result.passed_rules + phase2_result.passed_rules,
            filename=workspace.filename,
        )
        logger.info(
            "内参持久任务阶段耗时: phase1=%.1fms phase2=%.1fms task_id=%s",
            timings["phase1_ms"],
            phase2_ms,
            workspace.task_id,
            extra=log_extra(workspace.sender_userid, workspace.sender_name),
        )

    file_bytes = await asyncio.to_thread(workspace.input_file.read_bytes)
    await asyncio.to_thread(
        save_review_to_directory,
        review_dir=workspace.task_dir,
        file_bytes=file_bytes,
        original_filename=workspace.filename,
        sender=workspace.sender_userid,
        msgid="",
        task_id=workspace.task_id,
        result=result,
        parsed_paragraphs=parsed.paragraphs,
        doc_type=doc_type,
        mark_processing_completed=False,
    )
    reply = build_user_review_reply(result, workspace.filename, doc_type=doc_type)
    if reply is not None:
        return PreparedReviewDelivery.text(reply)
    marked_path = await asyncio.to_thread(
        _prepare_review_reply_file,
        workspace.task_dir,
        workspace.filename,
        result.findings,
    )
    if marked_path is None:
        raise RuntimeError(f"{_review_label(doc_type)}发现问题但未生成标注文档")
    return PreparedReviewDelivery.attachment(marked_path)


# ============================================================
# 主流程
# ============================================================

# ============================================================
# 持久化回复队列（解决 ACK 超时问题）
# ============================================================

class PendingReplyQueue:
    """持久化待发送回复队列，连接断开后仍保留，重连后自动发送。"""

    def __init__(self):
        self._queue: dict[str, tuple[object, str]] = {}  # req_id -> (frame, message)
        self._lock = asyncio.Lock()

    def put(self, frame: object, req_id: str, message: str) -> None:
        """添加待发送回复到队列。"""
        self._queue[req_id] = (frame, message)

    async def drain(self, ws_client: object) -> None:
        """将队列中的所有待发送回复通过 ws_client 发送出去。"""
        async with self._lock:
            if not self._queue:
                return
            pending = list(self._queue.items())
            # 不在这里清空，等发送成功后再清
            print(f"📤 重连后发送 {len(pending)} 条待处理回复...", flush=True)
        for req_id, (frame, message) in pending:
            try:
                await ws_client.reply_stream(frame, req_id, message, True)
                print(f"✅ 补发成功:req_id={req_id[:12]}...", flush=True)
                # 发送成功才从队列移除
                async with self._lock:
                    self._queue.pop(req_id, None)
            except Exception as exc:
                print(f"⚠️ 补发失败:req_id={req_id[:12]}... error={exc}", flush=True)
                # 发送失败保留在队列，下次重连再试
                break

    def clear(self) -> None:
        """清空队列（连接断开时被调用）。"""
        self._queue.clear()


_pending_queue: PendingReplyQueue | None = None


def _configure_ws_client_timeouts(ws_client: object, *, reply_ack_timeout_seconds: float) -> None:
    """覆盖 SDK 内部回复回执等待时间.

    wecom-aibot-sdk 1.0.7 内部默认只等 5 秒；大文件分片上传在企业微信侧
    回执可能超过 5 秒，导致实际成功回执被当成 unknown frame。
    """
    manager = getattr(ws_client, "_ws_manager", None)
    if manager is None or not hasattr(manager, "_reply_ack_timeout"):
        logger.warning(
            "未找到企业微信 SDK 回执超时配置入口,保留 SDK 默认值。",
            extra=log_extra("system", "system"),
        )
        return
    setattr(manager, "_reply_ack_timeout", reply_ack_timeout_seconds)
    logger.info(
        "企业微信回复回执等待时间已设置为 %.1f 秒。",
        reply_ack_timeout_seconds,
        extra=log_extra("system", "system"),
    )


def _summarize_delivery_error(exc: BaseException) -> str:
    """给用户看的发送失败摘要，避免暴露 req_id 等底层细节。"""
    message = str(exc)
    if isinstance(exc, asyncio.TimeoutError) or "Reply ack timeout" in message:
        return "企业微信上传回执超时"
    if "Upload failed" in message or "upload failed" in message:
        return "企业微信文件上传失败"
    if "reply" in message.lower() or "send" in message.lower():
        return "企业微信消息发送失败"
    return "企业微信发送异常"


def _build_processing_failure_user_reply(stage: str, exc: BaseException) -> str:
    """生成固定安全话术；原异常仅进入日志和运维事件。"""
    _ = exc
    return f"{stage}失败，已经提醒管理员排查。请稍后重试。"


def _build_delivery_failure_user_reply(msg_type: str, exc: BaseException) -> str:
    reason = _summarize_delivery_error(exc)
    return (
        f"审核已经完成，但{msg_type}发送失败（{reason}）。"
        "我已经把详细错误提醒给管理员处理，请稍后再试或联系管理员。"
    )


async def _reply_delivery_failure_to_user(
    ws_client: object,
    frame: object,
    req_id: str,
    msg_type: str,
    exc: BaseException,
) -> None:
    """附件/结果发送失败时给用户简短说明；详细错误走运维 Bot。"""
    try:
        await ws_client.reply_stream(
            frame,
            req_id,
            _build_delivery_failure_user_reply(msg_type, exc),
            True,
        )
    except Exception as reply_exc:
        logger.warning(
            "发送用户失败说明失败: %s",
            reply_exc,
            extra=log_extra("system", "system"),
        )


async def _deliver_review_attachment(
    *,
    attachment_delivery: AttachmentDelivery,
    ws_client: object,
    frame: object | None,
    path: Path,
    sender: str,
    sender_name: str,
    label: str,
    req_id_factory,
    chat_id: str = "",
    notify_user_on_failure: bool = True,
    raise_on_uncertain: bool = False,
) -> bool:
    task_dir = path.parent.parent if path.parent.name == "output" else path.parent
    result = await attachment_delivery.deliver(
        ws_client=ws_client,
        request=DeliveryRequest(
            file_path=path,
            allowed_root=task_dir,
            frame=frame,
            chat_id=chat_id,
            task_dir=task_dir,
            source="review_bot",
            sender_userid=sender,
            sender_name=sender_name,
            skill_id=label,
            job_id=task_dir.name,
        ),
    )
    if result.delivered:
        return True
    if raise_on_uncertain and result.error_code in {"reply_timeout", "reply_failed"}:
        raise ReviewDeliveryStatusUncertain(result.error_code)
    if not notify_user_on_failure:
        return False
    try:
        if chat_id:
            await asyncio.wait_for(
                ws_client.send_message(
                    chat_id,
                    {"msgtype": "markdown", "markdown": {"content": result.user_message}},
                ),
                timeout=30.0,
            )
        else:
            await asyncio.wait_for(
                ws_client.reply_stream(
                    frame,
                    req_id_factory("review-send-failed"),
                    result.user_message,
                    True,
                ),
                timeout=30.0,
            )
    except Exception as exc:
        logger.warning(
            "审核附件失败提示未能发送: %s",
            type(exc).__name__,
            extra=log_extra(sender, sender_name),
        )
    return False


def _build_queued_attachment_delivery(
    ops_event_logger: OpsEventLogger | None,
) -> AttachmentDelivery:
    # 队列结果只发一次；回执超时后重传可能造成用户收到两份文件。
    return AttachmentDelivery(
        config=AttachmentDeliveryConfig(max_attempts=1),
        ops_event_logger=ops_event_logger,
    )


async def _send_queued_review_text(
    ws_client: object,
    recipient: str,
    text: str,
    *,
    timeout_seconds: float = 30.0,
) -> bool:
    sender = getattr(ws_client, "send_message", None)
    if not callable(sender):
        raise RuntimeError("企业微信 SDK 不支持主动文本消息")
    await asyncio.wait_for(
        sender(
            recipient,
            {"msgtype": "markdown", "markdown": {"content": text}},
        ),
        timeout=timeout_seconds,
    )
    return True


async def _run_review_task_worker_supervised(
    *,
    task_executor: PersistentTaskExecutor,
    stop_event: asyncio.Event,
    poll_interval: float,
    worker_count: int,
    recovery_interval: float,
    ops_event_logger: OpsEventLogger,
    restart_delay_seconds: float = 5.0,
) -> None:
    while not stop_event.is_set():
        try:
            await task_executor.run_forever(
                stop_event=stop_event,
                poll_interval=poll_interval,
                worker_count=worker_count,
                recovery_interval=recovery_interval,
            )
            if stop_event.is_set():
                return
            raise RuntimeError("审核后台任务 worker 意外结束")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "审核后台任务 worker 异常退出，将自动重启",
                exc_info=exc,
                extra=log_extra("system", "system"),
            )
            try:
                ops_event_logger.record(
                    source="review_task_worker",
                    severity="error",
                    subject="审核后台任务 worker 异常退出",
                    detail="持久任务 worker 已退出，系统将自动重启。",
                    skill_id="review_single_item",
                )
            except Exception:
                logger.exception(
                    "审核后台任务 worker 异常事件写入失败",
                    extra=log_extra("system", "system"),
                )
            if stop_event.is_set():
                return
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=max(0.0, restart_delay_seconds),
                )
            except TimeoutError:
                pass


async def _heartbeat_loop(root_dir: Path, service: str) -> None:
    while True:
        try:
            write_heartbeat(root_dir, service)
        except Exception as exc:
            logger.warning("审核心跳写入失败: %s", exc, extra=log_extra("system", "system"))
        await asyncio.sleep(30)


# ============================================================
# 主流程
# ============================================================

async def run_review_bot(config: ReviewConfig) -> None:
    try:
        from wecom_aibot_sdk import WSClient, generate_req_id
    except ImportError as exc:
        raise RuntimeError(
            "缺少依赖 wecom-aibot-sdk。请在项目根目录运行：uv sync --locked"
        ) from exc

    global _pending_queue
    _pending_queue = PendingReplyQueue()

    config.reviews_dir.mkdir(parents=True, exist_ok=True)
    config.logs_dir.mkdir(parents=True, exist_ok=True)
    if not config.rules_path.exists():
        raise RuntimeError(f"规则库文件不存在: {config.rules_path}")

    rules_text = load_rules(str(config.rules_path))
    logger.info(
        "规则库已加载: %d 字符 (来源: %s)",
        len(rules_text),
        config.rules_path,
        extra=log_extra("system", "system"),
    )
    logger.info(
        "审核存档目录: %s", config.reviews_dir, extra=log_extra("system", "system")
    )
    logger.info(
        "日志目录: %s", config.logs_dir, extra=log_extra("system", "system")
    )

    registry = UserRegistry(config.user_registry_path)
    registration_flow = RegistrationFlow(
        registry, require_registration=config.require_registration
    )
    recent_submission_tracker = RecentSubmissionTracker()
    review_intake_store = ReviewIntakeStore(
        ttl_seconds=config.intake_ttl_seconds,
        storage_dir=config.intake_dir,
    )

    ws_client = WSClient(
        bot_id=config.wecom_bot_id,
        secret=config.wecom_bot_secret,
    )
    _configure_ws_client_timeouts(
        ws_client,
        reply_ack_timeout_seconds=config.reply_ack_timeout_seconds,
    )
    ops_event_logger = OpsEventLogger(config.ops_events_dir)
    attachment_delivery = AttachmentDelivery(ops_event_logger=ops_event_logger)
    queued_attachment_delivery = _build_queued_attachment_delivery(ops_event_logger)
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(config.ops_heartbeat_dir, "review_bot"),
        name="review-bot-heartbeat",
    )

    notifier = AdminNotifier(
        ws_client,
        NotificationConfig(
            admin_user_id=config.admin_user_id,
            admin_name=config.admin_name,
            cooldown_seconds=config.notification_cooldown,
            direct_message_enabled=config.direct_admin_notifications,
        ),
        event_logger=ops_event_logger,
        source="review_bot",
    )

    ws_client.on(
        "connected",
        lambda: logger.info("企业微信长连接已建立。", extra=log_extra("system", "system")),
    )
    ws_client.on(
        "authenticated",
        lambda: logger.info(
            "企业微信审核 Bot 认证成功,等待文件消息。",
            extra=log_extra("system", "system"),
        ),
    )
    ws_client.on(
        "disconnected",
        lambda reason: (
            logger.warning(
                "企业微信连接已断开: %s", reason, extra=log_extra("system", "system")
            ),
            ops_event_logger.record(
                source="review_bot",
                severity="error",
                subject="审核 Bot 连接断开",
                detail=str(reason),
            ),
        ),
    )
    ws_client.on(
        "reconnecting",
        lambda attempt: logger.info(
            "企业微信正在重连,第 %s 次。", attempt, extra=log_extra("system", "system")
        ),
    )
    ws_client.on(
        "error",
        lambda error: (
            logger.error(
                "企业微信连接错误: %s", error, extra=log_extra("system", "system")
            ),
            ops_event_logger.record(
                source="review_bot",
                severity="error",
                subject="审核 Bot 连接错误",
                detail=str(error),
            ),
        ),
    )

    async def send_text_result(frame, text: str, *, prefix: str, sender: str, label: str) -> bool:
        req_id = generate_req_id(prefix)
        for retry in range(3):
            try:
                await asyncio.wait_for(
                    ws_client.reply_stream(frame, req_id, text, True),
                    timeout=30.0,
                )
                return True
            except Exception as exc:
                logger.warning(
                    "%s发送失败 to %s: %s, 第 %d 次重试",
                    label,
                    sender,
                    exc,
                    retry + 1,
                    extra=log_extra(sender, registry.get_name(sender) or sender),
                )
                if retry < 2:
                    await asyncio.sleep(2 * (retry + 1))
        await notifier.notify_send_failure(sender, label, Exception("发送失败（已重试3次）"))
        return False

    async def send_review_file(frame, path: Path, *, sender: str, label: str) -> bool:
        return await _deliver_review_attachment(
            attachment_delivery=attachment_delivery,
            ws_client=ws_client,
            frame=frame,
            path=path,
            sender=sender,
            sender_name=registry.get_name(sender) or sender,
            label=label,
            req_id_factory=generate_req_id,
        )

    async def send_active_text(recipient: str, text: str, *, label: str) -> bool:
        sender = getattr(ws_client, "send_message", None)
        if not callable(sender):
            logger.error(
                "%s失败：企业微信 SDK 不支持主动文本消息",
                label,
                extra=log_extra(recipient, registry.get_name(recipient) or recipient),
            )
            return False
        for retry in range(3):
            try:
                await asyncio.wait_for(
                    sender(
                        recipient,
                        {"msgtype": "markdown", "markdown": {"content": text}},
                    ),
                    timeout=30.0,
                )
                return True
            except Exception as exc:
                logger.warning(
                    "%s失败 to %s: %s, 第 %d 次重试",
                    label,
                    recipient,
                    type(exc).__name__,
                    retry + 1,
                    extra=log_extra(recipient, registry.get_name(recipient) or recipient),
                )
                if retry < 2:
                    await asyncio.sleep(2 * (retry + 1))
        await notifier.notify_send_failure(recipient, label, Exception("发送失败（已重试3次）"))
        return False

    async def run_official_format_decision(
        frame,
        sender: str,
        decision,
        *,
        acknowledgment_already_sent: bool = False,
    ) -> None:
        file = decision.files[0]
        filename = file.filename
        buffer = file.read_bytes()
        try:
            try:
                message_id = extract_message_id(frame)
                if not message_id:
                    raise ValueError("企业微信消息缺少稳定消息标识")
                submission = review_tasks.submit_file(
                    channel="wecom",
                    sender_userid=sender,
                    sender_name=registry.get_name(sender) or sender,
                    message_id=message_id,
                    task_type=OFFICIAL_FORMAT_REVIEW_TASK_TYPE,
                    filename=filename,
                    file_bytes=buffer,
                )
            except Exception as exc:
                logger.exception(
                    "公文格式审核任务入队失败 from %s",
                    sender,
                    exc_info=exc,
                    extra=log_extra(sender, registry.get_name(sender) or sender),
                )
                await notifier.notify_file_review_error(sender, filename, "公文格式审核任务入队", exc)
                await ws_client.reply_stream(
                    frame,
                    generate_req_id("review-err"),
                    _build_processing_failure_user_reply("公文格式审核任务受理", exc),
                    True,
                )
                return
            await _reply_queued_review_acceptance(
                ws_client,
                frame,
                generate_req_id("review-format-queued"),
                review_label="公文格式审核",
                created=submission.created,
                input_label="这份文件",
                acknowledgment_already_sent=acknowledgment_already_sent,
            )
        finally:
            review_intake_store.cleanup_files(decision.files)

    async def run_multi_file_decision(frame, sender: str, decision) -> None:
        try:
            await ws_client.reply_stream(
                frame,
                generate_req_id("review-multi"),
                f"已收到 {len(decision.files)} 份文件，正在逐份审核并核对正文与附件，请稍等……",
                True,
            )
            from app.review.multi_file_reviewer import review_multiple_docx  # noqa: E402

            bundle = await review_multiple_docx(
                decision.files,
                general_rules_text=load_rules("app/review/rules_general.md"),
                neican_rules_text=rules_text,
                halfmonthly_rules_text=load_rules("app/review/rules_halfmonthly.md"),
                primary_file_index=decision.primary_file_index,
                instructions=decision.instructions,
            )
            task_dir, marked_paths = archive_multi_file_review(
                reviews_dir=config.reviews_dir,
                sender=sender,
                msgid=extract_message_id(frame),
                bundle=bundle,
            )
            logger.info(
                "多文件联合审核完成: %d 份文件, %d 个跨文件问题, 存档: %s",
                len(bundle.documents),
                bundle.cross_file_finding_count,
                task_dir,
                extra=log_extra(sender, registry.get_name(sender) or sender),
            )
            await send_text_result(
                frame,
                build_multi_file_review_reply(bundle, marked_file_count=len(marked_paths)),
                prefix="review-multi-done",
                sender=sender,
                label="多文件联合审核摘要",
            )
            for path in marked_paths:
                await send_review_file(
                    frame,
                    path,
                    sender=sender,
                    label=f"联合审核文档 {path.name}",
                )
        except Exception as exc:
            logger.exception(
                "多文件联合审核失败 from %s",
                sender,
                exc_info=exc,
                extra=log_extra(sender, registry.get_name(sender) or sender),
            )
            filenames = "、".join(file.filename for file in decision.files)
            await notifier.notify_file_review_error(sender, filenames, "多文件联合审核", exc)
            await ws_client.reply_stream(
                frame,
                generate_req_id("review-err"),
                _build_processing_failure_user_reply("多文件联合审核", exc),
                True,
            )
        finally:
            review_intake_store.cleanup_files(decision.files)

    async def process_queued_review(
        workspace: GeneralReviewWorkspace,
    ) -> PreparedReviewDelivery:
        return await _process_queued_single_review(
            workspace,
            config=config,
            neican_rules_text=rules_text,
        )

    async def send_queued_review_attachment(
        recipient: str,
        path: Path,
        task_dir: Path,
    ) -> bool:
        if path.parent.parent != task_dir:
            raise ValueError("审核结果附件不属于当前任务目录")
        return await _deliver_review_attachment(
            attachment_delivery=queued_attachment_delivery,
            ws_client=ws_client,
            frame=None,
            chat_id=recipient,
            path=path,
            sender=recipient,
            sender_name=registry.get_name(recipient) or recipient,
            label="单项审核文档",
            req_id_factory=generate_req_id,
            notify_user_on_failure=False,
            raise_on_uncertain=True,
        )

    async def notify_queued_review_failure(
        recipient: str,
        error_code: str,
        task_id: str,
    ) -> None:
        messages = {
            "delivery_status_uncertain": (
                "审核已经完成，但结果发送状态暂时无法确认。"
                f"为避免重复发送，我已暂停自动重试并提醒管理员核对。处理编号：{task_id}"
            ),
            "delivery_failed": (
                "审核已经完成，但结果发送失败，已经提醒管理员处理。"
                f"处理编号：{task_id}"
            ),
            "review_processing_failed": (
                "审核处理失败，已经提醒管理员排查。"
                f"请稍后重试，处理编号：{task_id}"
            ),
            "invalid_task_payload": (
                "审核任务状态异常，已经提醒管理员排查。"
                f"处理编号：{task_id}"
            ),
            "invalid_task_checkpoint": (
                "审核任务状态异常，已经提醒管理员排查。"
                f"处理编号：{task_id}"
            ),
        }
        message = messages.get(error_code)
        if message:
            await send_active_text(recipient, message, label="审核任务异常提示")

    task_repository = TaskRepository(
        config.task_queue_db,
        on_transition=TaskLifecycleObserver(
            task_root=config.reviews_dir,
            ops_event_logger=ops_event_logger,
        ),
    )
    review_tasks = GeneralReviewTaskService(
        repository=task_repository,
        reviews_root=config.reviews_dir,
        processor=process_queued_review,
        text_sender=lambda recipient, text: _send_queued_review_text(
            ws_client,
            recipient,
            text,
        ),
        attachment_sender=send_queued_review_attachment,
        failure_notifier=notify_queued_review_failure,
    )
    task_executor = PersistentTaskExecutor(
        repository=task_repository,
        limits=ClaimLimits(
            global_limit=config.task_worker_count,
            per_user_limit=1,
            cost_class_limits={GENERAL_REVIEW_COST_CLASS: config.task_worker_count},
        ),
        worker_id=f"review-bot-{uuid4().hex[:12]}",
        lease_duration=timedelta(seconds=config.task_lease_seconds),
    )
    for task_type in REVIEW_TASK_TYPES:
        task_executor.register_handler(task_type, review_tasks.handle)

    async def on_text(frame):
        """文本消息走通用审核(不生成 marked 文档)."""
        sender = get_sender_id(frame)
        english_name = registry.get_name(sender) or sender
        extra = log_extra(sender, english_name)
        stream_id = generate_req_id("review-text")

        content = extract_text_content(frame)
        if not content:
            logger.warning("收到空文字消息 from %s", sender, extra=extra)
            await ws_client.reply_stream(
                frame, stream_id,
                "没有收到文字内容,请直接发送需要审核的文字。", True,
            )
            return

        was_registered = registry.is_registered(sender)
        is_registration, reg_reply = _resolve_text_registration_reply(
            registration_flow,
            sender,
            content,
        )
        if is_registration:
            if not was_registered and registry.is_registered(sender):
                registered_name = registry.get_name(sender) or content.strip()
                logger.info(
                    "用户 %s 注册英文名: %s",
                    sender,
                    registered_name,
                    extra=log_extra(sender, registered_name),
                )
            elif not was_registered:
                logger.info("新用户 %s 首次使用,等待有效英文名", sender, extra=extra)
            await ws_client.reply_stream(frame, stream_id, reg_reply, True)
            return

        intake_decision = review_intake_store.handle_text(
            channel="wecom",
            sender_userid=sender,
            text=content,
        )
        if intake_decision.action == "wait":
            await ws_client.reply_stream(frame, stream_id, intake_decision.reply, True)
            return
        if intake_decision.action == "run_format":
            await run_official_format_decision(frame, sender, intake_decision)
            return
        if intake_decision.action == "run_multi":
            await run_multi_file_decision(frame, sender, intake_decision)
            return

        instruction_only_reply = _resolve_instruction_only_text_reply(
            recent_submission_tracker,
            sender,
            content,
        )
        smalltalk_reply = _resolve_smalltalk_text_reply(content)
        if smalltalk_reply is not None:
            logger.info("忽略闲聊短句 from %s: %s", sender, content[:80], extra=extra)
            await ws_client.reply_stream(
                frame,
                stream_id,
                smalltalk_reply,
                True,
            )
            return
        if instruction_only_reply is not None:
            logger.info("忽略独立审核指令 from %s: %s", sender, content[:80], extra=extra)
            await ws_client.reply_stream(
                frame,
                stream_id,
                instruction_only_reply,
                True,
            )
            return

        recent_submission_tracker.remember(sender, "text")
        logger.info("收到文字消息 from %s: %s...", sender, content[:80], extra=extra)
        try:
            message_id = extract_message_id(frame)
            if not message_id:
                raise ValueError("企业微信消息缺少稳定消息标识")
            submission = review_tasks.submit_text(
                channel="wecom",
                sender_userid=sender,
                sender_name=english_name,
                message_id=message_id,
                text=content,
            )
        except Exception as exc:
            logger.exception("文字审核任务入队失败 from %s", sender, exc_info=exc, extra=extra)
            await notifier.notify_text_review_error(sender, exc)
            await ws_client.reply_stream(
                frame, generate_req_id("review-err"),
                _build_processing_failure_user_reply("文字审核任务受理", exc), True,
            )
            return
        await _reply_queued_review_acceptance(
            ws_client,
            frame,
            generate_req_id("review-text-queued"),
            review_label="文字审核",
            created=submission.created,
            input_label="这段文字",
        )

    async def on_file(frame):
        sender = get_sender_id(frame)
        english_name = registry.get_name(sender) or sender
        extra = log_extra(sender, english_name)
        stream_id = generate_req_id("review-file")

        # 注册流程:未注册用户先要求发送英文名
        if registration_flow.should_ask_name(sender):
            logger.info("新用户 %s 首次使用,索要英文名", sender, extra=extra)
            await ws_client.reply_stream(
                frame, stream_id,
                registration_flow.ask_name_message(), True,
            )
            return

        pending_mode = review_intake_store.pending_mode(
            channel="wecom",
            sender_userid=sender,
        )

        recent_submission_tracker.remember(sender, "file")

        payload = extract_file_payload(frame)
        if payload is None:
            recent_submission_tracker.forget(sender)
            logger.warning("文件消息格式异常 from %s", sender, extra=extra)
            await ws_client.reply_stream(
                frame, stream_id,
                "文件消息格式异常(找不到下载地址)。", True,
            )
            return

        # 1. ACK（快速回复，不进队列）
        await ws_client.reply_stream(
            frame, stream_id,
            _build_file_ack(pending_mode),
            True,
        )

        # 2. 下载(SDK 内部从 HTTP Content-Disposition 拿真实 filename)
        try:
            result = await ws_client.download_file(payload.url, payload.aes_key)
        except Exception as exc:
            recent_submission_tracker.forget(sender)
            logger.exception("下载文件失败 from %s", sender, exc_info=exc, extra=extra)
            await notifier.notify_file_review_error(sender, payload.filename or "unknown", "下载", exc)
            await ws_client.reply_stream(
                frame, generate_req_id("review-err"),
                _build_processing_failure_user_reply("下载文件", exc), True,
            )
            return

        buffer = result.get("buffer", b"")
        filename = result.get("filename") or "unknown"
        logger.info("下载完成: filename=%s, size=%d 字节 from %s", filename, len(buffer), sender, extra=extra)

        # 3. 检查后缀(用下载回来的真实文件名)
        if not is_supported_review_filename(filename):
            recent_submission_tracker.forget(sender)
            logger.info("拒接非审核格式文件 from %s: %s", sender, filename, extra=extra)
            await ws_client.reply_stream(
                frame, generate_req_id("review-reject"),
                _review_file_rejection_message(filename), True,
            )
            return

        # 4. 文件大小检查
        size_mb = len(buffer) / 1024 / 1024
        if size_mb > config.max_file_size_mb:
            recent_submission_tracker.forget(sender)
            logger.warning(
                "文件过大 from %s: %.1fMB, 上限 %dMB", sender, size_mb, config.max_file_size_mb, extra=extra
            )
            await ws_client.reply_stream(
                frame, generate_req_id("review-err"),
                f"文件过大({size_mb:.1f}MB,上限 {config.max_file_size_mb}MB),暂不支持。", True,
            )
            return

        # PPTX 是独立单文件审核，不进入 Word 暂存、格式审核或联合审核。
        if is_pptx_filename(filename):
            if pending_mode in {"format", "multi"}:
                recent_submission_tracker.forget(sender)
                await ws_client.reply_stream(
                    frame,
                    generate_req_id("review-reject"),
                    "PPT仅支持单文件低级错误审核，不参与公文格式或多文件联合审核。",
                    True,
                )
                return
            try:
                message_id = extract_message_id(frame)
                if not message_id:
                    raise ValueError("企业微信消息缺少稳定消息标识")
                submission = review_tasks.submit_file(
                    channel="wecom",
                    sender_userid=sender,
                    sender_name=english_name,
                    message_id=message_id,
                    task_type=PPT_REVIEW_TASK_TYPE,
                    filename=filename,
                    file_bytes=buffer,
                )
            except Exception as exc:
                recent_submission_tracker.forget(sender)
                logger.exception("PPT审核任务入队失败 from %s", sender, exc_info=exc, extra=extra)
                await notifier.notify_file_review_error(sender, filename, "PPT审核任务入队", exc)
                await ws_client.reply_stream(
                    frame,
                    generate_req_id("review-err"),
                    _build_processing_failure_user_reply("PPT审核任务受理", exc),
                    True,
                )
                return
            await _reply_queued_review_acceptance(
                ws_client,
                frame,
                generate_req_id("review-ppt-queued"),
                review_label="PPT低级错误审核",
                created=submission.created,
                input_label="这份PPT",
                acknowledgment_already_sent=True,
            )
            return

        if is_html_filename(filename):
            if pending_mode == "format":
                recent_submission_tracker.forget(sender)
                await ws_client.reply_stream(
                    frame,
                    generate_req_id("review-reject"),
                    "公文格式审核只支持 .docx 文件；HTML仅支持可见文字和数据一致性审核。",
                    True,
                )
                return
            if pending_mode == "multi":
                recent_submission_tracker.forget(sender)
                await ws_client.reply_stream(
                    frame,
                    generate_req_id("review-reject"),
                    "HTML暂不支持多文件联合审核，请取消当前联合审核后单独发送。",
                    True,
                )
                return
            try:
                message_id = extract_message_id(frame)
                if not message_id:
                    raise ValueError("企业微信消息缺少稳定消息标识")
                submission = review_tasks.submit_file(
                    channel="wecom",
                    sender_userid=sender,
                    sender_name=english_name,
                    message_id=message_id,
                    task_type=GENERAL_HTML_REVIEW_TASK_TYPE,
                    filename=filename,
                    file_bytes=buffer,
                )
            except Exception as exc:
                recent_submission_tracker.forget(sender)
                logger.exception(
                    "HTML文字审核任务入队失败 from %s",
                    sender,
                    exc_info=exc,
                    extra=extra,
                )
                await notifier.notify_file_review_error(
                    sender,
                    filename,
                    "HTML文字审核任务入队",
                    exc,
                )
                await ws_client.reply_stream(
                    frame,
                    generate_req_id("review-err"),
                    _build_processing_failure_user_reply("HTML文字审核任务受理", exc),
                    True,
                )
                return
            await _reply_queued_review_acceptance(
                ws_client,
                frame,
                generate_req_id("review-html-queued"),
                review_label="HTML文字审核",
                created=submission.created,
                input_label="这份HTML文件",
                acknowledgment_already_sent=True,
            )
            return

        try:
            intake_decision = review_intake_store.add_file(
                channel="wecom",
                sender_userid=sender,
                message_id=extract_message_id(frame),
                file=UploadedFile(
                    filename=filename,
                    content=buffer,
                    content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ),
            )
        except Exception as exc:
            recent_submission_tracker.forget(sender)
            logger.exception("审核文件暂存失败 from %s", sender, exc_info=exc, extra=extra)
            await notifier.notify_file_review_error(sender, filename, "文件暂存", exc)
            await ws_client.reply_stream(
                frame,
                generate_req_id("review-err"),
                _build_processing_failure_user_reply("文件暂存", exc),
                True,
            )
            return

        if intake_decision.action == "duplicate":
            await ws_client.reply_stream(
                frame,
                generate_req_id("review-duplicate"),
                intake_decision.reply,
                True,
            )
            return

        if intake_decision.action == "wait":
            await ws_client.reply_stream(
                frame,
                generate_req_id("review-intake"),
                intake_decision.reply,
                True,
            )
            return
        if intake_decision.action == "wait_auto":
            intake_decision = await _settle_auto_review_batch(
                review_intake_store,
                channel="wecom",
                sender_userid=sender,
                expected_revision=intake_decision.revision,
                delay_seconds=config.auto_batch_seconds,
            )
            if intake_decision.action == "stale":
                logger.info(
                    "检测到同一用户后续文件，当前文件并入最新自动审核批次: %s",
                    sender,
                    extra=extra,
                )
                return
            if intake_decision.action == "wait":
                await ws_client.reply_stream(
                    frame,
                    generate_req_id("review-intake"),
                    intake_decision.reply,
                    True,
                )
                return
            if intake_decision.action == "run_multi":
                await run_multi_file_decision(frame, sender, intake_decision)
                return
            if intake_decision.action != "run_single":
                raise RuntimeError(f"未知的自动审核决策: {intake_decision.action}")
            queued_file = intake_decision.files[0]
            filename = queued_file.filename
            buffer = queued_file.read_bytes()
            review_intake_store.cleanup_files(
                intake_decision.files,
                channel="wecom",
                sender_userid=sender,
            )
        if intake_decision.action == "run_format":
            await run_official_format_decision(
                frame,
                sender,
                intake_decision,
                acknowledgment_already_sent=True,
            )
            return

        # 5. 仅在入口解析一次，用于识别单文件审核类型。
        tmp_path: Path | None = None
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
                tmp.write(buffer)
                tmp_path = Path(tmp.name)
            parsed = _parse_docx(tmp_path)
        except Exception as exc:
            recent_submission_tracker.forget(sender)
            logger.exception("文件解析失败 from %s", sender, exc_info=exc, extra=extra)
            await notifier.notify_file_review_error(sender, filename, "解析", exc)
            await ws_client.reply_stream(
                frame, generate_req_id("review-err"),
                _build_processing_failure_user_reply("文件解析", exc), True,
            )
            return
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

        # 6. 识别文档类型并分发到对应审核引擎
        doc_type = detect_document_type(filename, parsed.paragraphs)
        logger.info(
            "文档类型识别: %s (%s) from %s",
            document_type_label(doc_type),
            filename,
            sender,
            extra=extra,
        )

        review_label = _review_label(doc_type)
        try:
            message_id = extract_message_id(frame)
            if not message_id:
                raise ValueError("企业微信消息缺少稳定消息标识")
            submission = review_tasks.submit_file(
                channel="wecom",
                sender_userid=sender,
                sender_name=english_name,
                message_id=message_id,
                task_type=_review_task_type_for_document_type(doc_type),
                filename=filename,
                file_bytes=buffer,
            )
        except Exception as exc:
            logger.exception(
                "%s任务入队失败 from %s",
                review_label,
                sender,
                exc_info=exc,
                extra=extra,
            )
            await notifier.notify_file_review_error(sender, filename, f"{review_label}任务入队", exc)
            await ws_client.reply_stream(
                frame,
                generate_req_id("review-err"),
                _build_processing_failure_user_reply(f"{review_label}任务受理", exc),
                True,
            )
            return
        await _reply_queued_review_acceptance(
            ws_client,
            frame,
            generate_req_id("review-queued"),
            review_label=review_label,
            created=submission.created,
            input_label="这份文件",
            acknowledgment_already_sent=True,
        )
        return

    async def on_enter(frame):
        sender = get_sender_id(frame)
        await ws_client.reply_welcome(
            frame,
            {
                "msgtype": "text",
                "text": {
                    "content": _build_enter_welcome_text(registration_flow, sender)
                },
            },
        )

    ws_client.on("message.text", on_text)
    ws_client.on("message.file", on_file)
    ws_client.on("event.enter_chat", on_enter)

    await ws_client.connect()
    task_stop_event = asyncio.Event()
    task_worker = asyncio.create_task(
        _run_review_task_worker_supervised(
            task_executor=task_executor,
            stop_event=task_stop_event,
            poll_interval=config.task_poll_seconds,
            worker_count=config.task_worker_count,
            recovery_interval=config.task_recovery_seconds,
            ops_event_logger=ops_event_logger,
        ),
        name="review-persistent-task-worker",
    )
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        await ws_client.disconnect()
    finally:
        task_stop_event.set()
        await asyncio.gather(task_worker, return_exceptions=True)
        heartbeat_task.cancel()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="M-Agent 智能审核 Bot")
    parser.add_argument("--check-config", action="store_true", help="只检查本地配置")
    parser.add_argument(
        "--console",
        action="store_true",
        help="同时输出日志到控制台(默认只写文件)",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config()
        runtime = RuntimeEnvironment(
            mode=config.runtime_mode,
            data_root=config.data_root or config.reviews_dir.parent,
            values={},
        )
        validate_bot_startup(
            runtime,
            data_paths=(
                config.reviews_dir,
                config.logs_dir,
                config.ops_events_dir,
                config.ops_heartbeat_dir,
                config.user_registry_path,
                config.intake_dir,
                config.task_queue_db,
            ),
            project_root=_ROOT,
        )
    except RuntimeEnvironmentError as exc:
        print(f"错误：{exc}")
        return

    # 配置结构化日志
    setup_logging(
        config.logs_dir,
        console_output=args.console,
        max_bytes=config.log_max_bytes,
    )
    redirect_stdout_to_logging(logger)

    if args.check_config:
        logger.info("配置检查通过。", extra=log_extra("system", "system"))
        logger.info("运行环境: %s", config.runtime_mode, extra=log_extra("system", "system"))
        logger.info("Bot ID: %s...", config.wecom_bot_id[:8], extra=log_extra("system", "system"))
        logger.info("规则库: %s", config.rules_path, extra=log_extra("system", "system"))
        logger.info("存档目录: %s", config.reviews_dir, extra=log_extra("system", "system"))
        logger.info("日志目录: %s", config.logs_dir, extra=log_extra("system", "system"))
        logger.info(
            "单个日志文件上限: %d MB",
            config.log_max_bytes // 1024 // 1024,
            extra=log_extra("system", "system"),
        )
        if config.admin_user_id:
            logger.info(
                "管理员: %s (%s)", config.admin_name, config.admin_user_id,
                extra=log_extra("system", "system"),
            )
        return

    logger.info("正在连接企业微信审核 Bot。按 Ctrl+C 可停止。", extra=log_extra("system", "system"))
    asyncio.run(run_review_bot(config))


if __name__ == "__main__":
    main()
