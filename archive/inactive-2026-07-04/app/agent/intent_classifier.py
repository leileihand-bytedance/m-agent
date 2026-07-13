"""意图分类器 - AI判断用户消息意图"""

from pathlib import Path
from dataclasses import dataclass
from enum import Enum

import anthropic


class Intent(Enum):
    RAW_MATERIAL = "raw_material"      # 原材料
    CONCLUSION = "conclusion"           # 结论
    COMMAND = "command"                # 指令
    QUESTION = "question"               # 询问
    UNKNOWN = "unknown"                # 未知


@dataclass
class IntentResult:
    intent: Intent
    leader: str | None
    content: str | None
    confidence: float = 1.0
    reason: str = ""


COMMAND_KEYWORDS = {"开始提炼", "确认全部", "确认 1", "确认2", "确认3", "确认4", "确认5", "不入库", "取消", "重新提炼"}
QUESTION_KEYWORDS = {"什么是", "怎么", "如何", "为什么", "?"}


class IntentClassifier:
    """意图分类器"""

    def __init__(self, api_key: str, base_url: str, model: str = "MiniMax-M2.7"):
        self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url)
        self.model = model
        self.prompt_path = Path(__file__).parent.parent / "prompts" / "intent_classify.md"
        self.prompt_template = self.prompt_path.read_text(encoding="utf-8")

    def classify(self, message: str, leader_mapping: dict[str, str]) -> IntentResult:
        """分类消息意图"""
        # 先检查是否是指令
        for keyword in COMMAND_KEYWORDS:
            if keyword in message:
                return IntentResult(
                    intent=Intent.COMMAND,
                    leader=self._extract_leader(message, leader_mapping),
                    content=message,
                    reason=f"包含指令关键词: {keyword}",
                )

        # 检查是否是询问
        for keyword in QUESTION_KEYWORDS:
            if keyword in message:
                return IntentResult(
                    intent=Intent.QUESTION,
                    leader=self._extract_leader(message, leader_mapping),
                    content=message,
                    reason=f"包含询问关键词: {keyword}",
                )

        # 调用AI分类
        return self._ai_classify(message, leader_mapping)

    def _extract_leader(self, message: str, mapping: dict[str, list[str]]) -> str | None:
        """从消息中提取领导标识

        Returns:
            领导编号（如"01"），或 None
        """
        # 检查编号（如"01"）
        for key in mapping.keys():
            if key in message:
                return key

        # 检查名称（如"老李"、"NQ"）
        for key, names in mapping.items():
            for name in names:
                if name in message:
                    return key

        return None

    def _ai_classify(self, message: str, leader_mapping: dict[str, str]) -> IntentResult:
        """使用AI分类消息"""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=50,
                messages=[{"role": "user", "content": self.prompt_template + f"\n\n消息：{message}"}],
            )

            intent_str = response.content[0].text.strip().lower()

            if "raw_material" in intent_str:
                intent = Intent.RAW_MATERIAL
            elif "conclusion" in intent_str:
                intent = Intent.CONCLUSION
            elif "command" in intent_str:
                intent = Intent.COMMAND
            elif "question" in intent_str:
                intent = Intent.QUESTION
            else:
                intent = Intent.UNKNOWN

            leader = self._extract_leader(message, leader_mapping)

            return IntentResult(
                intent=intent,
                leader=leader,
                content=message,
                reason="AI分类",
            )
        except Exception as e:
            return self._fallback_classify(message, leader_mapping, str(e))

    def _fallback_classify(self, message: str, mapping: dict[str, str], error: str) -> IntentResult:
        """AI调用失败时的后备分类"""
        conclusion_indicators = ["要", "不要", "应该", "不应该", "偏好", "必须", "不得"]

        for indicator in conclusion_indicators:
            if indicator in message:
                leader = self._extract_leader(message, mapping)
                return IntentResult(
                    intent=Intent.CONCLUSION,
                    leader=leader,
                    content=message,
                    confidence=0.7,
                    reason=f"启发式: 包含'{indicator}'",
                )

        return IntentResult(
            intent=Intent.RAW_MATERIAL,
            leader=self._extract_leader(message, mapping),
            content=message,
            confidence=0.5,
            reason=f"后备分类: AI失败 ({error})",
        )