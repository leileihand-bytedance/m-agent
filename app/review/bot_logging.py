"""审核 Bot 日志配置.

特性:
  - 使用 Python 标准 logging 模块
  - 公共日志: M-Agent-Files/runtime/logs/review-bot-YYYY-MM.log
  - 用户日志: M-Agent-Files/runtime/logs/users/<userid>/YYYY-MM.log
  - 按月自动切分
  - 日志格式包含时间、级别、用户名(userid/english_name)
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import MutableMapping


@dataclass(frozen=True)
class LogConfig:
    """日志配置."""

    logs_dir: Path
    console_output: bool = False


class _MonthlyFileHandler(logging.FileHandler):
    """按月切换文件名的 Handler.

    文件名模板支持 {year} 和 {month:02d} 占位符.
    """

    def __init__(self, logs_dir: Path, filename_template: str) -> None:
        self.logs_dir = logs_dir
        self.filename_template = filename_template
        self._current_path = self._compute_path()
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        super().__init__(self._current_path, encoding="utf-8")

    def _compute_path(self) -> Path:
        now = datetime.now()
        filename = self.filename_template.format(year=now.year, month=now.month)
        path = self.logs_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _check_rotation(self) -> None:
        expected = self._compute_path()
        if self._current_path != expected:
            self._current_path = expected
            self.close()
            self.baseFilename = str(expected)
            self.stream = self._open()

    def emit(self, record: logging.LogRecord) -> None:
        self._check_rotation()
        super().emit(record)


class _UserMonthlyFileHandler(logging.Handler):
    """按用户 + 按月分文件的 Handler.

    每个 userid 每个月一个日志文件.
    """

    def __init__(self, logs_dir: Path) -> None:
        super().__init__()
        self.logs_dir = logs_dir
        self._handlers: dict[tuple[str, int, int], logging.FileHandler] = {}

    def _get_handler(self, record: logging.LogRecord) -> logging.FileHandler:
        userid = str(getattr(record, "userid", "unknown") or "unknown")
        now = datetime.now()
        key = (userid, now.year, now.month)

        handler = self._handlers.get(key)
        if handler is not None:
            # 检查是否需要切月
            current_path = self._compute_path(userid, now)
            if Path(handler.baseFilename) != current_path:
                handler.close()
                handler = None
                self._handlers.pop(key, None)

        if handler is None:
            current_path = self._compute_path(userid, now)
            handler = logging.FileHandler(current_path, encoding="utf-8")
            handler.setFormatter(self.formatter)
            self._handlers[key] = handler

        return handler

    def _compute_path(self, userid: str, now: datetime) -> Path:
        user_dir = self.logs_dir / "users" / userid
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir / f"{now.year}-{now.month:02d}.log"

    def emit(self, record: logging.LogRecord) -> None:
        handler = self._get_handler(record)
        handler.emit(record)

    def close(self) -> None:
        for handler in list(self._handlers.values()):
            handler.close()
        self._handlers.clear()
        super().close()


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


def setup_logging(logs_dir: Path, *, console_output: bool = False) -> logging.Logger:
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
    if logger.handlers:
        logger.handlers.clear()

    # 补齐 user 字段
    logger.addFilter(_UserContextFilter())

    formatter = _make_formatter()

    # 1. 公共日志: 所有日志都进这里
    main_handler = _MonthlyFileHandler(logs_dir, "review-bot-{year}-{month:02d}.log")
    main_handler.setFormatter(formatter)
    logger.addHandler(main_handler)

    # 2. 用户日志: 按用户分文件
    user_handler = _UserMonthlyFileHandler(logs_dir)
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
