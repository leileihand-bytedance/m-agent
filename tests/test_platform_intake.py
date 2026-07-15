from __future__ import annotations

import json
from pathlib import Path
import time

import pytest

from app.platform.intake import (
    IntakePersistence,
    check_intake_file_limits,
)
from app.platform.models import UploadedFile


def test_intake_persistence_saves_state_and_restores_isolated_file(tmp_path: Path):
    store = IntakePersistence(
        storage_dir=tmp_path,
        state_filename="session.json",
        ttl_seconds=1800,
    )
    key = ("wecom", "user-001")

    stored = store.persist_file(
        key,
        UploadedFile(filename="../调研 材料?.docx", content=b"docx-content"),
    )
    store.save_state(
        key,
        {
            "updated_at": time.time(),
            "files": [store.file_payload(stored)],
        },
    )

    payload = store.load_state(key)
    assert payload is not None
    restored = store.restore_file(key, payload["files"][0])
    assert restored is not None
    assert restored.filename == "../调研 材料?.docx"
    assert restored.read_bytes() == b"docx-content"
    assert restored.delete_after_read is True
    stored_path = Path(restored.stored_path)
    assert stored_path.parent.name == "files"
    assert stored_path.name.endswith("-调研_材料_.docx")
    assert "user-001" not in str(stored_path)


def test_intake_persistence_rejects_forged_path_and_mismatched_identity(tmp_path: Path):
    store = IntakePersistence(
        storage_dir=tmp_path / "intake",
        state_filename="state.json",
        ttl_seconds=1800,
    )
    key = ("wecom", "user-001")
    external = tmp_path / "secret.docx"
    external.write_bytes(b"secret")

    assert store.restore_file(
        key,
        {
            "filename": "secret.docx",
            "stored_path": str(external),
            "content_type": "application/octet-stream",
        },
    ) is None

    store.save_state(key, {"updated_at": time.time()})
    state_path = next((tmp_path / "intake").glob("*/state.json"))
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["sender_userid"] = "other-user"
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    assert store.load_state(key) is None
    assert not state_path.parent.exists()


def test_intake_persistence_expires_and_cleans_session_files(tmp_path: Path):
    key = ("wecom", "user-001")
    store = IntakePersistence(
        storage_dir=tmp_path,
        state_filename="session.json",
        ttl_seconds=10,
    )
    stored = store.persist_file(key, UploadedFile(filename="材料.pdf", content=b"pdf"))
    store.save_state(key, {"updated_at": time.time() - 11})

    assert store.load_state(key) is None
    assert not Path(stored.stored_path).exists()
    assert not any(tmp_path.iterdir())


def test_intake_persistence_can_preserve_files_when_state_is_consumed(tmp_path: Path):
    key = ("wecom", "user-001")
    store = IntakePersistence(
        storage_dir=tmp_path,
        state_filename="state.json",
        ttl_seconds=1800,
    )
    stored = store.persist_file(key, UploadedFile(filename="正文.docx", content=b"content"))
    store.save_state(key, {"updated_at": time.time()})

    store.clear(key, preserve_files=True)

    assert Path(stored.stored_path).exists()
    assert not any(tmp_path.glob("*/state.json"))
    store.delete_file(stored)
    assert not Path(stored.stored_path).exists()


def test_intake_persistence_without_storage_keeps_in_memory_upload():
    store = IntakePersistence(
        storage_dir=None,
        state_filename="session.json",
        ttl_seconds=1800,
    )
    uploaded = UploadedFile(filename="材料.docx", content=b"content")

    assert store.persist_file(("local", "user"), uploaded) is uploaded
    store.save_state(("local", "user"), {"updated_at": time.time()})
    assert store.load_state(("local", "user")) is None


def test_intake_file_limits_share_count_and_total_size_rules():
    files = (
        UploadedFile(filename="a.docx", content=b"1234"),
        UploadedFile(filename="b.docx", content=b"12345"),
    )

    too_many = check_intake_file_limits(
        files,
        incoming_size=1,
        max_files=2,
        max_total_file_bytes=20,
    )
    too_large = check_intake_file_limits(
        files[:1],
        incoming_size=17,
        max_files=3,
        max_total_file_bytes=20,
    )
    allowed = check_intake_file_limits(
        files[:1],
        incoming_size=16,
        max_files=3,
        max_total_file_bytes=20,
    )

    assert too_many is not None and too_many.code == "too_many_files"
    assert too_large is not None and too_large.code == "total_size_exceeded"
    assert allowed is None


def test_intake_persistence_rejects_unsafe_state_filename(tmp_path: Path):
    with pytest.raises(ValueError, match="状态文件名"):
        IntakePersistence(
            storage_dir=tmp_path,
            state_filename="../state.json",
            ttl_seconds=1800,
        )
