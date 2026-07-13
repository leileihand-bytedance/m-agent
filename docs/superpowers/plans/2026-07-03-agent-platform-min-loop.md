# Agent Platform Minimum Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first safe M-Agent platform skeleton: load file-based skills, route a natural-language request to `direct_report`, enforce allowed tools, and run a deterministic direct-report workflow in tests.

**Architecture:** Keep existing `app/review/` and `app/writing/` untouched. Add a new `app/platform/` base layer and a new `skills/direct_report/` capability directory. The first runtime is deterministic and testable; real WeCom, Pydantic AI, search, web reading, and model calls are added in later tasks.

**Tech Stack:** Python 3, standard library dataclasses/pathlib, PyYAML for `config.yaml`, existing test style using plain Python test files runnable by pytest or direct Python.

---

## File Structure

- Create `app/platform/__init__.py`: public platform package marker.
- Create `app/platform/models.py`: request, route, skill, and workflow result data models.
- Create `app/platform/registry.py`: load `skills/*/config.yaml` into a skill registry.
- Create `app/platform/router.py`: route natural-language user messages to registered skills, with unknown fallback.
- Create `app/platform/tools.py`: tool gateway that only allows tools declared by a skill.
- Create `app/platform/runtime.py`: execute a routed request through the registered skill workflow.
- Create `skills/direct_report/SKILL.md`: first normalized direct-report skill document, migrated from the earlier single-skill draft.
- Create `skills/direct_report/config.yaml`: direct-report metadata, triggers, tools, and workflow entrypoint.
- Create `skills/direct_report/__init__.py`: package marker.
- Create `skills/direct_report/schema.py`: direct-report input/output data models.
- Create `skills/direct_report/workflow.py`: deterministic first workflow using injected tools and writer.
- Create `skills/direct_report/prompts/draft.md`: initial prompt placeholder with actual direct-report rules.
- Create `tests/test_platform_registry.py`: registry tests.
- Create `tests/test_platform_router.py`: router tests.
- Create `tests/test_platform_tools.py`: tool gateway tests.
- Create `tests/test_direct_report_workflow.py`: direct-report workflow tests.
- Modify `app/requirements.txt`: add `PyYAML` explicitly because registry reads YAML.

## Task 1: Skill Registry

**Files:**
- Create: `app/platform/__init__.py`
- Create: `app/platform/models.py`
- Create: `app/platform/registry.py`
- Create: `skills/direct_report/config.yaml`
- Create: `tests/test_platform_registry.py`
- Modify: `app/requirements.txt`

- [ ] **Step 1: Write the failing registry test**

Create `tests/test_platform_registry.py`:

```python
from pathlib import Path

from app.platform.registry import SkillRegistry


def test_registry_loads_enabled_direct_report_skill():
    registry = SkillRegistry.from_directory(Path("skills"))

    skill = registry.get("direct_report")

    assert skill.id == "direct_report"
    assert skill.name == "直报写作"
    assert skill.enabled is True
    assert "web_reader" in skill.allowed_tools
    assert "llm_writer" in skill.allowed_tools
    assert skill.workflow == "skills.direct_report.workflow:run"


def test_registry_lists_only_enabled_skills(tmp_path):
    skills_dir = tmp_path / "skills"
    enabled = skills_dir / "enabled_skill"
    disabled = skills_dir / "disabled_skill"
    enabled.mkdir(parents=True)
    disabled.mkdir(parents=True)
    (enabled / "config.yaml").write_text(
        "id: enabled_skill\n"
        "name: Enabled\n"
        "enabled: true\n"
        "description: Enabled skill\n"
        "triggers:\n"
        "  - enabled\n"
        "allowed_tools:\n"
        "  - web_reader\n"
        "workflow: enabled.workflow:run\n",
        encoding="utf-8",
    )
    (disabled / "config.yaml").write_text(
        "id: disabled_skill\n"
        "name: Disabled\n"
        "enabled: false\n"
        "description: Disabled skill\n"
        "triggers:\n"
        "  - disabled\n"
        "allowed_tools:\n"
        "  - web_reader\n"
        "workflow: disabled.workflow:run\n",
        encoding="utf-8",
    )

    registry = SkillRegistry.from_directory(skills_dir)

    assert [skill.id for skill in registry.list_enabled()] == ["enabled_skill"]
```

- [ ] **Step 2: Run the registry test and verify it fails**

Run: `pytest tests/test_platform_registry.py -v`

Expected: FAIL because `app.platform.registry` does not exist yet.

- [ ] **Step 3: Implement registry models and loader**

Create `app/platform/__init__.py`:

```python
"""M-Agent platform base layer."""
```

Create `app/platform/models.py`:

```python
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
```

Create `app/platform/registry.py`:

```python
from pathlib import Path

import yaml

from app.platform.models import SkillDefinition


class SkillRegistry:
    def __init__(self, skills: list[SkillDefinition]):
        self._skills = {skill.id: skill for skill in skills}

    @classmethod
    def from_directory(cls, skills_dir: Path) -> "SkillRegistry":
        skills: list[SkillDefinition] = []
        if not skills_dir.exists():
            return cls([])

        for config_path in sorted(skills_dir.glob("*/config.yaml")):
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            skill = SkillDefinition(
                id=str(raw["id"]),
                name=str(raw["name"]),
                description=str(raw.get("description", "")),
                enabled=bool(raw.get("enabled", False)),
                triggers=tuple(str(item) for item in raw.get("triggers", [])),
                allowed_tools=tuple(str(item) for item in raw.get("allowed_tools", [])),
                workflow=str(raw["workflow"]),
                directory=config_path.parent,
                supports_revision=bool(raw.get("supports_revision", False)),
                inputs=tuple(str(item) for item in raw.get("inputs", [])),
                outputs=tuple(str(item) for item in raw.get("outputs", [])),
            )
            skills.append(skill)
        return cls(skills)

    def get(self, skill_id: str) -> SkillDefinition:
        return self._skills[skill_id]

    def list_enabled(self) -> list[SkillDefinition]:
        return [skill for skill in self._skills.values() if skill.enabled]
```

Create `skills/direct_report/config.yaml`:

```yaml
id: direct_report
name: 直报写作
description: 根据网页链接或用户材料生成信息直报初稿。
enabled: true
triggers:
  - 直报
  - 报送材料
  - 国办
  - 报送
allowed_tools:
  - web_reader
  - search
  - word_reader
  - pdf_reader
  - llm_writer
workflow: skills.direct_report.workflow:run
inputs:
  - url
  - file
  - user_instruction
outputs:
  - title
  - body
  - sources
supports_revision: true
```

Add to `app/requirements.txt`:

```text
PyYAML>=6.0
```

- [ ] **Step 4: Run registry test and verify it passes**

Run: `pytest tests/test_platform_registry.py -v`

Expected: PASS.

## Task 2: Intent Router

**Files:**
- Create: `app/platform/router.py`
- Modify: `app/platform/models.py`
- Create: `tests/test_platform_router.py`

- [ ] **Step 1: Write the failing router test**

Create `tests/test_platform_router.py`:

```python
from pathlib import Path

from app.platform.registry import SkillRegistry
from app.platform.router import route_message


def test_router_matches_direct_report_from_natural_language():
    registry = SkillRegistry.from_directory(Path("skills"))

    route = route_message("帮我根据这个链接写一篇报送材料：https://example.com/a", registry)

    assert route.skill_id == "direct_report"
    assert route.needs_clarification is False
    assert route.inputs["urls"] == ["https://example.com/a"]


def test_router_asks_when_intent_is_unknown():
    registry = SkillRegistry.from_directory(Path("skills"))

    route = route_message("帮我处理一下这个东西", registry)

    assert route.skill_id is None
    assert route.needs_clarification is True
    assert "写直报" in route.message
```

- [ ] **Step 2: Run router test and verify it fails**

Run: `pytest tests/test_platform_router.py -v`

Expected: FAIL because `app.platform.router` does not exist yet.

- [ ] **Step 3: Implement route model and router**

Append to `app/platform/models.py`:

```python

@dataclass(frozen=True)
class RoutedRequest:
    skill_id: str | None
    confidence: float
    needs_clarification: bool
    message: str
    inputs: dict[str, object] = field(default_factory=dict)
```

Create `app/platform/router.py`:

```python
import re

from app.platform.models import RoutedRequest
from app.platform.registry import SkillRegistry


URL_RE = re.compile(r"https?://[^\s，。；;）)]+")


def route_message(message: str, registry: SkillRegistry) -> RoutedRequest:
    normalized = message.strip()
    urls = URL_RE.findall(normalized)

    for skill in registry.list_enabled():
        if any(trigger in normalized for trigger in skill.triggers):
            return RoutedRequest(
                skill_id=skill.id,
                confidence=0.85,
                needs_clarification=False,
                message="已识别为直报写作。",
                inputs={"text": normalized, "urls": urls},
            )

    return RoutedRequest(
        skill_id=None,
        confidence=0.0,
        needs_clarification=True,
        message="我还不确定你要做什么。你是想写直报、写简报，还是审核文档？",
        inputs={"text": normalized, "urls": urls},
    )
```

- [ ] **Step 4: Run router test and verify it passes**

Run: `pytest tests/test_platform_router.py -v`

Expected: PASS.

## Task 3: Tool Gateway

**Files:**
- Create: `app/platform/tools.py`
- Create: `tests/test_platform_tools.py`

- [ ] **Step 1: Write the failing tool gateway test**

Create `tests/test_platform_tools.py`:

```python
from app.platform.tools import ToolGateway, ToolNotAllowedError


def test_tool_gateway_allows_declared_tool():
    gateway = ToolGateway(
        allowed_tools=("web_reader",),
        tools={"web_reader": lambda url: {"title": "标题", "text": "正文", "url": url}},
    )

    result = gateway.call("web_reader", "https://example.com/a")

    assert result["title"] == "标题"
    assert result["url"] == "https://example.com/a"


def test_tool_gateway_blocks_undeclared_tool():
    gateway = ToolGateway(
        allowed_tools=("web_reader",),
        tools={"shell": lambda command: command},
    )

    try:
        gateway.call("shell", "ls")
    except ToolNotAllowedError as exc:
        assert "shell" in str(exc)
    else:
        raise AssertionError("ToolNotAllowedError was not raised")
```

- [ ] **Step 2: Run tool test and verify it fails**

Run: `pytest tests/test_platform_tools.py -v`

Expected: FAIL because `app.platform.tools` does not exist yet.

- [ ] **Step 3: Implement tool gateway**

Create `app/platform/tools.py`:

```python
from collections.abc import Callable


class ToolNotAllowedError(RuntimeError):
    pass


class ToolGateway:
    def __init__(self, allowed_tools: tuple[str, ...], tools: dict[str, Callable[..., object]]):
        self._allowed_tools = set(allowed_tools)
        self._tools = tools

    def call(self, tool_name: str, *args: object, **kwargs: object) -> object:
        if tool_name not in self._allowed_tools:
            raise ToolNotAllowedError(f"Tool is not allowed for this skill: {tool_name}")
        if tool_name not in self._tools:
            raise KeyError(f"Tool is not registered: {tool_name}")
        return self._tools[tool_name](*args, **kwargs)
```

- [ ] **Step 4: Run tool test and verify it passes**

Run: `pytest tests/test_platform_tools.py -v`

Expected: PASS.

## Task 4: Direct Report Skill Workflow

**Files:**
- Create: `skills/direct_report/__init__.py`
- Create: `skills/direct_report/schema.py`
- Create: `skills/direct_report/workflow.py`
- Create: `skills/direct_report/SKILL.md`
- Create: `skills/direct_report/prompts/draft.md`
- Create: `tests/test_direct_report_workflow.py`

- [ ] **Step 1: Write the failing workflow test**

Create `tests/test_direct_report_workflow.py`:

```python
from app.platform.tools import ToolGateway
from skills.direct_report.workflow import run


def test_direct_report_workflow_reads_url_and_returns_draft():
    gateway = ToolGateway(
        allowed_tools=("web_reader", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "微众银行服务小微企业",
                "text": "微众银行通过数字化方式提升小微企业金融服务可得性。",
                "url": url,
            },
            "llm_writer": lambda payload: {
                "title": "微众银行提升小微企业金融服务可得性",
                "body": "微众银行围绕小微企业融资需求，持续完善数字化服务能力。",
            },
        },
    )

    result = run(
        inputs={
            "text": "根据这个链接写直报：https://example.com/news",
            "urls": ["https://example.com/news"],
        },
        tools=gateway,
    )

    assert result.title == "微众银行提升小微企业金融服务可得性"
    assert "小微企业" in result.body
    assert result.sources == ["https://example.com/news"]
    assert result.needs_clarification is False


def test_direct_report_workflow_asks_when_no_material_is_available():
    gateway = ToolGateway(allowed_tools=("web_reader", "llm_writer"), tools={})

    result = run(inputs={"text": "帮我写直报", "urls": []}, tools=gateway)

    assert result.needs_clarification is True
    assert "链接" in result.message
```

- [ ] **Step 2: Run workflow test and verify it fails**

Run: `pytest tests/test_direct_report_workflow.py -v`

Expected: FAIL because `skills.direct_report.workflow` does not exist yet.

- [ ] **Step 3: Implement direct-report schema and workflow**

Create `skills/direct_report/__init__.py`:

```python
"""Direct report writing skill."""
```

Create `skills/direct_report/schema.py`:

```python
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DirectReportResult:
    title: str
    body: str
    sources: list[str] = field(default_factory=list)
    needs_clarification: bool = False
    message: str = ""
```

Create `skills/direct_report/workflow.py`:

```python
from app.platform.tools import ToolGateway
from skills.direct_report.schema import DirectReportResult


def run(inputs: dict[str, object], tools: ToolGateway) -> DirectReportResult:
    urls = list(inputs.get("urls") or [])
    if not urls:
        return DirectReportResult(
            title="",
            body="",
            needs_clarification=True,
            message="请提供网页链接、Word 文件或 PDF 文件，我再为你写直报。",
        )

    materials = [tools.call("web_reader", url) for url in urls]
    draft = tools.call(
        "llm_writer",
        {
            "task": "direct_report",
            "instruction": inputs.get("text", ""),
            "materials": materials,
        },
    )

    return DirectReportResult(
        title=str(draft.get("title", "")),
        body=str(draft.get("body", "")),
        sources=[str(item.get("url", "")) for item in materials if item.get("url")],
        needs_clarification=False,
        message="已生成直报初稿。",
    )
```

Create `skills/direct_report/SKILL.md` with the existing direct-report rule as normalized documentation.

Create `skills/direct_report/prompts/draft.md` with the first direct-report drafting prompt.

- [ ] **Step 4: Run workflow test and verify it passes**

Run: `pytest tests/test_direct_report_workflow.py -v`

Expected: PASS.

## Task 5: Platform Runtime

**Files:**
- Create: `app/platform/runtime.py`
- Modify: `app/platform/models.py`
- Create: `tests/test_platform_runtime.py`

- [ ] **Step 1: Write the failing runtime test**

Create `tests/test_platform_runtime.py`:

```python
from pathlib import Path

from app.platform.registry import SkillRegistry
from app.platform.router import route_message
from app.platform.runtime import PlatformRuntime


def test_runtime_executes_routed_direct_report_skill():
    registry = SkillRegistry.from_directory(Path("skills"))
    route = route_message("请根据这个链接写直报：https://example.com/news", registry)
    runtime = PlatformRuntime(
        registry=registry,
        tools={
            "web_reader": lambda url: {
                "title": "微众银行服务小微企业",
                "text": "微众银行通过数字化方式提升小微企业金融服务可得性。",
                "url": url,
            },
            "llm_writer": lambda payload: {
                "title": "微众银行提升小微企业金融服务可得性",
                "body": "微众银行围绕小微企业融资需求，持续完善数字化服务能力。",
            },
        },
    )

    result = runtime.run(route)

    assert result.skill_id == "direct_report"
    assert result.output["title"] == "微众银行提升小微企业金融服务可得性"
    assert result.output["sources"] == ["https://example.com/news"]


def test_runtime_returns_clarification_for_unknown_route():
    registry = SkillRegistry.from_directory(Path("skills"))
    route = route_message("帮我处理一下", registry)
    runtime = PlatformRuntime(registry=registry, tools={})

    result = runtime.run(route)

    assert result.skill_id is None
    assert result.needs_clarification is True
    assert "写直报" in result.message
```

- [ ] **Step 2: Run runtime test and verify it fails**

Run: `pytest tests/test_platform_runtime.py -v`

Expected: FAIL because `app.platform.runtime` does not exist yet.

- [ ] **Step 3: Implement platform runtime**

Append to `app/platform/models.py`:

```python

@dataclass(frozen=True)
class PlatformResult:
    skill_id: str | None
    output: dict[str, object]
    needs_clarification: bool
    message: str
```

Create `app/platform/runtime.py`:

```python
from collections.abc import Callable
from importlib import import_module

from app.platform.models import PlatformResult, RoutedRequest
from app.platform.registry import SkillRegistry
from app.platform.tools import ToolGateway


class PlatformRuntime:
    def __init__(self, registry: SkillRegistry, tools: dict[str, Callable[..., object]]):
        self._registry = registry
        self._tools = tools

    def run(self, route: RoutedRequest) -> PlatformResult:
        if route.needs_clarification or route.skill_id is None:
            return PlatformResult(
                skill_id=None,
                output={},
                needs_clarification=True,
                message=route.message,
            )

        skill = self._registry.get(route.skill_id)
        module_name, function_name = skill.workflow.split(":", 1)
        workflow = getattr(import_module(module_name), function_name)
        gateway = ToolGateway(allowed_tools=skill.allowed_tools, tools=self._tools)
        result = workflow(inputs=route.inputs, tools=gateway)
        return PlatformResult(
            skill_id=skill.id,
            output={
                "title": result.title,
                "body": result.body,
                "sources": result.sources,
            },
            needs_clarification=result.needs_clarification,
            message=result.message,
        )
```

- [ ] **Step 4: Run runtime test and verify it passes**

Run: `pytest tests/test_platform_runtime.py -v`

Expected: PASS.

## Task 6: Verification

**Files:**
- Read: all files created in Tasks 1-5.

- [ ] **Step 1: Run focused platform tests**

Run:

```bash
pytest tests/test_platform_registry.py tests/test_platform_router.py tests/test_platform_tools.py tests/test_direct_report_workflow.py tests/test_platform_runtime.py -v
```

Expected: all focused platform tests PASS.

- [ ] **Step 2: Run existing review tests**

Run:

```bash
python tests/test_reviewer.py
python tests/test_review_bot.py
```

Expected: existing review tests still PASS or report any pre-existing environment issue without changing review code.

- [ ] **Step 3: Inspect git diff**

Run: `git status --short`

Expected: only new platform/skill/docs files plus the already-existing user changes. No changes to `app/review/` or `app/writing/` from this implementation.

## Self-Review

- Spec coverage: The plan covers底座骨架、功能区第一个 skill、工具授权、自然语言路由和不影响旧功能的迁移原则。
- Placeholder scan: No TODO/TBD placeholders are used as implementation steps.
- Type consistency: `SkillDefinition`, `RoutedRequest`, `PlatformResult`, `ToolGateway`, and `DirectReportResult` are introduced before use.
