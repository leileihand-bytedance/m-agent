from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.platform.models import PlatformResult, UploadedFile  # noqa: E402
from app.platform.ops.events import OpsEventLogger, read_ops_events  # noqa: E402
from app.writing.portal import (  # noqa: E402
    PortalService,
    PortalTokenStore,
    _is_loopback_address,
    _parse_multipart_form,
    _public_portal_error,
    _validate_uploaded_files,
    _validate_request_size,
    build_welcome_text,
    parse_links,
    render_compose_page,
)


def test_build_welcome_text_guides_user_to_send_links_or_merged_document():
    store = PortalTokenStore(ttl_seconds=600, time_fn=lambda: 100.0)
    token = store.issue("user-001")

    text = build_welcome_text(base_url="http://127.0.0.1:8790", token=token)

    assert "写简报" in text
    assert "写直报" in text
    assert "直接把链接发给我" in text
    assert "多个素材文档，建议先整合成一个文档" in text
    assert "重点方向" in text
    assert "篇幅要求" not in text
    assert "http://127.0.0.1:8790/compose/brief" not in text
    assert "http://127.0.0.1:8790/compose/direct_report" not in text


def test_portal_token_store_rejects_expired_token():
    now = {"value": 100.0}
    store = PortalTokenStore(ttl_seconds=30, time_fn=lambda: now["value"])
    token = store.issue("user-001")

    assert store.resolve(token) == "user-001"
    now["value"] = 131.0
    assert store.resolve(token) is None


def test_portal_preview_is_limited_to_loopback_clients():
    assert _is_loopback_address("127.0.0.1") is True
    assert _is_loopback_address("::1") is True
    assert _is_loopback_address("192.168.1.20") is False


def test_portal_rejects_oversized_request_before_reading_body():
    try:
        _validate_request_size(21, max_bytes=20)
    except ValueError as exc:
        assert "文件过大" in str(exc)
    else:
        raise AssertionError("ValueError was not raised")


def test_portal_error_message_hides_internal_exception_details():
    assert _public_portal_error(RuntimeError("secret path: /internal/tasks/123")) == "提交失败，请稍后重试。"
    assert "请至少提供" in _public_portal_error(ValueError("请至少提供一个链接、一个文件，或粘贴一段文字素材。"))


def test_render_compose_page_shows_clean_form():
    html = render_compose_page(entry_key="brief", token="abc123")

    assert "写简报" in html
    assert 'name="instruction"' in html
    assert 'name="material_text"' in html
    assert 'name="links"' in html
    assert 'name="files"' in html
    assert 'action="/submit/brief?t=abc123"' in html
    assert "如果是简报入口，后端会自动判断走单素材还是多素材版本。" not in html


def test_render_compose_page_supports_local_preview_mode():
    html = render_compose_page(entry_key="brief", token="", preview=True)

    assert 'action="/submit/brief?preview=1"' in html


def test_render_compose_page_shows_preview_submit_notice():
    html = render_compose_page(entry_key="brief", token="", submitted=True, preview=True)

    assert "本地预览模式不会回企业微信" in html


def test_parse_links_splits_lines_and_spaces():
    links = parse_links(" https://example.com/a\nhttps://example.com/b  https://example.com/c ")

    assert links == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]


def test_parse_multipart_form_supports_multiple_files():
    boundary = "----CodexBoundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="instruction"\r\n\r\n'
        "请写简报\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="links"\r\n\r\n'
        "https://example.com/a\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="files"; filename="材料A.docx"\r\n'
        "Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n\r\n"
        "docx-bytes\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="files"; filename="材料B.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
        "pdf-bytes\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")

    form, files = _parse_multipart_form(f"multipart/form-data; boundary={boundary}", body)

    assert form["instruction"] == ["请写简报"]
    assert form["links"] == ["https://example.com/a"]
    assert [item.filename for item in files] == ["材料A.docx", "材料B.pdf"]


def test_portal_service_submits_multiple_files_and_returns_reply():
    store = PortalTokenStore(ttl_seconds=600, time_fn=lambda: 100.0)
    token = store.issue("user-001")
    sent_messages = []

    class FakePlatformApp:
        def __init__(self):
            self.calls = []

        def handle_structured_request(self, **kwargs):
            self.calls.append(kwargs)
            return PlatformResult(
                skill_id="writer1",
                output={"title": "简报标题", "body": "简报正文", "sources": ["https://example.com/a"]},
                needs_clarification=False,
                message="",
            )

    app = FakePlatformApp()
    service = PortalService(
        platform_app=app,
        message_sender=lambda user, payload: sent_messages.append((user, payload)),
        token_store=store,
        dispatch=lambda fn, args: fn(*args),
    )

    service.submit(
        entry_key="brief",
        token=token,
        instruction="请正式一点。",
        material_text="补充文字素材。",
        links="https://example.com/a",
        files=[
            UploadedFile(filename="材料A.docx", content=b"docx-bytes"),
            UploadedFile(filename="材料B.pdf", content=b"pdf-bytes"),
        ],
    )

    assert app.calls[0]["skill_id"] == "brief"
    assert app.calls[0]["urls"] == ["https://example.com/a"]
    assert [item.filename for item in app.calls[0]["files"]] == ["材料A.docx", "材料B.pdf"]
    assert sent_messages[0][1]["text"]["content"] == "已收到写简报素材，正在处理，请稍后……"
    assert sent_messages[1][1]["text"]["content"] == "简报标题\n\n简报正文"


def test_portal_service_local_preview_bypasses_token_lookup():
    class FakePlatformApp:
        def __init__(self):
            self.calls = []

        def handle_structured_request(self, **kwargs):
            self.calls.append(kwargs)
            return PlatformResult(
                skill_id="writer1",
                output={"title": "标题", "body": "正文", "sources": []},
                needs_clarification=False,
                message="",
            )

    service = PortalService(
        platform_app=FakePlatformApp(),
        message_sender=lambda user, payload: None,
        token_store=PortalTokenStore(ttl_seconds=600, time_fn=lambda: 100.0),
        dispatch=lambda fn, args: fn(*args),
    )

    service.submit(
        entry_key="brief",
        token="",
        instruction="请写简报。",
        material_text="这是一段本地预览文字素材，用于验证 preview 入口。",
        links="",
        files=[],
        preview=True,
    )

    assert service._platform_app.calls[0]["sender_userid"] == "local-preview-user"


def test_portal_service_rejects_unsupported_file_before_dispatch():
    store = PortalTokenStore(ttl_seconds=600, time_fn=lambda: 100.0)
    token = store.issue("user-001")
    sent_messages = []

    class FakePlatformApp:
        def handle_structured_request(self, **kwargs):
            raise AssertionError("should not be called")

    service = PortalService(
        platform_app=FakePlatformApp(),
        message_sender=lambda user, payload: sent_messages.append((user, payload)),
        token_store=store,
        dispatch=lambda fn, args: fn(*args),
    )

    try:
        service.submit(
            entry_key="brief",
            token=token,
            instruction="",
            material_text="",
            links="",
            files=[UploadedFile(filename="材料C.xlsx", content=b"xlsx-bytes")],
        )
    except ValueError as exc:
        assert "Word(.docx)" in str(exc)
    else:
        raise AssertionError("ValueError was not raised")

    assert sent_messages == []


def test_portal_accepts_pptx_upload_type():
    _validate_uploaded_files(
        [UploadedFile(filename="汇报材料.pptx", content=b"pptx-bytes")]
    )


def test_portal_service_records_ops_event_when_background_processing_fails(tmp_path):
    store = PortalTokenStore(ttl_seconds=600, time_fn=lambda: 100.0)
    token = store.issue("user-001")
    sent_messages = []

    class FakePlatformApp:
        def handle_structured_request(self, **kwargs):
            raise RuntimeError("portal model failed")

    service = PortalService(
        platform_app=FakePlatformApp(),
        message_sender=lambda user, payload: sent_messages.append((user, payload)),
        token_store=store,
        dispatch=lambda fn, args: fn(*args),
        ops_event_logger=OpsEventLogger(tmp_path / "ops_events"),
    )

    service.submit(
        entry_key="brief",
        token=token,
        instruction="请写简报。",
        material_text="这是一段文字素材。",
        links="",
        files=[],
    )

    events = read_ops_events(tmp_path / "ops_events", __import__("datetime").date.today())
    assert len(events) == 1
    assert events[0].source == "writing_portal"
    assert events[0].subject == "素材页处理失败"
    assert events[0].sender_userid == "user-001"
    assert events[0].skill_id == "brief"
    assert "portal model failed" in events[0].detail
