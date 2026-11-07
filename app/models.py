"""Data shapes shared by the agent, the API and the incident log."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


MemoryKind = Literal["fix", "failure", "context"]


class Memory(BaseModel):
    """One memory Hindsight recalled, labelled so the UI and prompt can tell fixes from failures."""

    ref: str  # "M1", "M2", ... used for citations in the triage
    text: str
    type: str = "world"  # Hindsight fact type: world | experience | observation
    kind: MemoryKind = "context"
    occurred: str | None = None


class Step(BaseModel):
    action: str
    why: str = ""
    sources: list[str] = Field(default_factory=list)


class Avoid(BaseModel):
    action: str
    why: str = ""
    sources: list[str] = Field(default_factory=list)


class TriageResult(BaseModel):
    summary: str
    likely_cause: str = ""
    confidence: Literal["low", "medium", "high"] = "low"
    steps: list[Step] = Field(default_factory=list)
    avoid: list[Avoid] = Field(default_factory=list)
    similar_incidents: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    memories: list[Memory] = Field(default_factory=list)
    memory_enabled: bool = True
    model: str = ""
    degraded: bool = False  # True when the LLM failed and we fell back to raw memories


class Resolution(BaseModel):
    root_cause: str
    worked: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    minutes_to_resolve: int | None = None
    notes: str = ""


class Incident(BaseModel):
    id: str
    title: str
    service: str
    severity: str
    alert: str
    status: Literal["open", "resolved"] = "open"
    created_at: datetime = Field(default_factory=utcnow)
    resolved_at: datetime | None = None
    triage: TriageResult | None = None
    resolution: Resolution | None = None
