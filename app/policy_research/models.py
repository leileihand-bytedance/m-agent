from __future__ import annotations

from pydantic import BaseModel, Field


class PolicyCandidate(BaseModel):
    title: str
    source: str
    category: str
    publish_date: str
    url: str
    snippet: str
    matched_terms: list[str] = Field(default_factory=list)
    relevance_score: int = 0
    selection_reason: str = ""


class PolicyResearchResult(BaseModel):
    should_attach_policy: bool
    decision_reason: str
    matched_themes: list[str] = Field(default_factory=list)
    retrieval_query: str = ""
    confidence: float = 0.0
    primary_policy: PolicyCandidate | None = None
    alternative_policies: list[PolicyCandidate] = Field(default_factory=list)
