from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SkillDefinition:
    id: str
    name: str
    description: str
    enabled: bool
    triggers: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    workflow: str
    directory: Path
    supports_revision: bool = False
    inputs: tuple[str, ...] = field(default_factory=tuple)
    outputs: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RoutedRequest:
    skill_id: str | None
    confidence: float
    needs_clarification: bool
    message: str
    inputs: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class UploadedFile:
    filename: str
    content: bytes = b""
    content_type: str = ""
    stored_path: str = ""
    delete_after_read: bool = False

    @property
    def size_bytes(self) -> int:
        if self.content:
            return len(self.content)
        if self.stored_path:
            try:
                return Path(self.stored_path).stat().st_size
            except OSError:
                return 0
        return 0

    def read_bytes(self) -> bytes:
        if self.content:
            return self.content
        if self.stored_path:
            return Path(self.stored_path).read_bytes()
        return b""


@dataclass(frozen=True)
class PlatformResult:
    skill_id: str | None
    output: dict[str, object]
    needs_clarification: bool
    message: str
