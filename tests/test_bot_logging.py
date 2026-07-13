"""审核 Bot 日志系统测试."""

from __future__ import annotations

import logging
import tempfile
from datetime import datetime
from pathlib import Path

from app.review.bot_logging import (
    setup_logging,
    log_extra,
    _MonthlyFileHandler,
    _UserMonthlyFileHandler,
)


def test_log_extra_returns_user_fields():
    extra = log_extra("test-user-id", "test-user")
    assert extra["userid"] == "test-user-id"
    assert extra["english_name"] == "test-user"


def test_monthly_handler_creates_log_file(tmp_path: Path):
    handler = _MonthlyFileHandler(tmp_path, "bot-{year}-{month:02d}.log")
    logger = logging.getLogger("test_monthly")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    now = datetime.now()
    logger.info("test message")

    expected_path = tmp_path / f"bot-{now.year}-{now.month:02d}.log"
    assert expected_path.exists()
    content = expected_path.read_text(encoding="utf-8")
    assert "test message" in content


def test_user_monthly_handler_creates_user_log_file(tmp_path: Path):
    handler = _UserMonthlyFileHandler(tmp_path)
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    logger = logging.getLogger("test_user_monthly")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    logger.info("user message", extra=log_extra("test-user-id", "test-user"))

    now = datetime.now()
    expected_path = tmp_path / "users" / "test-user-id" / f"{now.year}-{now.month:02d}.log"
    assert expected_path.exists()
    content = expected_path.read_text(encoding="utf-8")
    assert "user message" in content


def test_setup_logging_creates_main_and_user_handlers():
    with tempfile.TemporaryDirectory() as tmpdir:
        logs_dir = Path(tmpdir)
        logger = setup_logging(logs_dir)

        assert len(logger.handlers) == 2

        now = datetime.now()
        logger.info("main log message", extra=log_extra("system", "system"))
        logger.info("user log message", extra=log_extra("test-user-id", "test-user"))

        main_log = logs_dir / f"review-bot-{now.year}-{now.month:02d}.log"
        user_log = logs_dir / "users" / "test-user-id" / f"{now.year}-{now.month:02d}.log"

        assert main_log.exists()
        assert user_log.exists()

        main_content = main_log.read_text(encoding="utf-8")
        user_content = user_log.read_text(encoding="utf-8")

        assert "main log message" in main_content
        assert "user log message" in main_content
        assert "user log message" in user_content
