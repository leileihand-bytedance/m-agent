from pydantic import BaseModel, Field


class ResearchSynthesisResult(BaseModel):
    title: str
    body: str
    sources: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    message: str = ""


__all__ = ["ResearchSynthesisResult"]
