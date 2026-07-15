import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import multiprocessing

import pytest

import app.platform.task_status as task_status_module
from app.platform.task_status import read_task_status, update_task_status, write_task_status


def _write_versioned_status_process(
    task_dir: str,
    *,
    processing_status: str,
    state_version: int,
    wait_event,
    done_event,
    result_queue,
) -> None:
    try:
        if wait_event is not None and not wait_event.wait(timeout=5):
            raise TimeoutError("status writer wait timeout")
        write_task_status(
            Path(task_dir),
            processing_status=processing_status,
            state_version=state_version,
        )
        result_queue.put(("ok", state_version))
    except BaseException as exc:
        result_queue.put(("error", type(exc).__name__))
    finally:
        if done_event is not None:
            done_event.set()


def test_task_status_supports_background_queue_states(tmp_path: Path):
    for status in ("queued", "running", "cancelled"):
        write_task_status(tmp_path, processing_status=status)
        assert read_task_status(tmp_path)["processing_status"] == status


def test_partial_status_updates_preserve_the_other_dimension(tmp_path: Path):
    write_task_status(
        tmp_path,
        processing_status="running",
        delivery_status="unknown",
        source="task_executor",
    )

    update_task_status(tmp_path, delivery_status="delivered", source="attachment_delivery")
    delivered = read_task_status(tmp_path)
    update_task_status(tmp_path, processing_status="completed", source="task_executor")
    completed = read_task_status(tmp_path)

    assert delivered["processing_status"] == "running"
    assert delivered["delivery_status"] == "delivered"
    assert completed["processing_status"] == "completed"
    assert completed["delivery_status"] == "delivered"
    assert completed["source"] == "task_executor"


def test_read_task_status_returns_empty_mapping_for_invalid_json(tmp_path: Path):
    (tmp_path / "status.json").write_text("not-json", encoding="utf-8")

    assert read_task_status(tmp_path) == {}


def test_status_update_remains_atomic_and_does_not_leave_temporary_file(tmp_path: Path):
    update_task_status(
        tmp_path,
        processing_status="queued",
        delivery_status="unknown",
    )

    payload = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert payload["processing_status"] == "queued"
    assert not list(tmp_path.glob(".status.json.*.tmp"))


def test_parallel_partial_updates_do_not_overwrite_processing_or_delivery(tmp_path: Path):
    write_task_status(
        tmp_path,
        processing_status="queued",
        delivery_status="unknown",
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = []
        for _ in range(20):
            futures.append(
                executor.submit(
                    update_task_status,
                    tmp_path,
                    processing_status="running",
                    source="task_executor",
                )
            )
            futures.append(
                executor.submit(
                    update_task_status,
                    tmp_path,
                    delivery_status="delivered",
                    source="attachment_delivery",
                )
            )
        for future in futures:
            future.result()

    payload = read_task_status(tmp_path)
    assert payload["processing_status"] == "running"
    assert payload["delivery_status"] == "delivered"


def test_old_status_json_without_state_version_remains_compatible(tmp_path: Path):
    (tmp_path / "status.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "processing_status": "running",
                "delivery_status": "unknown",
                "source": "legacy",
            }
        ),
        encoding="utf-8",
    )

    update_task_status(tmp_path, delivery_status="delivered")
    payload = read_task_status(tmp_path)

    assert payload["processing_status"] == "running"
    assert payload["delivery_status"] == "delivered"
    assert payload["state_version"] == 0


def test_status_writer_rejects_stale_state_version_and_preserves_newer_file(
    tmp_path: Path,
):
    write_task_status(
        tmp_path,
        processing_status="running",
        state_version=3,
    )

    with pytest.raises(task_status_module.StaleTaskStatusVersionError, match="旧版本"):
        write_task_status(
            tmp_path,
            processing_status="queued",
            state_version=2,
        )

    payload = read_task_status(tmp_path)
    assert payload["processing_status"] == "running"
    assert payload["state_version"] == 3


def test_partial_delivery_update_preserves_current_state_version(tmp_path: Path):
    write_task_status(
        tmp_path,
        processing_status="completed",
        delivery_status="unknown",
        state_version=7,
    )

    update_task_status(tmp_path, delivery_status="delivered")

    payload = read_task_status(tmp_path)
    assert payload["state_version"] == 7
    assert payload["delivery_status"] == "delivered"


def test_multiprocess_late_old_status_cannot_overwrite_newer_version(tmp_path: Path):
    write_task_status(tmp_path, processing_status="queued", state_version=1)
    context = multiprocessing.get_context("spawn")
    newer_done = context.Event()
    result_queue = context.Queue()
    older = context.Process(
        target=_write_versioned_status_process,
        kwargs={
            "task_dir": str(tmp_path),
            "processing_status": "running",
            "state_version": 2,
            "wait_event": newer_done,
            "done_event": None,
            "result_queue": result_queue,
        },
    )
    newer = context.Process(
        target=_write_versioned_status_process,
        kwargs={
            "task_dir": str(tmp_path),
            "processing_status": "completed",
            "state_version": 3,
            "wait_event": None,
            "done_event": newer_done,
            "result_queue": result_queue,
        },
    )

    older.start()
    newer.start()
    older.join(timeout=10)
    newer.join(timeout=10)
    assert older.exitcode == 0
    assert newer.exitcode == 0
    results = {result_queue.get(timeout=2) for _ in range(2)}

    assert ("ok", 3) in results
    assert ("error", "StaleTaskStatusVersionError") in results
    payload = read_task_status(tmp_path)
    assert payload["processing_status"] == "completed"
    assert payload["state_version"] == 3
