"""审核 Bot 日志系统测试."""

from __future__ import annotations

import logging
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from app.review.bot_logging import (
    setup_logging,
    log_extra,
    _DailySizeFileHandler,
    _UserDailySizeFileHandler,
)


def test_log_extra_returns_user_fields():
    extra = log_extra("test-user-id", "test-user")
    assert extra["userid"] == "test-user-id"
    assert extra["english_name"] == "test-user"


def test_daily_handler_creates_log_file(tmp_path: Path):
    now = datetime(2026, 7, 13, 10, 0, 0)
    handler = _DailySizeFileHandler(
        tmp_path,
        "bot-{year}-{month:02d}-{day:02d}.log",
        now_provider=lambda: now,
    )
    logger = logging.getLogger("test_daily")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    logger.info("test message")

    expected_path = tmp_path / "bot-2026-07-13.log"
    assert expected_path.exists()
    content = expected_path.read_text(encoding="utf-8")
    assert "test message" in content


def test_daily_handler_switches_file_on_next_day(tmp_path: Path):
    current = [datetime(2026, 7, 13, 23, 59, 0)]
    handler = _DailySizeFileHandler(
        tmp_path,
        "bot-{year}-{month:02d}-{day:02d}.log",
        now_provider=lambda: current[0],
    )
    logger = logging.getLogger("test_daily_switch")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    logger.info("day one")
    current[0] += timedelta(minutes=2)
    logger.info("day two")

    assert "day one" in (tmp_path / "bot-2026-07-13.log").read_text(encoding="utf-8")
    assert "day two" in (tmp_path / "bot-2026-07-14.log").read_text(encoding="utf-8")


def test_daily_handler_splits_file_when_size_limit_is_reached(tmp_path: Path):
    now = datetime(2026, 7, 13, 10, 0, 0)
    handler = _DailySizeFileHandler(
        tmp_path,
        "bot-{year}-{month:02d}-{day:02d}.log",
        max_bytes=32,
        now_provider=lambda: now,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("test_daily_size")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    logger.info("a" * 24)
    logger.info("b" * 24)

    assert (tmp_path / "bot-2026-07-13.log").exists()
    part = tmp_path / "bot-2026-07-13.part-002.log"
    assert part.exists()
    assert "b" * 24 in part.read_text(encoding="utf-8")


def test_user_daily_handler_creates_user_log_file(tmp_path: Path):
    now = datetime(2026, 7, 13, 10, 0, 0)
    handler = _UserDailySizeFileHandler(tmp_path, now_provider=lambda: now)
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    logger = logging.getLogger("test_user_daily")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    logger.info("user message", extra=log_extra("test-user-id", "test-user"))

    expected_path = tmp_path / "users" / "test-user-id" / "2026-07-13.log"
    assert expected_path.exists()
    content = expected_path.read_text(encoding="utf-8")
    assert "user message" in content


def test_user_daily_handler_does_not_duplicate_system_logs(tmp_path: Path):
    handler = _UserDailySizeFileHandler(tmp_path)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("test_user_skip_system")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    logger.info("heartbeat", extra=log_extra("system", "system"))

    assert not (tmp_path / "users" / "system").exists()


def test_user_daily_handler_limits_open_file_handlers(tmp_path: Path):
    handler = _UserDailySizeFileHandler(tmp_path, max_open_handlers=2)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("test_user_handler_limit")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    for userid in ("user-1", "user-2", "user-3"):
        logger.info(userid, extra=log_extra(userid, userid))

    assert handler.open_handler_count == 2


def test_setup_logging_creates_main_and_user_handlers():
    with tempfile.TemporaryDirectory() as tmpdir:
        logs_dir = Path(tmpdir)
        logger = setup_logging(logs_dir)

        assert len(logger.handlers) == 2

        now = datetime.now()
        logger.info("main log message", extra=log_extra("system", "system"))
        logger.info("user log message", extra=log_extra("test-user-id", "test-user"))

        main_log = logs_dir / f"review-bot-{now.year}-{now.month:02d}-{now.day:02d}.log"
        user_log = logs_dir / "users" / "test-user-id" / f"{now.year}-{now.month:02d}-{now.day:02d}.log"

        assert main_log.exists()
        assert user_log.exists()

        main_content = main_log.read_text(encoding="utf-8")
        user_content = user_log.read_text(encoding="utf-8")

        assert "main log message" in main_content
        assert "user log message" in main_content
        assert "user log message" in user_content


def test_setup_logging_passes_size_limit_to_file_handlers(tmp_path: Path):
    logger = setup_logging(tmp_path, max_bytes=1234)

    main_handler = next(item for item in logger.handlers if isinstance(item, _DailySizeFileHandler))
    user_handler = next(item for item in logger.handlers if isinstance(item, _UserDailySizeFileHandler))

    assert main_handler.max_bytes == 1234
    assert user_handler.max_bytes == 1234
