from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel
try:
    from pydantic_ai.settings import ModelSettings
except ImportError:  # pragma: no cover - exercised indirectly in test environments without the package
    ModelSettings = None

from skills.direct_report.schema import DirectReportResult


class PydanticAIWriter:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model_name: str,
        skill_dir: Path,
        agent_factory: Callable[[object, type[BaseModel], str, ModelSettings], Any] | None = None,
        model_max_tokens: int = 4096,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name
        self.skill_dir = skill_dir
        self.agent_factory = agent_factory
        self.model_max_tokens = model_max_tokens

    def write(self, payload: dict[str, object]) -> dict[str, object]:
        output_type = self._resolve_output_type(payload)
        instructions = self._build_instructions(payload, output_type)
        prompt = self._build_prompt(payload)
        agent = self._create_agent(instructions, output_type)
        result = agent.run_sync(prompt)
        output = result.output
        if not isinstance(output, output_type):
            output = output_type.model_validate(output)
        return output.model_dump()

    def _create_agent(self, instructions: str, output_type: type[BaseModel]) -> Any:
        model = self._model()
        if ModelSettings is None:
            raise RuntimeError("缺少 pydantic-ai，无法使用 Pydantic AI 写作器。")
        # DeepSeek 默认进入 thinking mode，而 thinking mode 不支持 tool_choice（结构化输出）。
        # 必须通过 extra_body 传 {"thinking": {"type": "disabled"}} 来关闭 thinking。
        if self._is_deepseek():
            model_settings = ModelSettings(
                max_tokens=self.model_max_tokens,
                extra_body={"thinking": {"type": "disabled"}},
            )
        else:
            model_settings = ModelSettings(max_tokens=self.model_max_tokens, thinking=False)
        if self.agent_factory:
            return self.agent_factory(model, output_type, instructions, model_settings)

        try:
            from pydantic_ai import Agent
        except ImportError as exc:
            raise RuntimeError("缺少 pydantic-ai，无法使用 Pydantic AI 写作器。") from exc
        return Agent(
            model,
            output_type=output_type,
            instructions=instructions,
            model_settings=model_settings,
        )

    def _resolve_output_type(self, payload: dict[str, object]) -> type[BaseModel]:
        output_type = payload.get("output_type")
        if isinstance(output_type, type) and issubclass(output_type, BaseModel):
            return output_type
        return DirectReportResult

    def _model(self) -> object:
        if self.agent_factory:
            return self.model_name
        if not self.api_key:
            raise RuntimeError("缺少 API Key（ANTHROPIC_API_KEY / DEEPSEEK_API_KEY），无法调用写作模型。")

        if self._is_deepseek():
            return self._deepseek_model()
        return self._anthropic_model()

    def _is_deepseek(self) -> bool:
        return "deepseek.com" in self.base_url.lower()

    def _deepseek_model(self) -> object:
        try:
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.openai import OpenAIProvider
        except ImportError as exc:
            raise RuntimeError("缺少 pydantic-ai，无法创建 DeepSeek 模型。") from exc

        provider = OpenAIProvider(
            base_url="https://api.deepseek.com/v1",
            api_key=self.api_key,
        )
        return OpenAIChatModel(self.model_name, provider=provider)

    def _anthropic_model(self) -> object:
        try:
            import anthropic
            from pydantic_ai.models.anthropic import AnthropicModel
            from pydantic_ai.providers.anthropic import AnthropicProvider
        except ImportError as exc:
            raise RuntimeError("缺少 pydantic-ai 或 anthropic，无法创建 Anthropic 兼容模型。") from exc

        client = anthropic.AsyncAnthropic(api_key=self.api_key, base_url=self.base_url)
        provider = AnthropicProvider(anthropic_client=client)
        return AnthropicModel(self.model_name, provider=provider)

    def _build_instructions(self, payload: dict[str, object], output_type: type[BaseModel]) -> str:
        skill_dir = self._resolve_skill_dir(payload)
        skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

        prompt_path = payload.get("prompt_path")
        if isinstance(prompt_path, str) and prompt_path:
            draft_prompt_path = skill_dir / prompt_path
        else:
            draft_prompt_path = skill_dir / "prompts" / "draft.md"

        draft_prompt = draft_prompt_path.read_text(encoding="utf-8") if draft_prompt_path.exists() else ""
        prefix = f"{draft_prompt}\n\n---\n\n" if draft_prompt else ""
        schema_hint = self._schema_hint(output_type)
        return f"""{prefix}## Skill 规则

{skill_text}

---

{schema_hint}
"""

    def _schema_hint(self, output_type: type[BaseModel]) -> str:
        if output_type is DirectReportResult:
            return """请严格返回结构化结果：
- title: 标题
- body: 正文
- sources: 来源链接列表
- needs_clarification: 是否需要追问
- message: 给用户的补充说明
"""
        return f"请严格按 {output_type.__name__} 的结构化字段返回结果，不要输出额外说明。"

    def _resolve_skill_dir(self, payload: dict[str, object]) -> Path:
        skill_id = str(payload.get("skill_id") or payload.get("task") or "").strip()
        if (self.skill_dir / "SKILL.md").exists():
            return self.skill_dir
        if skill_id and (self.skill_dir / skill_id / "SKILL.md").exists():
            return self.skill_dir / skill_id
        raise FileNotFoundError(f"找不到 skill 规则目录：{self.skill_dir} / {skill_id}")

    def _build_prompt(self, payload: dict[str, object]) -> str:
        materials = payload.get("materials", [])
        material_text = self._format_materials(materials if isinstance(materials, list) else [])
        planning_note = str(payload.get("planning_note", "") or "").strip()
        planning_block = f"## 写作规划\n\n{planning_note}\n\n---\n\n" if planning_note else ""
        return f"""## 用户要求

{payload.get("instruction", "")}

---

{planning_block}## 用户材料

{material_text}
"""

    def _format_materials(self, materials: list[object]) -> str:
        sections: list[str] = []
        for idx, item in enumerate(materials, 1):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "") or "")
            source = str(item.get("source", "") or "")
            if source == "previous_draft":
                text = _trim_material_text(text, max_chars=12000)
            elif source == "uploaded_file":
                text = _trim_material_text(text, max_chars=6000, balanced=True)
            else:
                text = _trim_material_text(text, max_chars=2000)
            sections.append(
                f"【材料{idx}】\n"
                f"标题：{item.get('title', '')}\n"
                f"来源：{item.get('url', '')}\n"
                f"材料类型：{source}\n"
                f"政策分类：{item.get('category', '')}\n"
                f"发布日期：{item.get('publish_date', '')}\n"
                f"正文：{text}"
            )
        return "\n\n".join(sections)


def _trim_material_text(text: str, *, max_chars: int, balanced: bool = False) -> str:
    if len(text) <= max_chars:
        return text
    if not balanced:
        return text[:max_chars] + "\n[后文已截断]"

    marker = "\n[长文档已均衡取样，完整解析结果保存在任务 work 目录]\n"
    budget = max(3, max_chars - len(marker) * 2)
    head_length = max(1, budget * 2 // 5)
    middle_length = max(1, budget // 5)
    tail_length = max(1, budget - head_length - middle_length)
    middle_start = max(0, (len(text) - middle_length) // 2)
    return (
        text[:head_length]
        + marker
        + text[middle_start : middle_start + middle_length]
        + marker
        + text[-tail_length:]
    )
