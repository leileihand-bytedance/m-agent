"""智能审核 Bot 入口 (独立进程).

功能:
  - 接入企业微信长连接(独立 Bot,只做审核)
  - 接收文件消息 → 检查后缀(.docx 接受,其他拒)
  - 解析 .docx → 加载 rules.md → 跑审核引擎 → 输出意见
  - 存档到 data/reviews/<日期-序号>/
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

# 让 import app.* 找得到
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from app.review import load_rules, format_review_result  # noqa: E402
from app.review.reviewer import ReviewResult  # noqa: E402


# ============================================================
# 配置加载
# ============================================================

@dataclass(frozen=True)
class ReviewConfig:
    wecom_bot_id: str
    wecom_bot_secret: str
    rules_path: Path
    reviews_dir: Path
    max_file_size_mb: int = 10


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


def load_config(env_path: Path | None = None) -> ReviewConfig:
    if env_path is None:
        env_path = _ROOT / ".env"
    values = parse_env_file(env_path)
    rules_path = Path(values.get("M_AGENT_REVIEW_RULES", "app/data/rules.md") or "app/data/rules.md")
    if not rules_path.is_absolute():
        rules_path = _ROOT / rules_path
    reviews_dir = Path(values.get("M_AGENT_REVIEWS_DIR", "data/reviews") or "data/reviews")
    if not reviews_dir.is_absolute():
        reviews_dir = _ROOT / reviews_dir
    return ReviewConfig(
        wecom_bot_id=require_value(values, "WECOM_REVIEW_BOT_ID"),
        wecom_bot_secret=require_value(values, "WECOM_REVIEW_BOT_SECRET"),
        rules_path=rules_path,
        reviews_dir=reviews_dir,
    )


# ============================================================
# 拒接非审核消息
# ============================================================

REJECT_MESSAGE = "本入口仅接收审核文档(.docx),请直接发送文件"


def is_docx_filename(filename: str | None) -> bool:
    """判断文件名是否为 .docx."""
    if not filename:
        return False
    return filename.lower().endswith(".docx")


# ============================================================
# .docx 解析(复用 app.review.parser,这里直接 import)
# ============================================================

from app.review.parser import parse_docx as _parse_docx  # noqa: E402


# ============================================================
# 存档管理
# ============================================================

def _next_review_index(reviews_dir: Path, date_str: str) -> int:
    """取当天下一个序号(从 001 开始)."""
    existing = [
        d for d in reviews_dir.iterdir()
        if d.is_dir() and d.name.startswith(date_str)
    ]
    return len(existing) + 1


def save_review(
    *,
    reviews_dir: Path,
    file_bytes: bytes,
    original_filename: str,
    sender: str,
    msgid: str,
    result: ReviewResult,
    parsed_paragraphs: list[str],
) -> Path:
    """保存审核记录到 data/reviews/<日期-序号>/.

    目录结构:
      data/reviews/2026-06-13-001/
        source/原文件名.docx
        report.md            (审核意见)
        meta.md              (时间 / Bot ID / 触发用户)
    """
    date_str = datetime.now().strftime("%Y%m%d")
    idx = _next_review_index(reviews_dir, date_str)
    review_dir = reviews_dir / f"{date_str}-{idx:03d}"
    source_dir = review_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    # 1. 保存原始文件
    original = original_filename or "uploaded.docx"
    # 先剥离扩展名(只允许 .docx)
    if original.lower().endswith(".docx"):
        stem = original[:-5]  # 去掉 .docx
        ext = ".docx"
    else:
        stem = Path(original).stem  # 拿无扩展名部分
        ext = ".docx"  # 强制 .docx
    # 清洗 stem:剥离所有 . 和 / 和 \
    safe_stem = re.sub(r'[^\w\u4e00-\u9fff\-_]', '_', stem)
    safe_name = safe_stem + ext
    source_path = source_dir / safe_name
    source_path.write_bytes(file_bytes)

    # 2. 保存 report.md
    report_path = review_dir / "report.md"
    report_path.write_text(
        format_review_result(result, original_filename),
        encoding="utf-8",
    )

    # 3. 保存 meta.md
    meta_path = review_dir / "meta.md"
    meta_path.write_text(
        "\n".join(
            [
                "# 审核记录元信息",
                "",
                f"**文件:** {original_filename}",
                f"**发送人:** {sender}",
                f"**消息 ID:** {msgid}",
                f"**审核时间:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"**规则数:** {result.total_rules}",
                f"**通过规则:** {result.passed_rules}",
                f"**发现问题:** {len(result.findings)}",
                "",
                "## 解析预览",
                "",
            ]
            + [f"- 段{i+1}: {p[:80]}" for i, p in enumerate(parsed_paragraphs[:10])]
        ),
        encoding="utf-8",
    )

    return review_dir


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


# ============================================================
# 主流程
# ============================================================

async def run_review_bot(config: ReviewConfig) -> None:
    try:
        from wecom_aibot_sdk import WSClient, generate_req_id
    except ImportError as exc:
        raise RuntimeError(
            "缺少依赖 wecom-aibot-sdk。请先安装:python -m pip install -r app/requirements.txt"
        ) from exc

    global _pending_queue
    _pending_queue = PendingReplyQueue()

    config.reviews_dir.mkdir(parents=True, exist_ok=True)
    if not config.rules_path.exists():
        raise RuntimeError(f"规则库文件不存在: {config.rules_path}")

    rules_text = load_rules(str(config.rules_path))
    print(f"✅ 规则库已加载: {len(rules_text)} 字符 (来源: {config.rules_path})", flush=True)
    print(f"✅ 审核存档目录: {config.reviews_dir}", flush=True)

    ws_client = WSClient(
        bot_id=config.wecom_bot_id,
        secret=config.wecom_bot_secret,
    )

    ws_client.on("connected", lambda: print("企业微信长连接已建立。", flush=True))
    ws_client.on("authenticated", lambda: print("企业微信审核 Bot 认证成功,等待文件消息。", flush=True))
    ws_client.on("disconnected", lambda reason: print(f"企业微信连接已断开:{reason}", flush=True))
    ws_client.on("reconnecting", lambda attempt: print(f"企业微信正在重连,第 {attempt} 次。", flush=True))
    ws_client.on("error", lambda error: print(f"企业微信连接错误:{error}", flush=True))

    async def on_text(frame):
        """文本消息一律拒绝(本入口只接文件)."""
        stream_id = generate_req_id("review-reject")
        await ws_client.reply_stream(frame, stream_id, REJECT_MESSAGE, True)
        print(f"拒接文本消息:{get_sender_id(frame)}", flush=True)

    async def on_file(frame):
        sender = get_sender_id(frame)
        stream_id = generate_req_id("review-file")

        payload = extract_file_payload(frame)
        if payload is None:
            await ws_client.reply_stream(
                frame, stream_id,
                "文件消息格式异常(找不到下载地址)。", True,
            )
            return

        # 1. ACK（快速回复，不进队列）
        await ws_client.reply_stream(
            frame, stream_id,
            "已收到文件,在努力审核了,请稍等……", True,
        )

        # 2. 下载(SDK 内部从 HTTP Content-Disposition 拿真实 filename)
        try:
            result = await ws_client.download_file(payload.url, payload.aes_key)
        except Exception as exc:
            await ws_client.reply_stream(
                frame, generate_req_id("review-err"),
                f"下载文件失败:{exc}", True,
            )
            return

        buffer = result.get("buffer", b"")
        filename = result.get("filename") or "unknown.docx"
        print(f"📄 下载完成:filename={filename},size={len(buffer)} 字节", flush=True)

        # 3. 检查后缀(用下载回来的真实文件名)
        if not is_docx_filename(filename):
            await ws_client.reply_stream(
                frame, generate_req_id("review-reject"),
                f"❌ 本入口仅接收 .docx 文件,你发的是: {filename}", True,
            )
            print(f"拒接非 .docx 文件:{filename}", flush=True)
            return

        # 4. 文件大小检查
        size_mb = len(buffer) / 1024 / 1024
        if size_mb > config.max_file_size_mb:
            await ws_client.reply_stream(
                frame, generate_req_id("review-err"),
                f"文件过大({size_mb:.1f}MB,上限 {config.max_file_size_mb}MB),暂不支持。", True,
            )
            return

        # 5. 解析
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
                tmp.write(buffer)
                tmp_path = Path(tmp.name)
            parsed = _parse_docx(tmp_path)
            tmp_path.unlink()
        except Exception as exc:
            await ws_client.reply_stream(
                frame, generate_req_id("review-err"),
                f"文件解析失败:{exc}", True,
            )
            return

        # 6. 第一阶段审核（格式正则 + 基础内容 LLM）
        from app.review.reviewer import review_phase1, review_phase2  # noqa: E402
        from app.review.output_formatter import format_phase1_result, format_phase2_result  # noqa: E402

        phase1_result = review_phase1(parsed.paragraphs, rules_text, filename)

        # 立即发第一阶段结果
        phase1_reply = format_phase1_result(phase1_result)
        done_id_1 = generate_req_id("review-p1")
        try:
            await asyncio.wait_for(
                ws_client.reply_stream(frame, done_id_1, phase1_reply, True),
                timeout=30.0,
            )
            print(f"✅ 第一阶段结果已发送", flush=True)
        except asyncio.TimeoutError:
            print(f"⚠️ 第一阶段发送超时", flush=True)
        except Exception as exc:
            print(f"⚠️ 第一阶段发送失败:{exc}", flush=True)

        # 7. 第二阶段审核（深度内容 LLM）
        phase2_result = review_phase2(parsed.paragraphs, rules_text, filename)

        # 8. 存档（完整结果存档到 phase2_result）
        msgid = str(frame.get("body", {}).get("msgid", "") or frame.get("headers", {}).get("req_id", ""))
        review_dir = None
        try:
            review_dir = save_review(
                reviews_dir=config.reviews_dir,
                file_bytes=buffer,
                original_filename=filename,
                sender=sender,
                msgid=msgid,
                result=phase2_result,
                parsed_paragraphs=parsed.paragraphs,
            )
            print(f"✅ 审核完成:{filename} ({len(phase2_result.findings)} 个问题),存档:{review_dir}", flush=True)
        except Exception as exc:
            print(f"⚠️ 存档失败:{exc}", flush=True)

        # 9. 追加发送第二阶段结果（带重试）
        phase2_reply = format_phase2_result(phase2_result)
        done_id_2 = generate_req_id("review-p2")
        phase2_sent = False
        for retry in range(3):
            try:
                await asyncio.wait_for(
                    ws_client.reply_stream(frame, done_id_2, phase2_reply, True),
                    timeout=30.0,
                )
                print(f"✅ 第二阶段结果已发送", flush=True)
                phase2_sent = True
                break
            except asyncio.TimeoutError:
                print(f"⚠️ 第二阶段发送超时，第 {retry+1} 次重试...", flush=True)
            except Exception as exc:
                print(f"⚠️ 第二阶段发送失败:{exc}，第 {retry+1} 次重试...", flush=True)
            if retry < 2:
                await asyncio.sleep(2 * (retry + 1))  # 2s, 4s backoff
        if not phase2_sent:
            print(f"⚠️ 第二阶段结果发送失败（已重试3次），存档已保存:{review_dir}/report.md", flush=True)

    async def on_enter(frame):
        await ws_client.reply_welcome(
            frame,
            {
                "msgtype": "text",
                "text": {
                    "content": "您好,我是 M-Agent 智能审核 Bot。请直接发送 .docx 文档,我会按规则标出低级错误。"
                },
            },
        )

    ws_client.on("message.text", on_text)
    ws_client.on("message.file", on_file)
    ws_client.on("event.enter_chat", on_enter)

    await ws_client.connect()
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        await ws_client.disconnect()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="M-Agent 智能审核 Bot")
    parser.add_argument("--check-config", action="store_true", help="只检查本地配置")
    parser.add_argument("--log-file", type=str, default=None, help="日志文件路径(便于后台调试)")
    args = parser.parse_args(argv)

    # 如果指定了 --log-file,所有 stdout 重定向到该文件
    if args.log_file:
        log_file = open(args.log_file, "a", buffering=1, encoding="utf-8")  # 行缓冲
        import sys as _sys
        _sys.stdout = log_file
        _sys.stderr = log_file

    config = load_config()
    if args.check_config:
        print("配置检查通过。")
        print(f"Bot ID: {config.wecom_bot_id[:8]}...")
        print(f"规则库: {config.rules_path}")
        print(f"存档目录: {config.reviews_dir}")
        return

    print("正在连接企业微信审核 Bot。按 Ctrl+C 可停止。", flush=True)
    asyncio.run(run_review_bot(config))


if __name__ == "__main__":
    main()
