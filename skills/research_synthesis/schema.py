import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator


OutlineType = Literal["questionnaire", "policy_catalog", "report_skeleton", "unknown"]
CoverageMode = Literal["exhaustive", "selective"]
EvidenceKind = Literal["source_text", "derived", "image_candidate", "external_missing"]


class ResearchEvidencePoint(BaseModel):
    content: str
    source_labels: list[str] = Field(default_factory=list)
    derivation_note: str = ""
    evidence_kind: EvidenceKind = "source_text"
    source_locations: list[str] = Field(default_factory=list)
    time_scope: str = ""
    metric_scope: str = ""
    unit: str = ""
    verification_note: str = ""
    usable: bool = True

    @model_validator(mode="after")
    def protect_unverified_evidence(self) -> "ResearchEvidencePoint":
        if self.evidence_kind in {"image_candidate", "external_missing"}:
            self.usable = False
        if self.evidence_kind == "derived" and (
            not self.source_labels or not _contains_explicit_calculation(self.derivation_note)
        ):
            self.usable = False
        return self


class ResearchPlanSubsection(BaseModel):
    heading: str
    evidence_points: list[ResearchEvidencePoint] = Field(default_factory=list)
    missing_note: str = ""
    image_reminders: list[str] = Field(default_factory=list)


class ResearchPlanSection(BaseModel):
    heading: str
    subsections: list[ResearchPlanSubsection] = Field(default_factory=list)


class ResearchSynthesisPlan(BaseModel):
    title: str = ""
    outline_type: OutlineType
    coverage_mode: CoverageMode
    classification_reason: str
    required_headings: list[str] = Field(default_factory=list)
    selected_headings: list[str] = Field(default_factory=list)
    omitted_outline_items: list[str] = Field(default_factory=list)
    sections: list[ResearchPlanSection] = Field(default_factory=list)
    unresolved_conflicts: list[str] = Field(default_factory=list)
    missing_items: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    message: str = ""


class ResearchSynthesisResult(BaseModel):
    title: str
    body: str
    sources: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    message: str = ""
    output_file: str = ""


def _contains_explicit_calculation(note: str) -> bool:
    numbers = re.findall(r"\d[\d,，]*(?:\.\d+)?", note)
    has_operator = bool(re.search(r"[+＋\-−×xX*÷/]", note))
    has_result = "=" in note or "＝" in note
    return len(numbers) >= 3 and has_operator and has_result


__all__ = [
    "CoverageMode",
    "EvidenceKind",
    "OutlineType",
    "ResearchEvidencePoint",
    "ResearchPlanSection",
    "ResearchPlanSubsection",
    "ResearchSynthesisPlan",
    "ResearchSynthesisResult",
]
