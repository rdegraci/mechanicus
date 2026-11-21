"""Pydantic models and draft session state."""
from pydantic import BaseModel, Field


class TestsResult(BaseModel):
    tests: str
    check: str
    explanation: str


class CodeResult(BaseModel):
    code: str
    explanation: str


class StrengthenReview(BaseModel):
    """Whether adopted tests adequately prove the behavior."""

    adequate: bool
    reason: str
    gaps: str = ""
    explanation: str = ""


class PlannedModule(BaseModel):
    name: str
    behavior: str
    uses: list[str] = Field(default_factory=list)


class ApplicationPlan(BaseModel):
    modules: list[PlannedModule]
    explanation: str


class BehaviorParts(BaseModel):
    parts: list[str]
    explanation: str


class DescriptionEnrichment(BaseModel):
    """Assumed product additions for /plan full."""

    additions: list[str] = Field(default_factory=list)
    enriched_description: str
    explanation: str = ""


class CycleRepair(BaseModel):
    name: str
    behavior: str
    uses: list[str] = Field(default_factory=list)
    modules: list[PlannedModule]
    explanation: str


class DraftTurn(BaseModel):
    reply: str
    description: str = ""
    read_paths: list[str] = Field(default_factory=list)


class SuggestionItem(BaseModel):
    title: str
    detail: str
    tag: str = "feature"


class SuggestionsResult(BaseModel):
    purpose: str
    suggestions: list[SuggestionItem] = Field(default_factory=list)
    bugs: list[SuggestionItem] = Field(default_factory=list)
    explanation: str = ""


class DraftSession:
    def __init__(self, kind, description="", inventory="", reads=None, transcript=None):
        self.kind = kind
        self.description = description
        self.inventory = inventory
        self.reads = reads if reads is not None else {}
        self.transcript = transcript if transcript is not None else []
