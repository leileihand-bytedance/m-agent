from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import struct
import threading

import pytest
from pypdf import PdfWriter

import app.platform.documents.enrichment as enrichment_module
from app.platform.documents import (
    DocumentAsset,
    DocumentFormat,
    DocumentService,
    DocumentWarning,
)
from app.platform.documents.enrichment import (
    DocumentEnricher,
    OCRPageResult,
    RenderedPage,
    SubprocessPdfRenderer,
    VISION_SWIFT_SCRIPT,
    VisionOCRBackend,
    discover_enrichment_capabilities,
)


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _make_blank_pdf(path: Path, pages: int = 2) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    with path.open("wb") as stream:
        writer.write(stream)


def _make_pptx(path: Path) -> None:
    pptx = pytest.importorskip("pptx")
    presentation = pptx.Presentation()
    presentation.slides.add_slide(presentation.slide_layouts[1])
    presentation.save(path)


def _pdf_parser_without_text(path: Path, *, asset_dir: Path) -> dict[str, object]:
    return {
        "blocks": (),
        "assets": (),
        "warnings": (),
        "page_count": 2,
        "metadata": {"parser": "fake-pdf"},
    }


def _pdf_parser_with_ocr_warning(path: Path, *, asset_dir: Path) -> dict[str, object]:
    return {
        "blocks": (),
        "assets": (),
        "warnings": (
            DocumentWarning(
                code="ocr_required",
                message="需要 OCR",
                locations=("page:1", "page:3"),
            ),
        ),
        "page_count": 3,
        "metadata": {"parser": "fake-pdf"},
    }


def _pptx_parser(path: Path, *, asset_dir: Path) -> dict[str, object]:
    return {
        "blocks": (),
        "assets": (),
        "warnings": (),
        "page_count": 1,
        "metadata": {"parser": "fake-pptx"},
    }


class FakePdfRenderer:
    backend_name = "fake-pdf-renderer"

    def __init__(self, *, outside_output: Path | None = None):
        self.calls: list[tuple[Path, tuple[int, ...], Path]] = []
        self.outside_output = outside_output

    def render_pdf(
        self,
        pdf_path: Path,
        *,
        output_dir: Path,
        page_numbers: tuple[int, ...],
        location_prefix: str,
    ) -> tuple[RenderedPage, ...]:
        self.calls.append((pdf_path, page_numbers, output_dir))
        rendered: list[RenderedPage] = []
        for page_number in page_numbers:
            if self.outside_output is not None:
                target = self.outside_output
            else:
                target = output_dir / f"{location_prefix}-{page_number}.png"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(PNG_BYTES)
            rendered.append(
                RenderedPage(
                    page_number=page_number,
                    location=f"{location_prefix}:{page_number}",
                    path=target,
                    content_type="image/png",
                    width=1,
                    height=1,
                )
            )
        return tuple(rendered)


class FakeOfficeConverter:
    backend_name = "fake-office-converter"

    def __init__(self, *, pages: int = 1):
        self.calls: list[tuple[Path, Path]] = []
        self.pages = pages

    def convert_to_pdf(self, source_path: Path, *, output_dir: Path) -> Path:
        self.calls.append((source_path, output_dir))
        target = output_dir / "converted.pdf"
        _make_blank_pdf(target, pages=self.pages)
        return target


class FakeOCRBackend:
    backend_name = "fake-ocr"

    def __init__(self, *, failures: set[str] | None = None):
        self.calls: list[tuple[Path, str]] = []
        self.failures = failures or set()

    def recognize_page(self, image_path: Path, *, location: str) -> OCRPageResult:
        self.calls.append((image_path, location))
        if location in self.failures:
            raise RuntimeError("boom")
        return OCRPageResult(location=location, text=f"{location}-识别文本")


class SnapshotCheckingOCRBackend(FakeOCRBackend):
    def __init__(self, *, original_path_holder: dict[str, Path]):
        super().__init__()
        self.original_path_holder = original_path_holder

    def recognize_page(self, image_path: Path, *, location: str) -> OCRPageResult:
        original_path = self.original_path_holder["path"]
        assert image_path != original_path
        assert image_path.parent == original_path.parent
        assert image_path.read_bytes() == PNG_BYTES
        return super().recognize_page(image_path, location=location)


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class SlowPdfRenderer(FakePdfRenderer):
    def __init__(self, clock: FakeClock, *, seconds_per_call: float):
        super().__init__()
        self.clock = clock
        self.seconds_per_call = seconds_per_call

    def render_pdf(self, *args, **kwargs) -> tuple[RenderedPage, ...]:
        rendered = super().render_pdf(*args, **kwargs)
        self.clock.advance(self.seconds_per_call)
        return rendered


class SlowOCRBackend(FakeOCRBackend):
    def __init__(self, clock: FakeClock, *, seconds_per_call: float):
        super().__init__()
        self.clock = clock
        self.seconds_per_call = seconds_per_call

    def recognize_page(self, image_path: Path, *, location: str) -> OCRPageResult:
        result = super().recognize_page(image_path, location=location)
        self.clock.advance(self.seconds_per_call)
        return result


def _pdf_parser_with_existing_rendered_assets(
    locations: tuple[str, ...],
    *,
    warning_groups: tuple[tuple[str, ...], ...] | None = None,
):
    def parser(path: Path, *, asset_dir: Path) -> dict[str, object]:
        rendered_dir = asset_dir.parent / "rendered"
        rendered_dir.mkdir(parents=True, exist_ok=True)
        assets = []
        for location in locations:
            page_number = int(location.split(":", maxsplit=1)[1])
            image_path = rendered_dir / f"page-{page_number}.png"
            image_path.write_bytes(PNG_BYTES)
            assets.append(
                DocumentAsset(
                    asset_id=f"existing-{page_number}",
                    kind="rendered_page",
                    location=location,
                    path=str(image_path),
                    content_type="image/png",
                    width=1,
                    height=1,
                )
            )
        groups = warning_groups or (locations,)
        warnings = tuple(
            DocumentWarning(code="ocr_required", message="需要 OCR", locations=group)
            for group in groups
        )
        return {
            "blocks": (),
            "assets": tuple(assets),
            "warnings": warnings,
            "page_count": max(int(location.split(":", maxsplit=1)[1]) for location in locations),
            "metadata": {"parser": "fake-pdf"},
        }

    return parser


def test_document_service_renders_pdf_pages_with_fake_backend(tmp_path):
    input_dir = tmp_path / "input"
    work_dir = tmp_path / "work"
    input_dir.mkdir()
    pdf_path = input_dir / "材料.pdf"
    _make_blank_pdf(pdf_path, pages=2)

    renderer = FakePdfRenderer()
    service = DocumentService(
        parsers={DocumentFormat.PDF: _pdf_parser_without_text},
        enricher=DocumentEnricher(pdf_renderer=renderer),
    )

    artifact = service.parse(
        pdf_path,
        allowed_root=input_dir,
        work_dir=work_dir,
        render_pages=True,
    )

    assert [asset.location for asset in artifact.assets] == ["page:1", "page:2"]
    assert artifact.metadata["render_used"] is True
    assert artifact.metadata["render_backend"] == "fake-pdf-renderer"
    assert [call[1] for call in renderer.calls] == [(1,), (2,)]

    stored = json.loads((work_dir / "documents" / artifact.artifact_id / "document.json").read_text(encoding="utf-8"))
    assert len(stored["assets"]) == 2
    assert stored["metadata"]["render_used"] is True


def test_document_service_caps_total_rendered_asset_bytes(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf_path = input_dir / "材料.pdf"
    _make_blank_pdf(pdf_path, pages=2)
    service = DocumentService(
        parsers={DocumentFormat.PDF: _pdf_parser_without_text},
        enricher=DocumentEnricher(
            pdf_renderer=FakePdfRenderer(),
            max_total_render_bytes=len(PNG_BYTES) + 1,
        ),
    )

    artifact = service.parse(
        pdf_path,
        allowed_root=input_dir,
        work_dir=tmp_path / "work",
        render_pages=True,
    )

    assert [asset.location for asset in artifact.assets] == ["page:1"]
    warning = next(
        warning
        for warning in artifact.warnings
        if warning.code == "render_total_bytes_exceeded"
    )
    assert warning.locations == ("page:2",)


def test_document_service_renders_pptx_via_pdf_conversion_chain(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pptx_path = input_dir / "汇报材料.pptx"
    _make_pptx(pptx_path)

    renderer = FakePdfRenderer()
    converter = FakeOfficeConverter()
    service = DocumentService(
        parsers={DocumentFormat.PPTX: _pptx_parser},
        enricher=DocumentEnricher(
            pdf_renderer=renderer,
            office_converter=converter,
        ),
    )

    artifact = service.parse(
        pptx_path,
        allowed_root=input_dir,
        work_dir=tmp_path / "work",
        render_pages=True,
    )

    assert converter.calls
    assert renderer.calls
    assert [asset.location for asset in artifact.assets] == ["slide:1"]
    assert artifact.metadata["render_used"] is True


def test_document_service_only_ocrs_requested_scanned_pages(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf_path = input_dir / "扫描材料.pdf"
    _make_blank_pdf(pdf_path, pages=3)

    renderer = FakePdfRenderer()
    ocr = FakeOCRBackend()
    service = DocumentService(
        parsers={DocumentFormat.PDF: _pdf_parser_with_ocr_warning},
        enricher=DocumentEnricher(pdf_renderer=renderer, ocr_backend=ocr),
    )

    artifact = service.parse(
        pdf_path,
        allowed_root=input_dir,
        work_dir=tmp_path / "work",
        ocr_scanned_pages=True,
    )

    assert [call[1] for call in renderer.calls] == [(1,), (3,)]
    assert [block.location for block in artifact.blocks if block.kind == "ocr_page"] == ["page:1", "page:3"]
    assert all(warning.code != "ocr_required" for warning in artifact.warnings)
    assert artifact.metadata["ocr_used"] is True
    assert artifact.metadata["ocr_backend"] == "fake-ocr"


def test_document_service_keeps_remaining_ocr_warning_on_partial_failure(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf_path = input_dir / "扫描材料.pdf"
    _make_blank_pdf(pdf_path, pages=3)

    renderer = FakePdfRenderer()
    ocr = FakeOCRBackend(failures={"page:3"})
    service = DocumentService(
        parsers={DocumentFormat.PDF: _pdf_parser_with_ocr_warning},
        enricher=DocumentEnricher(pdf_renderer=renderer, ocr_backend=ocr),
    )

    artifact = service.parse(
        pdf_path,
        allowed_root=input_dir,
        work_dir=tmp_path / "work",
        ocr_scanned_pages=True,
    )

    remaining = next(warning for warning in artifact.warnings if warning.code == "ocr_required")
    assert remaining.locations == ("page:3",)
    assert any(block.kind == "ocr_page" and block.location == "page:1" for block in artifact.blocks)
    assert any(warning.code == "ocr_page_failed" for warning in artifact.warnings)


def test_document_service_converts_invalid_render_paths_to_warning(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf_path = input_dir / "材料.pdf"
    _make_blank_pdf(pdf_path, pages=2)

    outside_output = tmp_path / "leak.png"
    renderer = FakePdfRenderer(outside_output=outside_output)
    service = DocumentService(
        parsers={DocumentFormat.PDF: _pdf_parser_without_text},
        enricher=DocumentEnricher(pdf_renderer=renderer),
    )

    artifact = service.parse(
        pdf_path,
        allowed_root=input_dir,
        work_dir=tmp_path / "work",
        render_pages=True,
    )

    assert artifact.assets == ()
    assert any(warning.code == "render_output_invalid" for warning in artifact.warnings)


def test_document_service_defaults_remain_compatible_when_enrichment_disabled(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf_path = input_dir / "材料.pdf"
    _make_blank_pdf(pdf_path, pages=2)

    class ExplodingEnricher:
        def enrich(self, *args, **kwargs):  # pragma: no cover - should never run
            raise AssertionError("unexpected call")

    service = DocumentService(
        parsers={DocumentFormat.PDF: _pdf_parser_without_text},
        enricher=ExplodingEnricher(),
    )

    artifact = service.parse(
        pdf_path,
        allowed_root=input_dir,
        work_dir=tmp_path / "work",
    )

    assert artifact.metadata == {"parser": "fake-pdf"}


def test_capability_discovery_prefers_available_commands_and_macos_vision_fallback():
    commands = {
        "pdftocairo": "/opt/poppler/pdftocairo",
        "soffice": "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    }
    valid_paths = {Path(command).resolve() for command in commands.values()}
    valid_paths.add(Path("/usr/bin/swift"))

    capabilities = discover_enrichment_capabilities(
        which=commands.get,
        path_exists=lambda path: path in valid_paths,
        path_is_file=lambda path: path in valid_paths,
        path_is_executable=lambda path: path in valid_paths,
        system_name="Darwin",
    )

    assert capabilities.pdf_renderer is not None
    assert capabilities.pdf_renderer.backend == "pdftocairo"
    assert capabilities.office_converter is not None
    assert capabilities.office_converter.backend == "soffice"
    assert capabilities.ocr is not None
    assert capabilities.ocr.backend == "vision"


def test_capability_discovery_treats_configured_swift_as_vision_backend():
    swift = Path("/usr/bin/swift")
    capabilities = discover_enrichment_capabilities(
        ocr_command=swift,
        which=lambda name: None,
        path_exists=lambda path: path == swift,
        path_is_file=lambda path: path == swift,
        path_is_executable=lambda path: path == swift,
        system_name="Darwin",
    )

    assert capabilities.ocr is not None
    assert capabilities.ocr.backend == "vision"


def test_vision_ocr_uses_unique_controlled_script_and_cleans_it(tmp_path, monkeypatch):
    image_path = tmp_path / "page.png"
    image_path.write_bytes(PNG_BYTES)
    malicious_target = tmp_path / "malicious.swift"
    malicious_target.write_text('print("MALICIOUS")', encoding="utf-8")
    legacy_script = tmp_path / "_vision_ocr.swift"
    legacy_script.symlink_to(malicious_target)
    executed_scripts: list[Path] = []

    def fake_run(args, **kwargs):
        script_path = Path(args[1])
        executed_scripts.append(script_path)
        assert script_path != legacy_script
        assert not script_path.is_symlink()
        assert script_path.read_text(encoding="utf-8") == VISION_SWIFT_SCRIPT
        return "受控识别结果"

    monkeypatch.setattr(enrichment_module, "_run_command", fake_run)
    backend = VisionOCRBackend(command="/usr/bin/swift", backend_name="vision", timeout_sec=5)

    result = backend.recognize_page(image_path, location="page:1")

    assert result.text == "受控识别结果"
    assert len(executed_scripts) == 1
    assert not executed_scripts[0].exists()
    assert malicious_target.read_text(encoding="utf-8") == 'print("MALICIOUS")'
    assert legacy_script.is_symlink()


def test_capability_discovery_rejects_relative_or_non_executable_commands():
    relative = discover_enrichment_capabilities(
        pdf_renderer_command="tools/pdftoppm",
        office_command="tools/soffice",
        ocr_command="tools/tesseract",
        which=lambda name: f"bin/{name}",
        path_exists=lambda path: not path.is_absolute(),
        path_is_file=lambda path: not path.is_absolute(),
        path_is_executable=lambda path: not path.is_absolute(),
        system_name="Linux",
    )
    assert relative.pdf_renderer is None
    assert relative.office_converter is None
    assert relative.ocr is None

    non_executable = discover_enrichment_capabilities(
        pdf_renderer_command="/opt/tools/pdftoppm",
        office_command="/opt/tools/soffice",
        ocr_command="/opt/tools/tesseract",
        which=lambda name: None,
        path_exists=lambda path: True,
        path_is_file=lambda path: True,
        path_is_executable=lambda path: False,
        system_name="Linux",
    )
    assert non_executable.pdf_renderer is None
    assert non_executable.office_converter is None
    assert non_executable.ocr is None


def test_subprocess_commands_receive_sanitized_environment(tmp_path, monkeypatch):
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = ""

    def fake_run(args, **kwargs):
        captured.update(kwargs)
        return Completed()

    monkeypatch.setenv("MODEL_API_KEY", "must-not-reach-document-tools")
    monkeypatch.setenv("WRITING_BOT_SECRET", "must-not-reach-document-tools")
    monkeypatch.setattr(enrichment_module.subprocess, "run", fake_run)

    enrichment_module._run_command(
        ["/usr/bin/true"],
        cwd=tmp_path,
        timeout_sec=1,
    )

    child_env = captured["env"]
    assert isinstance(child_env, dict)
    assert "MODEL_API_KEY" not in child_env
    assert "WRITING_BOT_SECRET" not in child_env
    assert child_env["HOME"] == str(tmp_path)
    assert child_env["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"


def test_subprocess_renderer_does_not_reuse_stale_png(tmp_path, monkeypatch):
    output_dir = tmp_path / "rendered"
    output_dir.mkdir()
    stale = output_dir / "page-0001-1.png"
    stale.write_bytes(PNG_BYTES)
    pdf_path = tmp_path / "source.pdf"
    _make_blank_pdf(pdf_path, pages=1)
    monkeypatch.setattr(enrichment_module, "_run_command", lambda *args, **kwargs: "")
    renderer = SubprocessPdfRenderer(
        command="/usr/bin/true",
        backend_name="pdftoppm",
        timeout_sec=5,
    )

    with pytest.raises(RuntimeError, match="rendered output missing"):
        renderer.render_pdf(
            pdf_path,
            output_dir=output_dir,
            page_numbers=(1,),
            location_prefix="page",
        )

    assert stale.read_bytes() == PNG_BYTES


def test_subprocess_renderer_replaces_stable_page_output_without_accumulating(tmp_path, monkeypatch):
    output_dir = tmp_path / "rendered"
    output_dir.mkdir()
    pdf_path = tmp_path / "source.pdf"
    _make_blank_pdf(pdf_path, pages=1)

    def fake_run(args, **kwargs):
        prefix = Path(args[-1])
        (prefix.parent / f"{prefix.name}-1.png").write_bytes(PNG_BYTES)
        return ""

    monkeypatch.setattr(enrichment_module, "_run_command", fake_run)
    renderer = SubprocessPdfRenderer(
        command="/opt/homebrew/bin/pdftoppm",
        backend_name="pdftoppm",
        timeout_sec=5,
    )

    first = renderer.render_pdf(
        pdf_path,
        output_dir=output_dir,
        page_numbers=(1,),
        location_prefix="page",
    )
    second = renderer.render_pdf(
        pdf_path,
        output_dir=output_dir,
        page_numbers=(1,),
        location_prefix="page",
    )

    assert first[0].path == second[0].path == output_dir / "page-0001.png"
    assert sorted(path.name for path in output_dir.glob("*.png")) == ["page-0001.png"]


def test_office_converter_rejects_stale_pdf_when_current_conversion_produces_nothing(tmp_path, monkeypatch):
    from app.platform.documents.enrichment import SubprocessOfficeConverter

    source = tmp_path / "slides.pptx"
    source.write_bytes(b"pptx-placeholder")
    output_dir = tmp_path / "converted"
    output_dir.mkdir()
    stale = output_dir / "slides.pdf"
    _make_blank_pdf(stale, pages=1)
    monkeypatch.setattr(enrichment_module, "_run_command", lambda *args, **kwargs: "")
    converter = SubprocessOfficeConverter(
        command="/Applications/LibreOffice.app/Contents/MacOS/soffice",
        backend_name="libreoffice",
        timeout_sec=5,
    )

    with pytest.raises(RuntimeError, match="converted pdf missing"):
        converter.convert_to_pdf(source, output_dir=output_dir)

    assert not stale.exists()


def test_subprocess_renderer_caps_output_dimensions(tmp_path, monkeypatch):
    output_dir = tmp_path / "rendered"
    pdf_path = tmp_path / "source.pdf"
    _make_blank_pdf(pdf_path, pages=1)
    captured: list[str] = []

    def fake_run(args, **kwargs):
        captured.extend(args)
        prefix = Path(args[-1])
        prefix.with_name(f"{prefix.name}-1.png").write_bytes(PNG_BYTES)
        return ""

    monkeypatch.setattr(enrichment_module, "_run_command", fake_run)
    renderer = SubprocessPdfRenderer(
        command="/usr/bin/true",
        backend_name="pdftoppm",
        timeout_sec=5,
    )

    rendered = renderer.render_pdf(
        pdf_path,
        output_dir=output_dir,
        page_numbers=(1,),
        location_prefix="page",
    )

    assert rendered[0].width == 1
    assert captured[captured.index("-scale-to") + 1] == "2400"


def test_ppt_render_skips_converted_pdf_with_mismatched_page_count(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pptx_path = input_dir / "页数错配.pptx"
    _make_pptx(pptx_path)
    renderer = FakePdfRenderer()
    converter = FakeOfficeConverter(pages=2)
    service = DocumentService(
        parsers={DocumentFormat.PPTX: _pptx_parser},
        enricher=DocumentEnricher(pdf_renderer=renderer, office_converter=converter),
    )

    artifact = service.parse(
        pptx_path,
        allowed_root=input_dir,
        work_dir=tmp_path / "work",
        render_pages=True,
    )

    assert renderer.calls == []
    mismatch = next(warning for warning in artifact.warnings if warning.code == "ppt_render_page_mismatch")
    assert mismatch.locations == ("slide:1",)


def test_ocr_rejects_existing_rendered_asset_symlink_outside_work(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf_path = input_dir / "扫描材料.pdf"
    _make_blank_pdf(pdf_path, pages=1)
    outside_image = tmp_path / "outside.png"
    outside_image.write_bytes(PNG_BYTES)

    def parser(path: Path, *, asset_dir: Path) -> dict[str, object]:
        rendered_dir = asset_dir.parent / "rendered"
        rendered_dir.mkdir(parents=True, exist_ok=True)
        link_path = rendered_dir / "page-1.png"
        link_path.symlink_to(outside_image)
        return {
            "blocks": (),
            "assets": (
                DocumentAsset(
                    asset_id="existing-1",
                    kind="rendered_page",
                    location="page:1",
                    path=str(link_path),
                    content_type="image/png",
                    width=1,
                    height=1,
                ),
            ),
            "warnings": (DocumentWarning(code="ocr_required", message="需要 OCR", locations=("page:1",)),),
            "page_count": 1,
            "metadata": {"parser": "fake-pdf"},
        }

    renderer = FakePdfRenderer()
    ocr = FakeOCRBackend()
    service = DocumentService(
        parsers={DocumentFormat.PDF: parser},
        enricher=DocumentEnricher(pdf_renderer=renderer, ocr_backend=ocr),
    )

    artifact = service.parse(
        pdf_path,
        allowed_root=input_dir,
        work_dir=tmp_path / "work",
        ocr_scanned_pages=True,
    )

    assert ocr.calls == []
    assert renderer.calls == []
    assert any(warning.code == "ocr_asset_invalid" for warning in artifact.warnings)
    assert any(warning.code == "ocr_required" for warning in artifact.warnings)


@pytest.mark.parametrize("invalid_kind", ["unsupported", "file_too_large", "pixels_too_large"])
def test_ocr_revalidates_existing_rendered_asset_format_and_size(tmp_path, invalid_kind):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf_path = input_dir / f"{invalid_kind}.pdf"
    _make_blank_pdf(pdf_path, pages=1)

    def parser(path: Path, *, asset_dir: Path) -> dict[str, object]:
        rendered_dir = asset_dir.parent / "rendered"
        rendered_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".bmp" if invalid_kind == "unsupported" else ".png"
        image_path = rendered_dir / f"page-1{suffix}"
        if invalid_kind == "pixels_too_large":
            image_bytes = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 100, 100)
        else:
            image_bytes = PNG_BYTES
        image_path.write_bytes(image_bytes)
        return {
            "blocks": (),
            "assets": (
                DocumentAsset(
                    asset_id="existing-1",
                    kind="rendered_page",
                    location="page:1",
                    path=str(image_path),
                    content_type="image/bmp" if invalid_kind == "unsupported" else "image/png",
                    width=None,
                    height=None,
                ),
            ),
            "warnings": (DocumentWarning(code="ocr_required", message="需要 OCR", locations=("page:1",)),),
            "page_count": 1,
            "metadata": {"parser": "fake-pdf"},
        }

    ocr = FakeOCRBackend()
    options = {"max_image_bytes": 10} if invalid_kind == "file_too_large" else {}
    if invalid_kind == "pixels_too_large":
        options["max_image_pixels"] = 100
    service = DocumentService(
        parsers={DocumentFormat.PDF: parser},
        enricher=DocumentEnricher(ocr_backend=ocr, **options),
    )

    artifact = service.parse(
        pdf_path,
        allowed_root=input_dir,
        work_dir=tmp_path / "work",
        ocr_scanned_pages=True,
    )

    assert ocr.calls == []
    assert any(warning.code == "ocr_asset_invalid" for warning in artifact.warnings)


def test_ocr_merges_locations_from_all_required_warnings(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf_path = input_dir / "多组扫描页.pdf"
    _make_blank_pdf(pdf_path, pages=3)
    renderer = FakePdfRenderer()
    ocr = FakeOCRBackend()

    def parser(path: Path, *, asset_dir: Path) -> dict[str, object]:
        return {
            "blocks": (),
            "assets": (),
            "warnings": (
                DocumentWarning(code="ocr_required", message="第一组", locations=("page:1", "page:3")),
                DocumentWarning(code="ocr_required", message="第二组", locations=("page:2",)),
            ),
            "page_count": 3,
            "metadata": {"parser": "fake-pdf"},
        }

    service = DocumentService(
        parsers={DocumentFormat.PDF: parser},
        enricher=DocumentEnricher(pdf_renderer=renderer, ocr_backend=ocr),
    )

    artifact = service.parse(
        pdf_path,
        allowed_root=input_dir,
        work_dir=tmp_path / "work",
        ocr_scanned_pages=True,
    )

    assert [call[1] for call in renderer.calls] == [(1,), (2,), (3,)]
    assert [call[1] for call in ocr.calls] == ["page:1", "page:2", "page:3"]
    assert all(warning.code != "ocr_required" for warning in artifact.warnings)


def test_render_budget_preserves_completed_pages_and_stops_before_next_page(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf_path = input_dir / "渲染预算.pdf"
    _make_blank_pdf(pdf_path, pages=2)
    clock = FakeClock()
    renderer = SlowPdfRenderer(clock, seconds_per_call=2.0)
    service = DocumentService(
        parsers={DocumentFormat.PDF: _pdf_parser_without_text},
        enricher=DocumentEnricher(
            pdf_renderer=renderer,
            processing_budget_sec=1.0,
            clock=clock,
        ),
    )

    artifact = service.parse(
        pdf_path,
        allowed_root=input_dir,
        work_dir=tmp_path / "work",
        render_pages=True,
    )

    assert [asset.location for asset in artifact.assets] == ["page:1"]
    assert [call[1] for call in renderer.calls] == [(1,)]
    budget_warning = next(warning for warning in artifact.warnings if warning.code == "processing_budget_exceeded")
    assert budget_warning.locations == ("page:2",)


def test_subprocess_render_timeout_is_capped_by_remaining_total_budget(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf_path = input_dir / "材料.pdf"
    _make_blank_pdf(pdf_path, pages=1)
    observed_timeouts: list[float] = []

    def fake_run(args, *, timeout_sec, **kwargs):
        observed_timeouts.append(timeout_sec)
        prefix = Path(args[-1])
        (prefix.parent / f"{prefix.name}-1.png").write_bytes(PNG_BYTES)
        return ""

    monkeypatch.setattr(enrichment_module, "_run_command", fake_run)
    renderer = SubprocessPdfRenderer(
        command="/opt/homebrew/bin/pdftoppm",
        backend_name="pdftoppm",
        timeout_sec=30,
    )
    service = DocumentService(
        parsers={DocumentFormat.PDF: _pdf_parser_without_text},
        enricher=DocumentEnricher(
            pdf_renderer=renderer,
            processing_budget_sec=1.25,
        ),
    )

    service.parse(
        pdf_path,
        allowed_root=input_dir,
        work_dir=tmp_path / "work",
        render_pages=True,
    )

    assert observed_timeouts
    assert 0 < observed_timeouts[0] <= 1.25


def test_ocr_reads_validated_snapshot_instead_of_reopening_asset_path(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf_path = input_dir / "扫描材料.pdf"
    _make_blank_pdf(pdf_path, pages=1)
    rendered_path_holder: dict[str, Path] = {}

    def parser(path: Path, *, asset_dir: Path) -> dict[str, object]:
        rendered_dir = asset_dir.parent / "rendered"
        rendered_dir.mkdir(parents=True, exist_ok=True)
        image_path = rendered_dir / "page-1.png"
        image_path.write_bytes(PNG_BYTES)
        rendered_path_holder["path"] = image_path.resolve()
        return {
            "blocks": (),
            "assets": (
                DocumentAsset(
                    asset_id="existing-1",
                    kind="rendered_page",
                    location="page:1",
                    path=str(image_path.resolve()),
                    content_type="image/png",
                    width=1,
                    height=1,
                ),
            ),
            "warnings": (DocumentWarning(code="ocr_required", message="需要 OCR", locations=("page:1",)),),
            "page_count": 1,
            "metadata": {"parser": "fake-pdf"},
        }

    service = DocumentService(
        parsers={DocumentFormat.PDF: parser},
        enricher=DocumentEnricher(
            ocr_backend=SnapshotCheckingOCRBackend(original_path_holder=rendered_path_holder),
        ),
    )
    artifact = service.parse(
        pdf_path,
        allowed_root=input_dir,
        work_dir=tmp_path / "work",
        ocr_scanned_pages=True,
    )

    assert artifact.metadata["ocr_used"] is True
    assert not tuple((rendered_path_holder["path"].parent).glob(".ocr-input-*.png"))


def test_ocr_snapshot_copy_is_bounded_to_size_validated_before_concurrent_growth(
    tmp_path,
    monkeypatch,
):
    work_path = tmp_path / "work"
    work_path.mkdir()
    image_path = work_path / "page.png"
    image_path.write_bytes(PNG_BYTES)
    original_read = enrichment_module.os.read
    appended = False

    def growing_read(file_descriptor, size):
        nonlocal appended
        chunk = original_read(file_descriptor, size)
        if size == 24 and not appended:
            with image_path.open("ab") as stream:
                stream.write(b"x" * 1024)
            appended = True
        return chunk

    monkeypatch.setattr(enrichment_module.os, "read", growing_read)
    snapshot = enrichment_module._create_validated_image_snapshot(
        image_path.resolve(),
        content_type="image/png",
        work_path=work_path,
        max_image_bytes=len(PNG_BYTES),
        max_image_pixels=10,
    )

    try:
        assert snapshot is not None
        assert snapshot.read_bytes() == PNG_BYTES
        assert image_path.stat().st_size > len(PNG_BYTES)
    finally:
        if snapshot is not None:
            snapshot.unlink(missing_ok=True)


def test_ocr_budget_preserves_completed_pages_and_required_locations(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf_path = input_dir / "OCR预算.pdf"
    _make_blank_pdf(pdf_path, pages=3)
    clock = FakeClock()
    ocr = SlowOCRBackend(clock, seconds_per_call=0.6)
    service = DocumentService(
        parsers={DocumentFormat.PDF: _pdf_parser_with_existing_rendered_assets(("page:1", "page:2", "page:3"))},
        enricher=DocumentEnricher(
            ocr_backend=ocr,
            processing_budget_sec=1.0,
            clock=clock,
        ),
    )

    artifact = service.parse(
        pdf_path,
        allowed_root=input_dir,
        work_dir=tmp_path / "work",
        ocr_scanned_pages=True,
    )

    assert [block.location for block in artifact.blocks if block.kind == "ocr_page"] == ["page:1", "page:2"]
    assert [call[1] for call in ocr.calls] == ["page:1", "page:2"]
    remaining = next(warning for warning in artifact.warnings if warning.code == "ocr_required")
    assert remaining.locations == ("page:3",)
    assert any(warning.code == "processing_budget_exceeded" for warning in artifact.warnings)


def test_ocr_page_limit_keeps_unprocessed_locations(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf_path = input_dir / "OCR页数上限.pdf"
    _make_blank_pdf(pdf_path, pages=3)
    ocr = FakeOCRBackend()
    service = DocumentService(
        parsers={DocumentFormat.PDF: _pdf_parser_with_existing_rendered_assets(("page:1", "page:2", "page:3"))},
        enricher=DocumentEnricher(ocr_backend=ocr, max_ocr_pages=1),
    )

    artifact = service.parse(
        pdf_path,
        allowed_root=input_dir,
        work_dir=tmp_path / "work",
        ocr_scanned_pages=True,
    )

    assert [call[1] for call in ocr.calls] == ["page:1"]
    remaining = next(warning for warning in artifact.warnings if warning.code == "ocr_required")
    assert remaining.locations == ("page:2", "page:3")
    limit_warning = next(warning for warning in artifact.warnings if warning.code == "ocr_limit_exceeded")
    assert limit_warning.locations == ("page:2", "page:3")


def test_document_json_uses_unique_temporary_files_for_concurrent_writes(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf_path = input_dir / "并发材料.pdf"
    _make_blank_pdf(pdf_path, pages=2)
    service = DocumentService(parsers={DocumentFormat.PDF: _pdf_parser_without_text})
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    temporary_names: list[str] = []
    original_replace = Path.replace

    def synchronized_replace(self: Path, target: Path):
        if Path(target).name == "document.json":
            with lock:
                temporary_names.append(self.name)
            barrier.wait(timeout=5)
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", synchronized_replace)

    def parse_once():
        return service.parse(pdf_path, allowed_root=input_dir, work_dir=tmp_path / "work")

    with ThreadPoolExecutor(max_workers=2) as executor:
        artifacts = [future.result() for future in (executor.submit(parse_once), executor.submit(parse_once))]

    assert len(set(temporary_names)) == 2
    stored = json.loads(Path(artifacts[0].artifact_path).read_text(encoding="utf-8"))
    assert stored["artifact_id"] == artifacts[0].artifact_id
