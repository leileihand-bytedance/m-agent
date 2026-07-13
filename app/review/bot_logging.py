"""审核 Bot 日志配置.

特性:
  - 使用 Python 标准 logging 模块
  - 公共日志: M-Agent-Files/runtime/logs/review-bot-YYYY-MM-DD.log
  - 用户日志: M-Agent-Files/runtime/logs/users/<userid>/YYYY-MM-DD.log
  - 按天切分，单文件超限后继续生成 part-002、part-003
  - system 日志只写公共日志，不在 users/system 下重复保存
  - 日志格式包含时间、级别、用户名(userid/english_name)
"""

from __future__ import annotations

import logging
import re
import sys
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable


DEFAULT_LOG_MAX_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_OPEN_USER_HANDLERS = 64


@dataclass(frozen=True)
class LogConfig:
    """日志配置."""

    logs_dir: Path
    console_output: bool = False


class _DailySizeFileHandler(logging.FileHandler):
    """按天切换，并在单文件超限后继续分片的 Handler。"""

    def __init__(
        self,
        logs_dir: Path,
        filename_template: str,
        *,
        max_bytes: int = DEFAULT_LOG_MAX_BYTES,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.logs_dir = logs_dir
        self.filename_template = filename_template
        self.max_bytes = max(1, int(max_bytes))
        self._now_provider = now_provider or datetime.now
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        now = self._now_provider()
        self._current_day = now.date()
        self._current_part, self._current_path = self._select_current_path(now)
        super().__init__(self._current_path, encoding="utf-8")

    def _compute_base_path(self, now: datetime) -> Path:
        filename = self.filename_template.format(
            year=now.year,
            month=now.month,
            day=now.day,
        )
        path = self.logs_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _part_path(base_path: Path, part: int) -> Path:
        if part <= 1:
            return base_path
        return base_path.with_name(f"{base_path.stem}.part-{part:03d}{base_path.suffix}")

    def _select_current_path(self, now: datetime) -> tuple[int, Path]:
        base_path = self._compute_base_path(now)
        candidates: list[tuple[int, Path]] = []
        if base_path.exists():
            candidates.append((1, base_path))
        part_pattern = re.compile(
            rf"^{re.escape(base_path.stem)}\.part-(\d{{3}}){re.escape(base_path.suffix)}$"
        )
        for path in base_path.parent.glob(f"{base_path.stem}.part-*{base_path.suffix}"):
            match = part_pattern.match(path.name)
            if match:
                candidates.append((int(match.group(1)), path))
        if not candidates:
            return 1, base_path
        part, path = max(candidates, key=lambda item: item[0])
        if path.stat().st_size >= self.max_bytes:
            part += 1
            path = self._part_path(base_path, part)
        return part, path

    def _switch_to(self, path: Path) -> None:
        if self.stream is not None:
            self.stream.flush()
            self.stream.close()
        self._current_path = path
        self.baseFilename = str(path.resolve(strict=False))
        self.stream = self._open()

    def _check_rotation(self, record: logging.LogRecord) -> None:
        now = self._now_provider()
        if self._current_day != now.date():
            self._current_day = now.date()
            self._current_part, path = self._select_current_path(now)
            self._switch_to(path)

        message_bytes = len((self.format(record) + self.terminator).encode("utf-8"))
        current_size = self._current_path.stat().st_size if self._current_path.exists() else 0
        if current_size > 0 and current_size + message_bytes > self.max_bytes:
            self._current_part += 1
            base_path = self._compute_base_path(now)
            self._switch_to(self._part_path(base_path, self._current_part))

    def emit(self, record: logging.LogRecord) -> None:
        self._check_rotation(record)
        super().emit(record)


class _UserDailySizeFileHandler(logging.Handler):
    """按用户、按天和大小分片，并限制同时打开的文件数量。"""

    def __init__(
        self,
        logs_dir: Path,
        *,
        max_bytes: int = DEFAULT_LOG_MAX_BYTES,
        max_open_handlers: int = DEFAULT_MAX_OPEN_USER_HANDLERS,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__()
        self.logs_dir = logs_dir
        self.max_bytes = max(1, int(max_bytes))
        self.max_open_handlers = max(1, int(max_open_handlers))
        self._now_provider = now_provider or datetime.now
        self._handlers: OrderedDict[str, _DailySizeFileHandler] = OrderedDict()

    @property
    def open_handler_count(self) -> int:
        return len(self._handlers)

    def _get_handler(self, record: logging.LogRecord) -> _DailySizeFileHandler:
        userid = str(getattr(record, "userid", "unknown") or "unknown")
        safe_userid = _safe_user_dir_name(userid)
        handler = self._handlers.get(safe_userid)
        if handler is not None:
            self._handlers.move_to_end(safe_userid)
            return handler

        user_dir = self.logs_dir / "users" / safe_userid
        handler = _DailySizeFileHandler(
            user_dir,
            "{year}-{month:02d}-{day:02d}.log",
            max_bytes=self.max_bytes,
            now_provider=self._now_provider,
        )
        handler.setFormatter(self.formatter)
        self._handlers[safe_userid] = handler
        while len(self._handlers) > self.max_open_handlers:
            _, oldest = self._handlers.popitem(last=False)
            oldest.close()

        return handler

    def emit(self, record: logging.LogRecord) -> None:
        if str(getattr(record, "userid", "system") or "system") == "system":
            return
        handler = self._get_handler(record)
        handler.emit(record)

    def close(self) -> None:
        for handler in list(self._handlers.values()):
            handler.close()
        self._handlers.clear()
        super().close()


def _safe_user_dir_name(userid: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(userid or "unknown")).strip(".")
    if not cleaned or cleaned in {".", ".."}:
        return "unknown"
    return cleaned[:128]


class _UserContextFilter(logging.Filter):
    """为每条日志记录补齐 userid 和 english_name 字段."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "userid"):
            record.userid = "system"
        if not hasattr(record, "english_name"):
            record.english_name = record.userid
        return True


def _make_formatter() -> logging.Formatter:
    """构造日志格式器.

    格式: [2026-07-07 10:30:00] [INFO] [user=test-user|userid=xxx] message
    """
    return logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] [user=%(english_name)s|userid=%(userid)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def setup_logging(
    logs_dir: Path,
    *,
    console_output: bool = False,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
) -> logging.Logger:
    """配置并返回审核 Bot 的根 logger.

    Args:
        logs_dir: 日志根目录,默认由 M_AGENT_DATA_DIR 派生
        console_output: 是否同时输出到控制台,默认 False

    Returns:
        配置好的 logger 实例
    """
    logger = logging.getLogger("review_bot")
    logger.setLevel(logging.DEBUG)

    # 避免重复配置
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()

    # 补齐 user 字段
    logger.addFilter(_UserContextFilter())

    formatter = _make_formatter()

    # 1. 公共日志: 所有日志都进这里
    main_handler = _DailySizeFileHandler(
        logs_dir,
        "review-bot-{year}-{month:02d}-{day:02d}.log",
        max_bytes=max_bytes,
    )
    main_handler.setFormatter(formatter)
    logger.addHandler(main_handler)

    # 2. 用户日志: 按用户分文件
    user_handler = _UserDailySizeFileHandler(logs_dir, max_bytes=max_bytes)
    user_handler.setFormatter(formatter)
    logger.addHandler(user_handler)

    # 3. 控制台输出(调试用)
    if console_output:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        logger.addHandler(console)

    return logger


def log_extra(userid: str = "unknown", english_name: str | None = None) -> dict[str, object]:
    """构造日志 extra 字段,带用户身份信息."""
    return {
        "userid": userid or "unknown",
        "english_name": english_name or userid or "unknown",
    }


def redirect_stdout_to_logging(logger: logging.Logger) -> None:
    """把 print 输出也重定向到 logger,兼容旧代码中的 print 调试."""
    class _LogStream:
        def __init__(self, target: logging.Logger, level: int):
            self.target = target
            self.level = level
            self.buffer = ""

        def write(self, message: str) -> int:
            self.buffer += message
            while "\n" in self.buffer:
                line, self.buffer = self.buffer.split("\n", 1)
                if line:
                    self.target.log(self.level, line)
            return len(message)

        def flush(self) -> None:
            if self.buffer:
                self.target.log(self.level, self.buffer)
                self.buffer = ""

    sys.stdout = _LogStream(logger, logging.INFO)
    sys.stderr = _LogStream(logger, logging.ERROR)
