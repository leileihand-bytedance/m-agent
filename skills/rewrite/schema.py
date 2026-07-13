from pydantic import BaseModel, Field


class RewriteResult(BaseModel):
    title: str = ""
    body: str
    revision_note: str = ""
    sources: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    message: str = ""
