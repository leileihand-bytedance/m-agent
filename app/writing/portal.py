from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import uuid4

from app.platform.gateway.wecom import format_text_reply
from app.platform.models import UploadedFile
from app.platform.ops.events import OpsEventLogger


ENTRY_LABELS = {
    "brief": "写简报",
    "direct_report": "写直报",
}
LOCAL_PREVIEW_USER = "local-preview-user"
SUPPORTED_UPLOAD_SUFFIXES = {".docx", ".pdf", ".pptx"}
MAX_PORTAL_REQUEST_BYTES = 20 * 1024 * 1024
MAX_PORTAL_FILES = 5
_PUBLIC_ERROR_PREFIXES = (
    "入口链接已失效",
    "请至少提供",
    "暂时只支持上传",
    "一次最多上传",
    "文件过大",
    "不支持的提交格式",
)


@dataclass(frozen=True)
class PortalConfig:
    host: str
    port: int
    base_url: str
    token_ttl_seconds: int = 1800


class PortalTokenStore:
    def __init__(self, ttl_seconds: int = 1800, time_fn: Callable[[], float] | None = None):
        self._ttl_seconds = ttl_seconds
        self._time_fn = time_fn or time.time
        self._tokens: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def issue(self, sender_userid: str) -> str:
        token = uuid4().hex
        with self._lock:
            self._tokens[token] = (sender_userid, self._time_fn())
        return token

    def resolve(self, token: str) -> str | None:
        with self._lock:
            entry = self._tokens.get(token)
            if not entry:
                return None
            sender_userid, created_at = entry
            if self._time_fn() - created_at > self._ttl_seconds:
                self._tokens.pop(token, None)
                return None
            return sender_userid


class PortalService:
    def __init__(
        self,
        *,
        platform_app,
        message_sender: Callable[[str, dict[str, object]], None],
        token_store: PortalTokenStore,
        dispatch: Callable[[Callable[..., None], tuple[object, ...]], None] | None = None,
        ops_event_logger: OpsEventLogger | None = None,
    ):
        self._platform_app = platform_app
        self._message_sender = message_sender
        self._token_store = token_store
        self._dispatch = dispatch or _dispatch_in_thread
        self._ops_event_logger = ops_event_logger

    def submit(
        self,
        *,
        entry_key: str,
        token: str,
        instruction: str,
        material_text: str,
        links: str,
        files: list[UploadedFile],
        preview: bool = False,
    ) -> None:
        sender_userid = LOCAL_PREVIEW_USER if preview else self._token_store.resolve(token)
        if not sender_userid:
            raise ValueError("入口链接已失效，请返回企业微信会话重新打开。")
        urls = parse_links(links)
        _validate_uploaded_files(files)
        if not urls and not files and not material_text.strip():
            raise ValueError("请至少提供一个链接、一个文件，或粘贴一段文字素材。")

        if not preview:
            self._message_sender(
                sender_userid,
                _text_body(f"已收到{ENTRY_LABELS[entry_key]}素材，正在处理，请稍后……"),
            )
        self._dispatch(
            self._process_submission,
            (sender_userid, entry_key, instruction, material_text, urls, files, preview),
        )

    def _process_submission(
        self,
        sender_userid: str,
        entry_key: str,
        instruction: str,
        material_text: str,
        urls: list[str],
        files: list[UploadedFile],
        preview: bool,
    ) -> None:
        try:
            result = self._platform_app.handle_structured_request(
                channel="wecom-portal",
                sender_userid=sender_userid,
                skill_id=entry_key,
                text=instruction,
                material_text=material_text,
                urls=urls,
                files=files,
            )
            reply = format_text_reply(result)
        except Exception as exc:  # noqa: BLE001
            print(f"上传页处理失败:{type(exc).__name__}: {exc}", flush=True)
            self._record_ops_event(
                severity="error",
                subject="素材页处理失败",
                detail=f"{type(exc).__name__}: {exc}",
                sender_userid=sender_userid,
                skill_id=entry_key,
            )
            reply = "处理失败，请稍后重试。"

        if not preview:
            self._message_sender(sender_userid, _text_body(reply))

    def _record_ops_event(
        self,
        *,
        severity: str,
        subject: str,
        detail: str,
        sender_userid: str,
        skill_id: str,
    ) -> None:
        if not self._ops_event_logger:
            return
        try:
            self._ops_event_logger.record(
                source="writing_portal",
                severity=severity,
                subject=subject,
                detail=detail,
                sender_userid=sender_userid,
                skill_id=skill_id,
            )
        except Exception as exc:
            print(f"素材页运维事件记录失败:{type(exc).__name__}: {exc}", flush=True)


def build_welcome_text(*, base_url: str, token: str) -> str:
    return (
        "您好，我可以帮您写简报和直报。\n"
        "如果有链接，直接把链接发给我，并告诉我是写简报还是写直报。\n"
        "如果有多个素材文档，建议先整合成一个文档再发给我，这样我处理起来会更稳一些。\n"
        "如果还有重点方向或想突出的信息，也欢迎一并告诉我。"
    )


def render_compose_page(*, entry_key: str, token: str, error: str = "", submitted: bool = False, preview: bool = False) -> str:
    title = ENTRY_LABELS[entry_key]
    banner = ""
    if error:
        banner = f'<div class="notice notice-error">{escape(error)}</div>'
    elif submitted:
        if preview:
            banner = '<div class="notice notice-ok">已提交。本地预览模式不会回企业微信，可到最近任务里查看结果。</div>'
        else:
            banner = '<div class="notice notice-ok">已提交。处理结果会返回企业微信对话。</div>'
    action_query = urlencode({"preview": "1"}) if preview else urlencode({"t": token})

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} - M-Agent</title>
  <style>
    :root {{
      --bg: linear-gradient(180deg, #f2efe8 0%, #f8f6f1 100%);
      --panel: rgba(255, 255, 255, 0.92);
      --text: #1c1c1c;
      --muted: #6a6a62;
      --line: #d8d2c6;
      --accent: #1f5c4f;
      --accent-soft: #e4efe9;
      --error: #9c3f2e;
      --error-soft: #f7e8e3;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", sans-serif;
      line-height: 1.55;
    }}
    .shell {{
      width: min(920px, calc(100% - 28px));
      margin: 32px auto 56px;
    }}
    .hero {{
      padding: 12px 4px 24px;
    }}
    .eyebrow {{
      color: var(--muted);
      font-size: 13px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 10px 0 10px;
      font-size: clamp(28px, 4vw, 42px);
      line-height: 1.08;
      font-weight: 700;
      font-family: "Songti SC", "STSong", "Noto Serif CJK SC", serif;
    }}
    .hero p {{
      margin: 0;
      color: var(--muted);
      font-size: 16px;
      max-width: 720px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid rgba(216, 210, 198, 0.9);
      border-radius: 22px;
      box-shadow: 0 18px 48px rgba(30, 34, 28, 0.08);
      padding: 24px;
      backdrop-filter: blur(8px);
    }}
    .notice {{
      margin-bottom: 18px;
      border-radius: 14px;
      padding: 12px 14px;
      font-size: 14px;
    }}
    .notice-ok {{
      background: var(--accent-soft);
      color: var(--accent);
    }}
    .notice-error {{
      background: var(--error-soft);
      color: var(--error);
    }}
    form {{
      display: grid;
      gap: 18px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
    }}
    label {{
      display: grid;
      gap: 8px;
      font-size: 14px;
      color: var(--muted);
    }}
    .label-title {{
      color: var(--text);
      font-weight: 600;
      font-size: 15px;
    }}
    input[type="file"],
    textarea {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: #fff;
      padding: 14px 14px;
      font: inherit;
      color: var(--text);
    }}
    textarea {{
      min-height: 140px;
      resize: vertical;
    }}
    input[type="file"] {{
      padding: 12px;
    }}
    .tips {{
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 13px;
      padding: 0 2px;
    }}
    button {{
      appearance: none;
      border: 0;
      border-radius: 999px;
      background: var(--accent);
      color: #fff;
      padding: 13px 22px;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
      width: fit-content;
    }}
    @media (max-width: 760px) {{
      .shell {{ width: calc(100% - 20px); margin-top: 18px; }}
      .panel {{ padding: 18px; border-radius: 18px; }}
      .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="hero">
      <div class="eyebrow">M-Agent 素材入口</div>
      <h1>{escape(title)}</h1>
      <p>把文件、链接和补充要求一次性放好，再提交。处理完成后，结果会直接返回企业微信对话。</p>
    </div>
    <section class="panel">
      {banner}
      <form method="post" action="/submit/{entry_key}?{action_query}" enctype="multipart/form-data">
        <div class="grid">
          <label>
            <span class="label-title">上传文件</span>
            <input type="file" name="files" multiple accept=".docx,.pdf,.pptx">
          </label>
          <label>
            <span class="label-title">粘贴链接</span>
            <textarea name="links" placeholder="每行一个链接，支持一次贴多个。"></textarea>
          </label>
        </div>
        <label>
          <span class="label-title">补充要求</span>
          <textarea name="instruction" placeholder="例如：请写得更正式一些，突出服务实体经济。"></textarea>
        </label>
        <label>
          <span class="label-title">补充文字素材</span>
          <textarea name="material_text" placeholder="如果有原文或补充段落，也可以直接粘贴在这里。"></textarea>
        </label>
        <div class="tips">
          <div>支持上传 Word、PDF、PPTX，也支持只贴链接或只贴文字素材。</div>
        </div>
        <button type="submit">提交处理</button>
      </form>
    </section>
  </div>
</body>
</html>"""


def parse_links(raw_text: str) -> list[str]:
    return [item for item in re.split(r"\s+", raw_text.strip()) if item.startswith(("http://", "https://"))]


def create_portal_handler(service: PortalService) -> type[BaseHTTPRequestHandler]:
    class PortalHandler(BaseHTTPRequestHandler):
        server_version = "MAgentPortal/0.1"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/compose/"):
                entry_key = parsed.path.rsplit("/", 1)[-1]
                if entry_key not in ENTRY_LABELS:
                    self._send_text("Not found", HTTPStatus.NOT_FOUND)
                    return
                preview = _is_preview(parse_qs(parsed.query))
                if preview:
                    if not _is_loopback_address(self.client_address[0]):
                        self._send_text("Forbidden", HTTPStatus.FORBIDDEN)
                        return
                    self._send_html(render_compose_page(entry_key=entry_key, token="", preview=True))
                    return
                token = _one(parse_qs(parsed.query), "t")
                if not service._token_store.resolve(token):
                    self._send_html(render_compose_page(entry_key=entry_key, token=token, error="入口链接已失效，请返回企业微信重新打开。"))
                    return
                self._send_html(render_compose_page(entry_key=entry_key, token=token))
                return
            self._send_text("Not found", HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if not parsed.path.startswith("/submit/"):
                self._send_text("Not found", HTTPStatus.NOT_FOUND)
                return
            entry_key = parsed.path.rsplit("/", 1)[-1]
            if entry_key not in ENTRY_LABELS:
                self._send_text("Not found", HTTPStatus.NOT_FOUND)
                return
            query = parse_qs(parsed.query)
            preview = _is_preview(query)
            token = _one(query, "t")
            if preview and not _is_loopback_address(self.client_address[0]):
                self._send_text("Forbidden", HTTPStatus.FORBIDDEN)
                return
            if not preview and not service._token_store.resolve(token):
                self._send_html(render_compose_page(entry_key=entry_key, token=token, error="入口链接已失效，请返回企业微信重新打开。"))
                return
            try:
                form, files = self._read_form_and_files()
                service.submit(
                    entry_key=entry_key,
                    token=token,
                    instruction=_one(form, "instruction"),
                    material_text=_one(form, "material_text"),
                    links=_one(form, "links"),
                    files=files,
                    preview=preview,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"素材页提交失败:{type(exc).__name__}: {exc}", flush=True)
                self._send_html(
                    render_compose_page(
                        entry_key=entry_key,
                        token=token,
                        error=_public_portal_error(exc),
                        preview=preview,
                    )
                )
                return
            self._send_html(render_compose_page(entry_key=entry_key, token=token, submitted=True, preview=preview))

        def log_message(self, format: str, *args: object) -> None:
            return

        def _read_form_and_files(self) -> tuple[dict[str, list[str]], list[UploadedFile]]:
            content_type = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", "0"))
            _validate_request_size(length, max_bytes=MAX_PORTAL_REQUEST_BYTES)
            body = self.rfile.read(length)
            if content_type.startswith("multipart/form-data"):
                return _parse_multipart_form(content_type, body)
            if content_type.startswith("application/x-www-form-urlencoded"):
                return parse_qs(body.decode("utf-8"), keep_blank_values=True), []
            raise ValueError("不支持的提交格式。")

        def _send_html(self, html: str) -> None:
            data = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_text(self, text: str, status: HTTPStatus) -> None:
            data = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return PortalHandler


def start_portal_server(config: PortalConfig, service: PortalService) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((config.host, config.port), create_portal_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"素材入口已启动：{config.base_url}")
    return server


def _parse_multipart_form(content_type: str, body: bytes) -> tuple[dict[str, list[str]], list[UploadedFile]]:
    raw = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
    message = BytesParser(policy=default).parsebytes(raw)
    form: dict[str, list[str]] = {}
    files: list[UploadedFile] = []
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        field_name = part.get_param("name", header="content-disposition") or ""
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            files.append(
                UploadedFile(
                    filename=filename,
                    content=payload,
                    content_type=part.get_content_type(),
                )
            )
            continue
        value = payload.decode(part.get_content_charset() or "utf-8")
        form.setdefault(field_name, []).append(value)
    return form, files


def _entry_url(base_url: str, entry_key: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/compose/{entry_key}?{urlencode({'t': token})}"


def _one(form: dict[str, list[str]], key: str) -> str:
    values = form.get(key) or [""]
    return values[0]


def _is_preview(query: dict[str, list[str]]) -> bool:
    return _one(query, "preview") == "1"


def _is_loopback_address(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return value.strip().lower() == "localhost"


def _validate_request_size(length: int, *, max_bytes: int) -> None:
    if length < 0 or length > max_bytes:
        raise ValueError(f"文件过大，单次提交上限为 {max_bytes // 1024 // 1024}MB。")


def _public_portal_error(exc: Exception) -> str:
    message = str(exc).strip()
    if isinstance(exc, ValueError) and message.startswith(_PUBLIC_ERROR_PREFIXES):
        return message
    return "提交失败，请稍后重试。"


def _text_body(content: str) -> dict[str, object]:
    return {
        "msgtype": "text",
        "text": {"content": content},
    }


def _validate_uploaded_files(files: list[UploadedFile]) -> None:
    if len(files) > MAX_PORTAL_FILES:
        raise ValueError(f"一次最多上传 {MAX_PORTAL_FILES} 个文件。")
    invalid_names = [
        item.filename
        for item in files
        if Path(item.filename or "").suffix.lower() not in SUPPORTED_UPLOAD_SUFFIXES
    ]
    if invalid_names:
        raise ValueError("暂时只支持上传 Word(.docx)、PDF(.pdf) 和 PPT(.pptx) 文件。")


def _dispatch_in_thread(fn: Callable[..., None], args: tuple[object, ...]) -> None:
    thread = threading.Thread(target=fn, args=args, daemon=True)
    thread.start()
