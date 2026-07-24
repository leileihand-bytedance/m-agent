from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


SectionName = Literal["党政要闻", "监管动态", "同业动向", "市场观察", "前沿观点"]
MarketScope = Literal["weekly_a", "monday_a", "weekly_hk", "weekly_us"]


class WebCandidate(BaseModel):
    url: str
    canonical_url: str
    title: str
    site: str
    publisher: str = ""
    publish_date: str = ""
    date_extracted_from: str = ""
    body: str


class ContentCandidateAssessment(BaseModel):
    source_url: str
    include: bool
    section: SectionName
    title: str
    summary: str
    evidence_excerpt: str = ""
    evidence_block_ids: list[str] = Field(default_factory=list)
    score: float = Field(ge=0, le=10)
    reason: str


class ContentAssessmentBatch(BaseModel):
    items: list[ContentCandidateAssessment] = Field(default_factory=list)


class GroundingRepairItem(BaseModel):
    source_url: str
    summary: str
    evidence_block_ids: list[str] = Field(default_factory=list)


class GroundingRepairBatch(BaseModel):
    items: list[GroundingRepairItem] = Field(default_factory=list)


class PartyEventSynthesis(BaseModel):
    title: str
    summary: str


class MarketSeriesEvidence(BaseModel):
    scope: MarketScope
    index_code: str
    index_name: str
    start_date: str
    end_date: str
    start_close: float | None = Field(default=None, gt=0)
    end_close: float | None = Field(default=None, gt=0)
    reported_change_pct: float | None = None
    source_url: str
    source_title: str
    evidence_excerpt: str


class MarketContextEvidence(BaseModel):
    scope: MarketScope
    summary: str
    source_url: str
    source_title: str
    evidence_excerpt: str


class MarketEvidenceBundle(BaseModel):
    series: list[MarketSeriesEvidence] = Field(default_factory=list)
    contexts: list[MarketContextEvidence] = Field(default_factory=list)


class FrontierSelection(BaseModel):
    source_url: str
    title: str
    chinese_title: str = ""
    institution: str
    authors: list[str] = Field(default_factory=list)
    publish_date: str
    selected_passages: list[str] = Field(default_factory=list)
    chinese_summary: str = ""
    source_location: str
    reason: str


class SourceRecord(BaseModel):
    source_id: str
    title: str
    publisher: str = ""
    publish_date: str = ""
    url: str
    retrieved_at: str
    source_type: Literal["news", "market_data", "research_report"]
    source_location: str = "网页正文"
    evidence_excerpts: list[str] = Field(default_factory=list)
    content_sha256: str


class WeeklyItem(BaseModel):
    item_id: str
    section: SectionName
    title: str
    body: str
    content_mode: Literal[
        "summary",
        "market_fixed",
        "market_update",
        "report_extract",
        "report_summary",
    ]
    source_ids: list[str] = Field(default_factory=list)
    fixed_position: int | None = None


class WeeklySection(BaseModel):
    name: SectionName
    items: list[WeeklyItem] = Field(default_factory=list)


class InternalWeeklyResult(BaseModel):
    generation_mode: Literal["full_weekly", "market_update"] = "full_weekly"
    title: str = ""
    body: str = ""
    publication_date: str = ""
    period_start: str = ""
    period_end: str = ""
    sections: list[WeeklySection] = Field(default_factory=list)
    source_records: list[SourceRecord] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    ready_for_approval: bool = False
    draft_version: str = ""
    document_metadata: dict[str, str] = Field(default_factory=dict)
    output_file: str = ""
    manifest_file: str = ""
    needs_clarification: bool = False
    message: str = ""
