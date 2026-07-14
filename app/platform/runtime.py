from collections.abc import Callable
from importlib import import_module
from pathlib import Path

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
        output = {
            "title": result.title,
            "body": result.body,
            "sources": result.sources,
        }
        revision_note = getattr(result, "revision_note", "")
        if isinstance(revision_note, str) and revision_note.strip():
            output["revision_note"] = revision_note.strip()
        output_file = getattr(result, "output_file", "")
        if isinstance(output_file, str) and output_file.strip():
            output["output_file"] = _validated_output_file(
                output_file,
                output_dir=str(route.inputs.get("output_dir", "") or ""),
            )
        return PlatformResult(
            skill_id=skill.id,
            output=output,
            needs_clarification=result.needs_clarification,
            message=result.message,
        )


def _validated_output_file(output_file: str, *, output_dir: str) -> str:
    if not output_dir.strip():
        raise ValueError("生成文件缺少当前任务 output 目录上下文")
    candidate = Path(output_file).resolve()
    expected_dir = Path(output_dir).resolve()
    if candidate.parent != expected_dir:
        raise ValueError("生成文件必须位于当前任务 output 目录")
    return str(candidate)
