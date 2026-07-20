from app.platform.tools import ToolGateway
from skills.writer1.schema import BriefResult
from skills.writer1.workflow import run as run_brief


def run(inputs: dict[str, object], tools: ToolGateway) -> BriefResult:
    """Preserve the legacy writer2 ID while using the canonical brief workflow."""
    canonical_inputs = dict(inputs)
    if canonical_inputs.get("revision"):
        canonical_inputs["_brief_mode"] = "multi"
    return run_brief(canonical_inputs, tools)
