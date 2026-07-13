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
    content: bytes
    content_type: str = ""


@dataclass(frozen=True)
class PlatformResult:
    skill_id: str | None
    output: dict[str, object]
    needs_clarification: bool
    message: str
