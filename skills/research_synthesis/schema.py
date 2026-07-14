from pydantic import BaseModel, Field


class ResearchEvidencePoint(BaseModel):
    content: str
    source_labels: list[str] = Field(default_factory=list)
    derivation_note: str = ""


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


__all__ = [
    "ResearchEvidencePoint",
    "ResearchPlanSection",
    "ResearchPlanSubsection",
    "ResearchSynthesisPlan",
    "ResearchSynthesisResult",
]
