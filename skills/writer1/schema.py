from typing import Literal

from pydantic import BaseModel, Field


BriefType = Literal[
    "综合成果型",
    "机制成果型",
    "产品工具型",
    "平台合作型",
    "标准引领型",
    "能力建设型",
    "外部认可型",
    "活动亮相型",
    "专项治理型",
]


class BriefResult(BaseModel):
    title: str
    body: str
    sources: list[str] = Field(default_factory=list)
    output_file: str = ""
    needs_clarification: bool = False
    message: str = ""


class BriefViolation(BaseModel):
    rule: str
    severity: str = "hard"
    message: str
    suggestion: str = ""


class BriefCriticResult(BaseModel):
    violations: list[BriefViolation] = Field(default_factory=list)
    needs_clarification: bool = False
    message: str = ""


class BriefPlanResult(BaseModel):
    brief_type: BriefType
    core_message: str
    audience_value: str
    section_plan: list[str] = Field(min_length=1, max_length=3)
    selected_fact_ids: list[str] = Field(default_factory=list)
    selected_data_ids: list[str] = Field(default_factory=list)
    excluded_details: list[str] = Field(default_factory=list)


class BriefRevisionPlanResult(BaseModel):
    scope: Literal["title", "paragraph", "whole"] = "whole"
    target_paragraphs: list[int] = Field(default_factory=list)
    preserve_title: bool = False
    preserve_other_paragraphs: bool = False
    target_length: int | None = Field(default=None, ge=100, le=1200)
    preserve_facts_and_numbers: bool = False
    required_changes: list[str] = Field(default_factory=list)
    must_remove: list[str] = Field(default_factory=list)
