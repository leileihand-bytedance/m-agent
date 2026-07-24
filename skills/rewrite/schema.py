from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema


class RewriteResult(BaseModel):
    title: str = ""
    body: str
    revision_note: str = ""
    sources: list[str] = Field(default_factory=list)
    revision_plan: SkipJsonSchema[dict[str, object]] = Field(
        default_factory=dict,
        exclude=True,
    )
    needs_clarification: bool = False
    message: str = ""
