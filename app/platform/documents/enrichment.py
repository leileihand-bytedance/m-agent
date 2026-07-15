from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import time
from typing import Callable, Iterable, Protocol
import uuid

from pypdf import PdfReader

from .models import DocumentArtifact, DocumentAsset, DocumentBlock, DocumentFormat, DocumentWarning


DEFAULT_RENDER_TIMEOUT_SEC = 30
DEFAULT_OCR_TIMEOUT_SEC = 30
DEFAULT_MAX_RENDER_PAGES = 200
DEFAULT_MAX_RENDER_OUTPUTS = 400
DEFAULT_MAX_IMAGE_PIXELS = 25_000_000
DEFAULT_MAX_IMAGE_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_TOTAL_RENDER_BYTES = 250 * 1024 * 1024
DEFAULT_MAX_RENDER_SIDE = 2400
DEFAULT_PROCESSING_BUDGET_SEC = 300
DEFAULT_MAX_OCR_PAGES = 50
STANDARD_SOFFICE_PATHS = (
    Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
    Path("/Applications/OpenOffice.app/Contents/MacOS/soffice"),
)
VISION_SWIFT_SCRIPT = """\
import AppKit
import Foundation
import Vision

let arguments = CommandLine.arguments
guard arguments.count >= 2 else {
    FileHandle.standardError.write(Data("missing image path".utf8))
    exit(2)
}

let imagePath = arguments[1]
guard let image = NSImage(contentsOfFile: imagePath) else {
    FileHandle.standardError.write(Data("image open failed".utf8))
    exit(3)
}

var proposedRect = CGRect(origin: .zero, size: image.size)
guard let cgImage = image.cgImage(forProposedRect: &proposedRect, context: nil, hints: nil) else {
    FileHandle.standardError.write(Data("cgimage failed".utf8))
    exit(4)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
do {
    try handler.perform([request])
    let observations = request.results as? [VNRecognizedTextObservation] ?? []
    let text = observations.compactMap { observation in
        observation.topCandidates(1).first?.string
    }.joined(separator: "\\n")
    print(text)
} catch {
    FileHandle.standardError.write(Data("vision failed".utf8))
    exit(5)
}
"""


@dataclass(frozen=True)
class CommandCapability:
    backend: str
    command: str


@dataclass(frozen=True)
class DocumentEnrichmentCapabilities:
    pdf_renderer: CommandCapability | None
    office_converter: CommandCapability | None
    ocr: CommandCapability | None


@dataclass(frozen=True)
class RenderedPage:
    page_number: int
    location: str
    path: Path
    content_type: str = "image/png"
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class OCRPageResult:
    location: str
    text: str


class PdfRendererBackend(Protocol):
    backend_name: str

    def render_pdf(
        self,
        pdf_path: Path,
        *,
        output_dir: Path,
        page_numbers: tuple[int, ...],
        location_prefix: str,
    ) -> tuple[RenderedPage, ...]: ...


class OfficeToPdfConverter(Protocol):
    backend_name: str

    def convert_to_pdf(self, source_path: Path, *, output_dir: Path) -> Path: ...


class OCRBackend(Protocol):
    backend_name: str

    def recognize_page(self, image_path: Path, *, location: str) -> OCRPageResult: ...


def discover_enrichment_capabilities(
    *,
    pdf_renderer_command: str | Path | None = None,
    office_command: str | Path | None = None,
    ocr_command: str | Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
    path_exists: Callable[[Path], bool] | None = None,
    path_is_file: Callable[[Path], bool] | None = None,
    path_is_executable: Callable[[Path], bool] | None = None,
    system_name: str | None = None,
) -> DocumentEnrichmentCapabilities:
    exists = path_exists or (lambda path: path.exists())
    is_file = path_is_file or (lambda path: path.is_file())
    is_executable = path_is_executable or (lambda path: os.access(path, os.X_OK))
    system = system_name or platform.system()
    pdf_renderer = _discover_pdf_renderer(
        configured=pdf_renderer_command,
        which=which,
        exists=exists,
        is_file=is_file,
        is_executable=is_executable,
    )
    office_converter = _discover_office_converter(
        configured=office_command,
        which=which,
        exists=exists,
        is_file=is_file,
        is_executable=is_executable,
    )
    ocr = _discover_ocr_backend(
        configured=ocr_command,
        which=which,
        exists=exists,
        is_file=is_file,
        is_executable=is_executable,
        system_name=system,
    )
    return DocumentEnrichmentCapabilities(
        pdf_renderer=pdf_renderer,
        office_converter=office_converter,
        ocr=ocr,
    )


class DocumentEnricher:
    def __init__(
        self,
        *,
        pdf_renderer: PdfRendererBackend | None = None,
        office_converter: OfficeToPdfConverter | None = None,
        ocr_backend: OCRBackend | None = None,
        pdf_renderer_command: str | Path | None = None,
        office_command: str | Path | None = None,
        ocr_command: str | Path | None = None,
        render_timeout_sec: int = DEFAULT_RENDER_TIMEOUT_SEC,
        ocr_timeout_sec: int = DEFAULT_OCR_TIMEOUT_SEC,
        max_render_pages: int = DEFAULT_MAX_RENDER_PAGES,
        max_render_outputs: int = DEFAULT_MAX_RENDER_OUTPUTS,
        max_image_pixels: int = DEFAULT_MAX_IMAGE_PIXELS,
        max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
        max_total_render_bytes: int = DEFAULT_MAX_TOTAL_RENDER_BYTES,
        processing_budget_sec: float = DEFAULT_PROCESSING_BUDGET_SEC,
        max_ocr_pages: int = DEFAULT_MAX_OCR_PAGES,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._pdf_renderer = pdf_renderer
        self._office_converter = office_converter
        self._ocr_backend = ocr_backend
        self._capabilities = discover_enrichment_capabilities(
            pdf_renderer_command=pdf_renderer_command,
            office_command=office_command,
            ocr_command=ocr_command,
        )
        self._render_timeout_sec = max(1, int(render_timeout_sec))
        self._ocr_timeout_sec = max(1, int(ocr_timeout_sec))
        self._max_render_pages = max(1, int(max_render_pages))
        self._max_render_outputs = max(1, int(max_render_outputs))
        self._max_image_pixels = max(1, int(max_image_pixels))
        self._max_image_bytes = max(1, int(max_image_bytes))
        self._max_total_render_bytes = max(1, int(max_total_render_bytes))
        self._processing_budget_sec = max(0.001, float(processing_budget_sec))
        self._max_ocr_pages = max(1, int(max_ocr_pages))
        self._clock = clock

    def enrich(
        self,
        artifact: DocumentArtifact,
        *,
        allowed_root: str | Path,
        work_dir: str | Path,
        render_pages: bool = False,
        ocr_scanned_pages: bool = False,
    ) -> DocumentArtifact:
        if not render_pages and not ocr_scanned_pages:
            return artifact

        work_path = _resolve_work_dir(work_dir, allowed_root=allowed_root)
        artifact_dir = work_path / "documents" / artifact.artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        deadline = self._clock() + self._processing_budget_sec

        blocks = list(artifact.blocks)
        assets = list(artifact.assets)
        warnings = list(artifact.warnings)
        metadata = dict(artifact.metadata)

        render_used = False
        render_backend_name = ""
        ocr_used = False
        ocr_backend_name = ""

        if render_pages:
            rendered_assets, render_warnings, render_backend_name = self._render_artifact_pages(
                artifact,
                work_path=work_path,
                artifact_dir=artifact_dir,
                deadline=deadline,
            )
            render_used = bool(rendered_assets)
            assets = _merge_assets(assets, rendered_assets)
            warnings = _merge_warnings(warnings, render_warnings)
            if render_backend_name:
                metadata["render_backend"] = render_backend_name
            metadata["render_used"] = render_used

        if ocr_scanned_pages and artifact.format == DocumentFormat.PDF:
            (
                ocr_blocks,
                ocr_warnings,
                updated_assets,
                ocr_backend_name,
                render_used_for_ocr,
            ) = self._ocr_scanned_pdf_pages(
                artifact,
                warnings=warnings,
                assets=assets,
                work_path=work_path,
                artifact_dir=artifact_dir,
                deadline=deadline,
            )
            if render_used_for_ocr:
                render_used = True
                metadata["render_used"] = True
            if ocr_backend_name:
                metadata["ocr_backend"] = ocr_backend_name
            ocr_used = bool(ocr_blocks)
            metadata["ocr_used"] = ocr_used
            assets = updated_assets
            blocks.extend(ocr_blocks)
            warnings = ocr_warnings
            if not ocr_backend_name and "ocr_backend" not in metadata:
                metadata["ocr_backend"] = ""

        if render_pages and "render_used" not in metadata:
            metadata["render_used"] = render_used
        if ocr_scanned_pages and "ocr_used" not in metadata:
            metadata["ocr_used"] = ocr_used
        if ocr_scanned_pages and "ocr_backend" not in metadata:
            metadata["ocr_backend"] = ocr_backend_name

        sorted_blocks = tuple(sorted(blocks, key=_block_sort_key))
        full_text = "\n\n".join(block.text.strip() for block in sorted_blocks if block.text.strip())
        sorted_assets = tuple(sorted(assets, key=_asset_sort_key))
        sorted_warnings = tuple(_dedupe_warning_list(warnings))
        return replace(
            artifact,
            full_text=full_text,
            blocks=sorted_blocks,
            assets=sorted_assets,
            warnings=sorted_warnings,
            metadata=metadata,
        )

    def _render_artifact_pages(
        self,
        artifact: DocumentArtifact,
        *,
        work_path: Path,
        artifact_dir: Path,
        deadline: float,
        requested_locations: tuple[str, ...] | None = None,
    ) -> tuple[list[DocumentAsset], list[DocumentWarning], str]:
        page_numbers = _page_numbers_for_artifact(artifact, requested_locations=requested_locations)
        if not page_numbers:
            return [], [], ""
        if len(page_numbers) > self._max_render_pages:
            return [], [
                DocumentWarning(
                    code="render_limit_exceeded",
                    message="待渲染页数超过当前限制，已跳过页面渲染。",
                    locations=tuple(_format_location(artifact.format, page_number) for page_number in page_numbers),
                )
            ], ""

        renderer = self._get_pdf_renderer()
        if renderer is None:
            return [], [
                DocumentWarning(
                    code="renderer_unavailable",
                    message="当前环境缺少页面渲染能力，已跳过页面渲染。",
                    locations=tuple(_format_location(artifact.format, page_number) for page_number in page_numbers),
                )
            ], ""

        source_path = Path(artifact.source.path).resolve()
        pdf_source = source_path
        if artifact.format == DocumentFormat.PPTX:
            if self._clock() >= deadline:
                return [], [_processing_budget_warning(artifact, page_numbers)], getattr(renderer, "backend_name", "")
            converter = self._get_office_converter()
            if converter is None:
                return [], [
                    DocumentWarning(
                        code="renderer_unavailable",
                        message="当前环境缺少 PPT 页面渲染所需转换器，已跳过页面渲染。",
                        locations=tuple(_format_location(artifact.format, page_number) for page_number in page_numbers),
                    )
                ], ""
            try:
                converted_dir = artifact_dir / "converted"
                converted_dir.mkdir(parents=True, exist_ok=True)
                if isinstance(converter, SubprocessOfficeConverter):
                    pdf_source = Path(
                        converter.convert_to_pdf(
                            source_path,
                            output_dir=converted_dir,
                            timeout_sec=_remaining_timeout(
                                deadline,
                                configured_timeout=self._render_timeout_sec,
                                clock=self._clock,
                            ),
                        )
                    )
                else:
                    pdf_source = Path(converter.convert_to_pdf(source_path, output_dir=converted_dir))
            except Exception:
                return [], [
                    DocumentWarning(
                        code="renderer_unavailable",
                        message="PPT 页面渲染转换不可用，已保留文本解析结果。",
                        locations=tuple(_format_location(artifact.format, page_number) for page_number in page_numbers),
                    )
                ], ""
            if not _is_safe_regular_file(pdf_source, work_path):
                return [], [
                    DocumentWarning(
                        code="render_output_invalid",
                        message="页面渲染输出不在任务工作目录内，已忽略本次渲染结果。",
                        locations=tuple(_format_location(artifact.format, page_number) for page_number in page_numbers),
                    )
                ], getattr(renderer, "backend_name", "")
            try:
                converted_page_count = len(PdfReader(str(pdf_source), strict=False).pages)
            except Exception:
                converted_page_count = None
            if artifact.page_count is None or converted_page_count != artifact.page_count:
                return [], [
                    DocumentWarning(
                        code="ppt_render_page_mismatch",
                        message="PPT 转换后的 PDF 页数与原始幻灯片数不一致，已跳过页面渲染。",
                        locations=tuple(_format_location(artifact.format, page_number) for page_number in page_numbers),
                    )
                ], getattr(renderer, "backend_name", "")

        render_dir = artifact_dir / "rendered"
        render_dir.mkdir(parents=True, exist_ok=True)
        assets: list[DocumentAsset] = []
        rendered_bytes = 0
        warning_list: list[DocumentWarning] = []
        location_prefix = "page" if artifact.format == DocumentFormat.PDF else "slide"
        for index, page_number in enumerate(page_numbers):
            remaining_page_numbers = page_numbers[index:]
            if self._clock() >= deadline:
                warning_list.append(_processing_budget_warning(artifact, remaining_page_numbers))
                break
            if len(assets) >= self._max_render_outputs:
                warning_list.append(
                    DocumentWarning(
                        code="render_output_limit_exceeded",
                        message="页面渲染输出数量超过限制，已跳过额外结果。",
                        locations=tuple(
                            _format_location(artifact.format, remaining_page)
                            for remaining_page in remaining_page_numbers
                        ),
                    )
                )
                break

            expected_location = _format_location(artifact.format, page_number)
            try:
                render_kwargs = {
                    "output_dir": render_dir,
                    "page_numbers": (page_number,),
                    "location_prefix": location_prefix,
                }
                if isinstance(renderer, SubprocessPdfRenderer):
                    rendered = tuple(
                        renderer.render_pdf(
                            pdf_source,
                            **render_kwargs,
                            timeout_sec=_remaining_timeout(
                                deadline,
                                configured_timeout=self._render_timeout_sec,
                                clock=self._clock,
                            ),
                        )
                    )
                else:
                    rendered = tuple(renderer.render_pdf(
                        pdf_source,
                        **render_kwargs,
                    ))
            except Exception:
                warning_list.append(
                    DocumentWarning(
                        code="render_failed",
                        message="页面渲染失败，已保留文本解析结果。",
                        locations=(expected_location,),
                    )
                )
                continue
            if not rendered:
                warning_list.append(
                    DocumentWarning(
                        code="render_failed",
                        message="页面渲染未产生有效输出，已保留文本解析结果。",
                        locations=(expected_location,),
                    )
                )
                continue

            for page in rendered:
                if page.page_number != page_number or page.location != expected_location:
                    warning_list.append(
                        DocumentWarning(
                            code="render_output_invalid",
                            message="页面渲染输出页码与请求不一致，已忽略该结果。",
                            locations=(expected_location,),
                        )
                    )
                    continue
                normalized = self._normalize_rendered_page(
                    artifact=artifact,
                    rendered_page=page,
                    work_path=work_path,
                )
                if isinstance(normalized, DocumentAsset):
                    asset_size = Path(normalized.path).stat().st_size
                    if rendered_bytes + asset_size > self._max_total_render_bytes:
                        Path(normalized.path).unlink(missing_ok=True)
                        warning_list.append(
                            DocumentWarning(
                                code="render_total_bytes_exceeded",
                                message="页面渲染结果达到总容量上限，已停止生成额外页面图片。",
                                locations=tuple(
                                    _format_location(artifact.format, pending_page)
                                    for pending_page in remaining_page_numbers
                                ),
                            )
                        )
                        return assets, warning_list, getattr(renderer, "backend_name", "")
                    if len(assets) < self._max_render_outputs:
                        assets.append(normalized)
                        rendered_bytes += asset_size
                    else:
                        warning_list.append(
                            DocumentWarning(
                                code="render_output_limit_exceeded",
                                message="页面渲染输出数量超过限制，已跳过额外结果。",
                                locations=(expected_location,),
                            )
                        )
                else:
                    warning_list.append(normalized)
        return assets, warning_list, getattr(renderer, "backend_name", "")

    def _ocr_scanned_pdf_pages(
        self,
        artifact: DocumentArtifact,
        *,
        warnings: list[DocumentWarning],
        assets: list[DocumentAsset],
        work_path: Path,
        artifact_dir: Path,
        deadline: float,
    ) -> tuple[list[DocumentBlock], list[DocumentWarning], list[DocumentAsset], str, bool]:
        ocr_warnings = [warning for warning in warnings if warning.code == "ocr_required"]
        needed_locations = _sorted_unique_locations(
            location
            for warning in ocr_warnings
            for location in warning.locations
        )
        if not needed_locations:
            return [], warnings, assets, "", False

        backend = self._get_ocr_backend()
        if backend is None:
            updated = _replace_warning(
                warnings,
                new_warning=DocumentWarning(
                    code="ocr_backend_unavailable",
                    message="当前环境缺少可用 OCR 后端，已保留待 OCR 页标记。",
                    locations=needed_locations,
                ),
            )
            return [], updated, assets, "", False

        processable_locations = needed_locations[: self._max_ocr_pages]
        limited_locations = needed_locations[self._max_ocr_pages :]
        if limited_locations:
            warnings = _merge_warnings(
                warnings,
                [
                    DocumentWarning(
                        code="ocr_limit_exceeded",
                        message="待 OCR 页数超过当前限制，额外页面仍保留待处理标记。",
                        locations=limited_locations,
                    )
                ],
            )

        render_used = False
        existing_candidates = {
            asset.location: asset
            for asset in assets
            if asset.kind == "rendered_page" and asset.location in processable_locations
        }
        existing_by_location: dict[str, DocumentAsset] = {}
        invalid_asset_ids: set[str] = set()
        invalid_locations: set[str] = set()
        for location, asset in existing_candidates.items():
            normalized = self._normalize_ocr_asset(asset, work_path=work_path)
            if isinstance(normalized, DocumentAsset):
                existing_by_location[location] = normalized
                assets = _merge_assets(assets, [normalized])
            else:
                warnings = _merge_warnings(warnings, [normalized])
                invalid_asset_ids.add(asset.asset_id)
                invalid_locations.add(location)
        if invalid_asset_ids:
            assets = [asset for asset in assets if asset.asset_id not in invalid_asset_ids]

        missing_locations = tuple(
            location
            for location in processable_locations
            if location not in existing_by_location and location not in invalid_locations
        )
        if missing_locations:
            rendered_assets, render_warnings, _ = self._render_artifact_pages(
                artifact,
                work_path=work_path,
                artifact_dir=artifact_dir,
                deadline=deadline,
                requested_locations=missing_locations,
            )
            if rendered_assets:
                render_used = True
                assets = _merge_assets(assets, rendered_assets)
                for asset in rendered_assets:
                    existing_by_location[asset.location] = asset
            warnings = _merge_warnings(warnings, render_warnings)

        ocr_blocks: list[DocumentBlock] = []
        success_locations: list[str] = []
        failed_locations: list[str] = []
        for index, location in enumerate(processable_locations):
            asset = existing_by_location.get(location)
            if asset is None:
                continue
            if self._clock() >= deadline:
                warnings = _merge_warnings(
                    warnings,
                    [_processing_budget_warning(artifact, processable_locations[index:])],
                )
                break
            normalized = self._normalize_ocr_asset(asset, work_path=work_path)
            if not isinstance(normalized, DocumentAsset):
                warnings = _merge_warnings(warnings, [normalized])
                assets = [existing for existing in assets if existing.asset_id != asset.asset_id]
                continue
            snapshot_path = _create_validated_image_snapshot(
                Path(normalized.path),
                content_type=normalized.content_type,
                work_path=work_path,
                max_image_bytes=self._max_image_bytes,
                max_image_pixels=self._max_image_pixels,
            )
            if snapshot_path is None:
                warnings = _merge_warnings(
                    warnings,
                    [
                        DocumentWarning(
                            code="ocr_asset_invalid",
                            message="待 OCR 页面图片未通过读取时校验，已跳过该页 OCR。",
                            locations=(location,),
                        )
                    ],
                )
                assets = [existing for existing in assets if existing.asset_id != asset.asset_id]
                continue
            try:
                if isinstance(backend, (TesseractOCRBackend, VisionOCRBackend)):
                    result = backend.recognize_page(
                        snapshot_path,
                        location=location,
                        timeout_sec=_remaining_timeout(
                            deadline,
                            configured_timeout=self._ocr_timeout_sec,
                            clock=self._clock,
                        ),
                    )
                else:
                    result = backend.recognize_page(snapshot_path, location=location)
            except Exception:
                failed_locations.append(location)
                continue
            finally:
                snapshot_path.unlink(missing_ok=True)
            text = result.text.strip()
            if not text:
                failed_locations.append(location)
                continue
            success_locations.append(location)
            page_number = _location_page_number(location) or len(success_locations)
            ocr_blocks.append(
                DocumentBlock(
                    block_id=f"ocr-page-{page_number:05d}",
                    kind="ocr_page",
                    text=text,
                    location=location,
                )
            )

        updated_warnings = [warning for warning in warnings if warning.code != "ocr_required"]
        remaining_locations = tuple(location for location in needed_locations if location not in success_locations)
        if remaining_locations:
            updated_warnings.append(
                DocumentWarning(
                    code="ocr_required",
                    message=ocr_warnings[0].message,
                    locations=remaining_locations,
                )
            )
        if failed_locations:
            updated_warnings.append(
                DocumentWarning(
                    code="ocr_page_failed",
                    message="部分扫描页 OCR 失败或结果为空，仍需人工确认。",
                    locations=tuple(dict.fromkeys(failed_locations)),
                )
            )
        return ocr_blocks, _dedupe_warning_list(updated_warnings), assets, backend.backend_name, render_used

    def _normalize_rendered_page(
        self,
        *,
        artifact: DocumentArtifact,
        rendered_page: RenderedPage,
        work_path: Path,
    ) -> DocumentAsset | DocumentWarning:
        validated = _validate_image_file(
            rendered_page.path,
            content_type=rendered_page.content_type,
            work_path=work_path,
            max_image_bytes=self._max_image_bytes,
            max_image_pixels=self._max_image_pixels,
        )
        if validated is None:
            return DocumentWarning(
                code="render_output_invalid",
                message="页面渲染输出未通过路径、格式或大小校验，已忽略该页结果。",
                locations=(rendered_page.location,),
            )
        resolved, width, height = validated
        return DocumentAsset(
            asset_id=f"{artifact.artifact_id}-render-{rendered_page.page_number}",
            kind="rendered_page",
            location=rendered_page.location,
            path=str(resolved),
            content_type="image/png",
            width=width,
            height=height,
        )

    def _normalize_ocr_asset(
        self,
        asset: DocumentAsset,
        *,
        work_path: Path,
    ) -> DocumentAsset | DocumentWarning:
        validated = _validate_image_file(
            Path(asset.path),
            content_type=asset.content_type,
            work_path=work_path,
            max_image_bytes=self._max_image_bytes,
            max_image_pixels=self._max_image_pixels,
        )
        if validated is None:
            return DocumentWarning(
                code="ocr_asset_invalid",
                message="待 OCR 页面图片未通过路径、格式或大小校验，已跳过该页 OCR。",
                locations=(asset.location,),
            )
        resolved, width, height = validated
        return replace(
            asset,
            path=str(resolved),
            content_type="image/png",
            width=width,
            height=height,
        )

    def _get_pdf_renderer(self) -> PdfRendererBackend | None:
        if self._pdf_renderer is None and self._capabilities.pdf_renderer is not None:
            self._pdf_renderer = SubprocessPdfRenderer(
                command=self._capabilities.pdf_renderer.command,
                backend_name=self._capabilities.pdf_renderer.backend,
                timeout_sec=self._render_timeout_sec,
            )
        return self._pdf_renderer

    def _get_office_converter(self) -> OfficeToPdfConverter | None:
        if self._office_converter is None and self._capabilities.office_converter is not None:
            self._office_converter = SubprocessOfficeConverter(
                command=self._capabilities.office_converter.command,
                backend_name=self._capabilities.office_converter.backend,
                timeout_sec=self._render_timeout_sec,
            )
        return self._office_converter

    def _get_ocr_backend(self) -> OCRBackend | None:
        if self._ocr_backend is None and self._capabilities.ocr is not None:
            capability = self._capabilities.ocr
            if capability.backend == "vision":
                self._ocr_backend = VisionOCRBackend(
                    command=capability.command,
                    backend_name=capability.backend,
                    timeout_sec=self._ocr_timeout_sec,
                )
            else:
                self._ocr_backend = TesseractOCRBackend(
                    command=capability.command,
                    backend_name=capability.backend,
                    timeout_sec=self._ocr_timeout_sec,
                )
        return self._ocr_backend


class SubprocessPdfRenderer:
    def __init__(
        self,
        *,
        command: str,
        backend_name: str,
        timeout_sec: int,
        max_output_side: int = DEFAULT_MAX_RENDER_SIDE,
    ):
        self.command = command
        self.backend_name = backend_name
        self.timeout_sec = timeout_sec
        self.max_output_side = max(1, int(max_output_side))

    def render_pdf(
        self,
        pdf_path: Path,
        *,
        output_dir: Path,
        page_numbers: tuple[int, ...],
        location_prefix: str,
        timeout_sec: float | None = None,
    ) -> tuple[RenderedPage, ...]:
        output_dir.mkdir(parents=True, exist_ok=True)
        rendered: list[RenderedPage] = []
        for page_number in page_numbers:
            prefix = output_dir / f".{location_prefix}-{page_number:04d}-{uuid.uuid4().hex}"
            existing_matches = {path.resolve() for path in output_dir.glob(f"{prefix.name}*.png")}
            args = [
                self.command,
                "-png",
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                "-scale-to",
                str(self.max_output_side),
                str(pdf_path),
                str(prefix),
            ]
            _run_command(
                args,
                cwd=output_dir,
                timeout_sec=self.timeout_sec if timeout_sec is None else timeout_sec,
            )
            matches = sorted(
                path
                for path in output_dir.glob(f"{prefix.name}*.png")
                if path.resolve() not in existing_matches
            )
            if not matches:
                raise RuntimeError("rendered output missing")
            image_path = matches[0]
            stable_path = output_dir / f"{location_prefix}-{page_number:04d}.png"
            image_path.replace(stable_path)
            for extra_path in matches[1:]:
                extra_path.unlink(missing_ok=True)
            image_path = stable_path
            width, height = _read_image_size(image_path)
            rendered.append(
                RenderedPage(
                    page_number=page_number,
                    location=f"{location_prefix}:{page_number}",
                    path=image_path,
                    content_type="image/png",
                    width=width,
                    height=height,
                )
            )
        return tuple(rendered)


class SubprocessOfficeConverter:
    def __init__(self, *, command: str, backend_name: str, timeout_sec: int):
        self.command = command
        self.backend_name = backend_name
        self.timeout_sec = timeout_sec

    def convert_to_pdf(
        self,
        source_path: Path,
        *,
        output_dir: Path,
        timeout_sec: float | None = None,
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        profile_dir = output_dir / "libreoffice-profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"{source_path.stem}.pdf"
        target.unlink(missing_ok=True)
        args = [
            self.command,
            "--headless",
            f"-env:UserInstallation={profile_dir.as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_dir),
            str(source_path),
        ]
        env = _safe_subprocess_env(home=profile_dir)
        _run_command(
            args,
            cwd=output_dir,
            timeout_sec=self.timeout_sec if timeout_sec is None else timeout_sec,
            env=env,
        )
        if not target.exists():
            raise RuntimeError("converted pdf missing")
        return target


class TesseractOCRBackend:
    def __init__(self, *, command: str, backend_name: str, timeout_sec: int):
        self.command = command
        self.backend_name = backend_name
        self.timeout_sec = timeout_sec

    def recognize_page(
        self,
        image_path: Path,
        *,
        location: str,
        timeout_sec: float | None = None,
    ) -> OCRPageResult:
        args = [self.command, str(image_path), "stdout", "--psm", "6"]
        stdout = _run_command(
            args,
            cwd=image_path.parent,
            timeout_sec=self.timeout_sec if timeout_sec is None else timeout_sec,
            capture_stdout=True,
        )
        return OCRPageResult(location=location, text=stdout.strip())


class VisionOCRBackend:
    def __init__(self, *, command: str, backend_name: str, timeout_sec: int):
        self.command = command
        self.backend_name = backend_name
        self.timeout_sec = timeout_sec

    def recognize_page(
        self,
        image_path: Path,
        *,
        location: str,
        timeout_sec: float | None = None,
    ) -> OCRPageResult:
        script_fd, script_name = tempfile.mkstemp(
            prefix=".vision-ocr-",
            suffix=".swift",
            dir=image_path.parent,
            text=True,
        )
        script_path = Path(script_name)
        created_stat = os.fstat(script_fd)
        try:
            with os.fdopen(script_fd, "w", encoding="utf-8") as stream:
                stream.write(VISION_SWIFT_SCRIPT)
                stream.flush()
                os.fsync(stream.fileno())
            current_stat = os.lstat(script_path)
            if (
                not stat.S_ISREG(current_stat.st_mode)
                or stat.S_ISLNK(current_stat.st_mode)
                or (current_stat.st_dev, current_stat.st_ino) != (created_stat.st_dev, created_stat.st_ino)
            ):
                raise RuntimeError("controlled OCR script validation failed")
            stdout = _run_command(
                [self.command, str(script_path), str(image_path)],
                cwd=image_path.parent,
                timeout_sec=self.timeout_sec if timeout_sec is None else timeout_sec,
                capture_stdout=True,
            )
            return OCRPageResult(location=location, text=stdout.strip())
        finally:
            try:
                current_stat = os.lstat(script_path)
            except FileNotFoundError:
                pass
            else:
                if (
                    stat.S_ISREG(current_stat.st_mode)
                    and not stat.S_ISLNK(current_stat.st_mode)
                    and (current_stat.st_dev, current_stat.st_ino) == (created_stat.st_dev, created_stat.st_ino)
                ):
                    script_path.unlink()


def _discover_pdf_renderer(
    *,
    configured: str | Path | None,
    which: Callable[[str], str | None],
    exists: Callable[[Path], bool],
    is_file: Callable[[Path], bool],
    is_executable: Callable[[Path], bool],
) -> CommandCapability | None:
    if configured:
        command = _validated_command_path(
            configured,
            exists=exists,
            is_file=is_file,
            is_executable=is_executable,
        )
        if command is not None:
            return CommandCapability(backend=command.name, command=str(command))
    for name in ("pdftoppm", "pdftocairo"):
        command = _validated_command_path(
            which(name),
            exists=exists,
            is_file=is_file,
            is_executable=is_executable,
        )
        if command is not None:
            return CommandCapability(backend=name, command=str(command))
    return None


def _discover_office_converter(
    *,
    configured: str | Path | None,
    which: Callable[[str], str | None],
    exists: Callable[[Path], bool],
    is_file: Callable[[Path], bool],
    is_executable: Callable[[Path], bool],
) -> CommandCapability | None:
    if configured:
        command = _validated_command_path(
            configured,
            exists=exists,
            is_file=is_file,
            is_executable=is_executable,
        )
        if command is not None:
            return CommandCapability(backend="soffice", command=str(command))
    for name in ("soffice", "libreoffice"):
        command = _validated_command_path(
            which(name),
            exists=exists,
            is_file=is_file,
            is_executable=is_executable,
        )
        if command is not None:
            return CommandCapability(backend="soffice", command=str(command))
    for standard_path in STANDARD_SOFFICE_PATHS:
        command = _validated_command_path(
            standard_path,
            exists=exists,
            is_file=is_file,
            is_executable=is_executable,
        )
        if command is not None:
            return CommandCapability(backend="soffice", command=str(command))
    return None


def _discover_ocr_backend(
    *,
    configured: str | Path | None,
    which: Callable[[str], str | None],
    exists: Callable[[Path], bool],
    is_file: Callable[[Path], bool],
    is_executable: Callable[[Path], bool],
    system_name: str,
) -> CommandCapability | None:
    if configured:
        command = _validated_command_path(
            configured,
            exists=exists,
            is_file=is_file,
            is_executable=is_executable,
        )
        if command is not None:
            backend = (
                "vision"
                if system_name == "Darwin" and command.name.lower() == "swift"
                else "tesseract"
            )
            return CommandCapability(backend=backend, command=str(command))
    command = _validated_command_path(
        which("tesseract"),
        exists=exists,
        is_file=is_file,
        is_executable=is_executable,
    )
    if command is not None:
        return CommandCapability(backend="tesseract", command=str(command))
    if system_name == "Darwin":
        swift_path = _validated_command_path(
            Path("/usr/bin/swift"),
            exists=exists,
            is_file=is_file,
            is_executable=is_executable,
        )
        if swift_path is not None:
            return CommandCapability(backend="vision", command=str(swift_path))
    return None


def _validated_command_path(
    candidate: str | Path | None,
    *,
    exists: Callable[[Path], bool],
    is_file: Callable[[Path], bool],
    is_executable: Callable[[Path], bool],
) -> Path | None:
    if not candidate:
        return None
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        return None
    normalized = path.resolve(strict=False)
    if not exists(normalized) or not is_file(normalized) or not is_executable(normalized):
        return None
    return normalized


def _run_command(
    args: list[str],
    *,
    cwd: Path,
    timeout_sec: float,
    env: dict[str, str] | None = None,
    capture_stdout: bool = False,
) -> str:
    child_env = env or _safe_subprocess_env(home=cwd)
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        env=child_env,
        stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        text=True,
        timeout=timeout_sec,
    )
    if completed.returncode != 0:
        raise RuntimeError("command failed")
    return completed.stdout if capture_stdout else ""


def _remaining_timeout(
    deadline: float,
    *,
    configured_timeout: float,
    clock: Callable[[], float],
) -> float:
    remaining = deadline - clock()
    if remaining <= 0:
        raise TimeoutError("document processing budget exhausted")
    return min(float(configured_timeout), remaining)


def _safe_subprocess_env(*, home: Path) -> dict[str, str]:
    """只向文档工具传递运行所需环境，隔离模型和 Bot 密钥。"""

    environment = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "TMPDIR": tempfile.gettempdir(),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
    }
    locale = os.environ.get("LC_ALL", "").strip()
    if locale:
        environment["LC_ALL"] = locale
    return environment


def _resolve_work_dir(work_dir: str | Path, *, allowed_root: str | Path) -> Path:
    input_root = Path(allowed_root).resolve()
    task_root = input_root.parent
    expected_work_root = task_root / "work"
    candidate = Path(work_dir).resolve()
    if candidate != expected_work_root and expected_work_root not in candidate.parents:
        raise ValueError("文档中间产物必须保存在当前任务 work 目录")
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def _is_within_directory(path: Path, root: Path) -> bool:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def _is_safe_regular_file(path: Path, root: Path) -> bool:
    try:
        if not path.is_absolute() or path.is_symlink() or path.suffix.lower() != ".pdf":
            return False
        resolved = path.resolve(strict=True)
        if not _is_within_directory(resolved, root):
            return False
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        file_descriptor = os.open(resolved, flags)
        try:
            file_stat = os.fstat(file_descriptor)
            return stat.S_ISREG(file_stat.st_mode) and file_stat.st_size > 0
        finally:
            os.close(file_descriptor)
    except (OSError, RuntimeError):
        return False


def _validate_image_file(
    path: Path,
    *,
    content_type: str,
    work_path: Path,
    max_image_bytes: int,
    max_image_pixels: int,
) -> tuple[Path, int, int] | None:
    try:
        if (
            not path.is_absolute()
            or path.is_symlink()
            or path.suffix.lower() != ".png"
            or content_type != "image/png"
        ):
            return None
        resolved = path.resolve(strict=True)
        if not _is_within_directory(resolved, work_path):
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        file_descriptor = os.open(resolved, flags)
        try:
            file_stat = os.fstat(file_descriptor)
            if (
                not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_size <= 0
                or file_stat.st_size > max_image_bytes
            ):
                return None
            header = os.read(file_descriptor, 24)
        finally:
            os.close(file_descriptor)
        if (
            len(header) < 24
            or header[:8] != b"\x89PNG\r\n\x1a\n"
            or header[12:16] != b"IHDR"
        ):
            return None
        width, height = struct.unpack(">II", header[16:24])
        if width <= 0 or height <= 0 or width * height > max_image_pixels:
            return None
        return resolved, int(width), int(height)
    except (OSError, RuntimeError, ValueError):
        return None


def _create_validated_image_snapshot(
    path: Path,
    *,
    content_type: str,
    work_path: Path,
    max_image_bytes: int,
    max_image_pixels: int,
) -> Path | None:
    """从已打开的受控文件描述符复制 OCR 输入，消除校验后的路径替换窗口。"""

    source_descriptor: int | None = None
    snapshot_path: Path | None = None
    try:
        if (
            not path.is_absolute()
            or path.is_symlink()
            or path.suffix.lower() != ".png"
            or content_type != "image/png"
        ):
            return None
        resolved = path.resolve(strict=True)
        if not _is_within_directory(resolved, work_path):
            return None
        source_descriptor = os.open(resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        source_stat = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(source_stat.st_mode)
            or source_stat.st_size <= 0
            or source_stat.st_size > max_image_bytes
        ):
            return None
        header = os.read(source_descriptor, 24)
        if (
            len(header) < 24
            or header[:8] != b"\x89PNG\r\n\x1a\n"
            or header[12:16] != b"IHDR"
        ):
            return None
        width, height = struct.unpack(">II", header[16:24])
        if width <= 0 or height <= 0 or width * height > max_image_pixels:
            return None
        os.lseek(source_descriptor, 0, os.SEEK_SET)
        snapshot_descriptor, snapshot_name = tempfile.mkstemp(
            prefix=".ocr-input-",
            suffix=".png",
            dir=resolved.parent,
        )
        snapshot_path = Path(snapshot_name)
        with os.fdopen(snapshot_descriptor, "wb") as output_stream:
            remaining_bytes = source_stat.st_size
            while remaining_bytes > 0:
                chunk = os.read(source_descriptor, min(1024 * 1024, remaining_bytes))
                if not chunk:
                    raise OSError("validated OCR source was truncated during snapshot")
                output_stream.write(chunk)
                remaining_bytes -= len(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        return snapshot_path
    except (OSError, RuntimeError, ValueError):
        if snapshot_path is not None:
            snapshot_path.unlink(missing_ok=True)
        return None
    finally:
        if source_descriptor is not None:
            os.close(source_descriptor)


def _processing_budget_warning(
    artifact: DocumentArtifact,
    pending: Iterable[int | str],
) -> DocumentWarning:
    locations = tuple(
        item if isinstance(item, str) else _format_location(artifact.format, item)
        for item in pending
    )
    return DocumentWarning(
        code="processing_budget_exceeded",
        message="文档增强处理达到总时间预算，已保留已完成结果和未处理页面标记。",
        locations=locations,
    )


def _sorted_unique_locations(locations: Iterable[str]) -> tuple[str, ...]:
    unique_locations = set(locations)

    def sort_key(location: str) -> tuple[bool, int, str]:
        page_number = _location_page_number(location)
        return page_number is None, page_number or 0, location

    return tuple(sorted(unique_locations, key=sort_key))


def _page_numbers_for_artifact(
    artifact: DocumentArtifact,
    *,
    requested_locations: tuple[str, ...] | None,
) -> tuple[int, ...]:
    if requested_locations:
        numbers = sorted(
            {
                _location_page_number(location)
                for location in requested_locations
                if _location_page_number(location) is not None
            }
        )
        return tuple(numbers)
    if artifact.page_count is None or artifact.page_count <= 0:
        return ()
    return tuple(range(1, artifact.page_count + 1))


def _format_location(document_format: DocumentFormat, page_number: int) -> str:
    prefix = "slide" if document_format == DocumentFormat.PPTX else "page"
    return f"{prefix}:{page_number}"


def _location_page_number(location: str) -> int | None:
    match = re.match(r"^(?:page|slide):(\d+)", location)
    if not match:
        return None
    return int(match.group(1))


def _read_image_size(path: Path) -> tuple[int | None, int | None]:
    if path.suffix.lower() != ".png":
        return None, None
    with path.open("rb") as stream:
        signature = stream.read(8)
        if signature != b"\x89PNG\r\n\x1a\n":
            return None, None
        chunk_length = stream.read(4)
        chunk_type = stream.read(4)
        if len(chunk_length) != 4 or chunk_type != b"IHDR":
            return None, None
        payload = stream.read(8)
        if len(payload) != 8:
            return None, None
        width, height = struct.unpack(">II", payload)
        return int(width), int(height)


def _merge_assets(existing: list[DocumentAsset], new_assets: list[DocumentAsset]) -> list[DocumentAsset]:
    merged = {asset.asset_id: asset for asset in existing}
    for asset in new_assets:
        merged[asset.asset_id] = asset
    return list(merged.values())


def _merge_warnings(existing: list[DocumentWarning], new_warnings: list[DocumentWarning]) -> list[DocumentWarning]:
    return _dedupe_warning_list([*existing, *new_warnings])


def _replace_warning(
    warnings: list[DocumentWarning],
    *,
    new_warning: DocumentWarning,
) -> list[DocumentWarning]:
    return _dedupe_warning_list([*warnings, new_warning])


def _dedupe_warning_list(warnings: list[DocumentWarning]) -> list[DocumentWarning]:
    deduped: dict[tuple[str, str, tuple[str, ...]], DocumentWarning] = {}
    for warning in warnings:
        key = (warning.code, warning.message, tuple(warning.locations))
        deduped[key] = warning
    return list(deduped.values())


def _block_sort_key(block: DocumentBlock) -> tuple[int, int, int, int, str]:
    page_number, subsection = _location_sort_parts(block.location)
    kind_order = {"header": 0, "title": 1, "page": 2, "text": 3, "table": 4, "ocr_page": 5}.get(block.kind, 9)
    return (page_number, subsection, kind_order, _secondary_location_value(block.location), block.block_id)


def _asset_sort_key(asset: DocumentAsset) -> tuple[int, int, str]:
    page_number, subsection = _location_sort_parts(asset.location)
    return (page_number, subsection, asset.asset_id)


def _location_sort_parts(location: str) -> tuple[int, int]:
    match = re.match(r"^(?:page|slide):(\d+)(?:/(?:shape|notes):?(\d+)?)?$", location)
    if not match:
        fallback = _location_page_number(location) or 999_999
        return fallback, 0
    page_number = int(match.group(1))
    secondary = int(match.group(2) or 0)
    return page_number, secondary


def _secondary_location_value(location: str) -> int:
    numbers = re.findall(r"(\d+)", location)
    if len(numbers) < 2:
        return 0
    return int(numbers[1])
