from pydantic import BaseModel, Field


class DirectReportViolation(BaseModel):
    rule: str
    severity: str = "hard"
    message: str
    suggestion: str = ""


class DirectReportResult(BaseModel):
    title: str
    body: str
    sources: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    message: str = ""


class DirectReportCriticResult(BaseModel):
    violations: list[DirectReportViolation] = Field(default_factory=list)
    needs_clarification: bool = False
    message: str = ""
