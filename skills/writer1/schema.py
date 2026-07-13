from pydantic import BaseModel, Field


class BriefResult(BaseModel):
    title: str
    body: str
    sources: list[str] = Field(default_factory=list)
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
