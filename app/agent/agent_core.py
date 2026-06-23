"""智能体核心 - 协调各模块工作"""

from pathlib import Path
from dataclasses import dataclass

from .intent_classifier import IntentClassifier, Intent, IntentResult
from .context_manager import ContextManager, SessionState
from .profile_manager import ProfileManager
from config import AppConfig, load_leader_mapping


@dataclass
class AgentResponse:
    message: str
    action: str
    success: bool
    leader: str | None = None


class AgentCore:
    """智能体核心"""

    def __init__(self, config: AppConfig):
        self.config = config
        self.leader_mapping = load_leader_mapping()

        self.intent_classifier = IntentClassifier(
            api_key=config.anthropic_api_key,
            base_url=config.anthropic_base_url,
            model=config.model_name,
        )

        self.context_manager = ContextManager()
        self.profile_manager = ProfileManager(config.data_dir)

    def process(self, user_message: str, sender: str) -> AgentResponse:
        """处理用户消息"""
        intent_result = self.intent_classifier.classify(user_message, self.leader_mapping)

        self.context_manager.add_interaction(
            user_message=user_message,
            ai_response="",
            intent=intent_result.intent.value,
            leader=intent_result.leader,
        )

        if intent_result.intent == Intent.RAW_MATERIAL:
            return self._handle_raw_material(intent_result)
        elif intent_result.intent == Intent.CONCLUSION:
            return self._handle_conclusion(intent_result)
        elif intent_result.intent == Intent.COMMAND:
            return self._handle_command(intent_result)
        elif intent_result.intent == Intent.QUESTION:
            return AgentResponse(
                message="我可以帮你提炼领导风格。直接发送材料或文件即可。",
                action="answer_question",
                success=True,
            )
        else:
            return AgentResponse(
                message="我没有理解你的意思。请发送材料、文件或使用指令。",
                action="ask_clarify",
                success=False,
            )

    def _handle_raw_material(self, intent_result: IntentResult) -> AgentResponse:
        leader = intent_result.leader
        if not leader:
            leader = self.context_manager.get_recent_leader()
        if not leader:
            self.context_manager.set_state(SessionState.AWAITING_LEADER)
            self.context_manager.set_pending_content(intent_result.content)
            return AgentResponse(
                message="这是谁的风格？请回复（如：01）",
                action="ask_leader",
                success=True,
                leader=None,
            )

        leader_dir = self.profile_manager.ensure_leader_dir(leader)
        source_dir = leader_dir / "source"
        source_dir.mkdir(exist_ok=True)

        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        material_path = source_dir / f"{timestamp}-material.md"
        material_path.write_text(intent_result.content, encoding="utf-8")

        self.context_manager.set_leader(leader)
        self.context_manager.set_state(SessionState.COLLECTING)
        self.context_manager.set_pending_content(intent_result.content)

        return AgentResponse(
            message=f'已收到"{leader}"的材料。\n要现在提炼吗？发送"开始提炼"即可。',
            action="store_material",
            success=True,
            leader=leader,
        )

    def _handle_conclusion(self, intent_result: IntentResult) -> AgentResponse:
        leader = intent_result.leader
        if not leader:
            leader = self.context_manager.get_recent_leader()
        if not leader:
            self.context_manager.set_state(SessionState.AWAITING_LEADER)
            self.context_manager.set_pending_content(intent_result.content)
            return AgentResponse(
                message="这是谁的风格？请回复（如：01）",
                action="ask_leader",
                success=True,
                leader=None,
            )

        success = self.profile_manager.write_to_profile(
            leader=leader,
            content=intent_result.content,
            source="用户直接提供",
        )

        if success:
            self.context_manager.set_leader(leader)
            return AgentResponse(
                message=f'已写入"{leader}"档案：\n- {intent_result.content}',
                action="write_profile",
                success=True,
                leader=leader,
            )
        else:
            return AgentResponse(
                message="写入失败，请稍后重试。",
                action="write_profile",
                success=False,
                leader=leader,
            )

    def _handle_command(self, intent_result: IntentResult) -> AgentResponse:
        message = intent_result.content.lower()
        leader = intent_result.leader or self.context_manager.get_leader()

        if not leader:
            return AgentResponse(
                message="请先指定领导，例如：01",
                action="ask_leader",
                success=False,
                leader=None,
            )

        if "开始提炼" in message or "提炼" in message:
            return self._handle_start_extraction(leader)
        elif "确认" in message:
            return self._handle_confirmation(intent_result.content, leader)
        elif "不入库" in message:
            return self._handle_rejection(leader)
        elif "取消" in message:
            self.context_manager.clear()
            return AgentResponse(message="已取消当前操作。", action="cancel", success=True, leader=leader)
        else:
            return AgentResponse(
                message="我理解这是一个指令，但不知道要做什么。请使用：开始提炼、确认、不入库。",
                action="unknown_command",
                success=False,
                leader=leader,
            )

    def _handle_start_extraction(self, leader: str) -> AgentResponse:
        leader_dir = self.profile_manager.get_leader_dir(leader)
        materials = list(leader_dir.glob("source/*.md"))

        if not materials:
            return AgentResponse(message="还没有材料，请先发送材料。", action="check_materials", success=False, leader=leader)

        profile_path = leader_dir / "profile.md"
        existing_profile = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
        preferences = self.profile_manager.load_preferences(leader)

        prompt = self._build_extraction_prompt(leader, materials, existing_profile, preferences)

        try:
            from main import call_model, extract_style_suggestion
            ai_output = call_model(self.config, prompt)

            suggestion_path = self.profile_manager.save_suggestion(leader, ai_output)
            self.context_manager.set_pending_suggestion(str(suggestion_path))
            self.context_manager.set_state(SessionState.WAITING_CONFIRM)

            _, _, suggestions, _, _, _, _ = extract_style_suggestion(ai_output)

            summary = f'为"{leader}"提炼了 {len(suggestions)} 条建议：\n\n'
            for i, s in enumerate(suggestions, 1):
                summary += f"{i}. {s}\n"
            summary += "\n请回复：\n- 确认全部\n- 确认 1、3\n- 不入库"

            return AgentResponse(message=summary, action="extraction_complete", success=True, leader=leader)
        except Exception as e:
            return AgentResponse(message=f"提炼失败：{e}", action="extraction_failed", success=False, leader=leader)

    def _handle_confirmation(self, content: str, leader: str) -> AgentResponse:
        suggestion_path = self.context_manager.get_pending_suggestion()
        if not suggestion_path or not Path(suggestion_path).exists():
            return AgentResponse(
                message="没有正在等待确认的建议，请先发送\"开始提炼\"。",
                action="check_pending",
                success=False,
                leader=leader,
            )

        suggestion_content = Path(suggestion_path).read_text(encoding="utf-8")
        _, _, suggestions, _, _, _, _ = extract_style_suggestion(suggestion_content)

        if "确认全部" in content:
            confirmed = suggestions
        else:
            import re
            numbers = re.findall(r"确认\s*([0-9、，,]+)", content)
            confirmed = []
            if numbers:
                for part in numbers[0].split("、"):
                    part = part.strip("，,")
                    if part.isdigit():
                        idx = int(part) - 1
                        if 0 <= idx < len(suggestions):
                            confirmed.append(suggestions[idx])

        for item in confirmed:
            self.profile_manager.write_to_profile(leader, item, Path(suggestion_path).name)

        self.context_manager.set_state(SessionState.IDLE)
        self.context_manager.set_pending_suggestion(None)

        return AgentResponse(
            message=f'已更新"{leader}"档案，{len(confirmed)} 条建议已写入。',
            action="confirm_write",
            success=True,
            leader=leader,
        )

    def _handle_rejection(self, leader: str) -> AgentResponse:
        suggestion_path = self.context_manager.get_pending_suggestion()
        if suggestion_path and Path(suggestion_path).exists():
            suggestion_content = Path(suggestion_path).read_text(encoding="utf-8")
            self.profile_manager.record_rejection(leader, suggestion_content)

        self.context_manager.set_state(SessionState.IDLE)
        self.context_manager.set_pending_suggestion(None)

        return AgentResponse(message="已取消本次沉淀，材料已保存。", action="reject", success=True, leader=leader)

    def _build_extraction_prompt(self, leader: str, materials: list[Path], existing_profile: str, preferences: dict) -> str:
        prompt_template = (Path(__file__).parent.parent / "prompts" / "style_extraction.md").read_text(encoding="utf-8")

        material_contents = []
        for path in materials:
            content = path.read_text(encoding="utf-8")
            material_contents.append(f"## {path.name}\n\n{content}")

        avoid_instruction = ""
        if preferences.get("rejected_suggestions"):
            avoid_instruction = "\n\n## 避免以下已被用户拒绝的建议：\n"
            for item in preferences["rejected_suggestions"][-5:]:
                avoid_instruction += f"- {item['content']}\n"

        return prompt_template.format(
            leader_name=leader,
            material_sources="\n\n".join(material_contents),
            existing_profile=existing_profile or "（暂无已确认的档案）",
        ) + avoid_instruction