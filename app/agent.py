"""The on-call agent: triage an alert using memory, and learn from how it was resolved."""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol

from .llm import LLMError
from .models import Avoid, Incident, Memory, Resolution, Step, TriageResult, utcnow
from .store import IncidentStore

log = logging.getLogger("rewind.agent")


class MemoryBackend(Protocol):
    async def recall_for_alert(self, alert: str, service: str) -> list[Memory]: ...
    async def retain_resolution(self, incident: Incident, res: Resolution) -> int: ...
    async def ask(self, question: str) -> str: ...


class JSONLLM(Protocol):
    last_model: str

    async def complete_json(self, system: str, user: str) -> dict[str, Any]: ...


SYSTEM_PROMPT = """You are Rewind, an on-call assistant for a platform engineering team.
An engineer has been paged. Help them resolve the incident fast and safely.

You are given the ALERT and a list of MEMORIES recalled from past incidents (each has an id like M3).
Memories marked [FAILURE] describe fixes that did not work or made an incident worse.

Rules:
- Ground advice in the memories whenever they are relevant, and cite them by id in "sources".
- If a memory says an action failed or caused harm for a similar alert, put it in "avoid" and
  explain what happened last time. Never recommend that action in "steps".
- If no memory is relevant, say so in the summary and give careful, generic first steps
  (check recent deploys, dashboards, logs). Do not invent past incidents.
- Order steps from fastest/safest to slowest/riskiest. Keep each action short and concrete.
- Only cite ids that appear in MEMORIES. List incident ids (like INC-2041) you recognise
  in the memories under "similar_incidents".
- confidence is "high" only when a memory describes the same symptom on the same service with a
  confirmed fix; "medium" for a close match; otherwise "low".

Reply with one JSON object and nothing else:
{
  "summary": "1-2 sentences: what is probably happening and whether we have seen it before",
  "likely_cause": "short",
  "confidence": "low|medium|high",
  "steps": [{"action": "...", "why": "...", "sources": ["M1"]}],
  "avoid": [{"action": "...", "why": "what happened last time", "sources": ["M2"]}],
  "similar_incidents": ["INC-...."],
  "open_questions": ["what the engineer should check that memory cannot answer"]
}"""


_SEVERITY = re.compile(r"\b(sev[- ]?[0-4]|p[0-4])\b", re.IGNORECASE)
_SERVICE = re.compile(r"(?:service|svc|app|job)[\"']?\s*[=:]\s*[\"']?([A-Za-z0-9_.\-]+)", re.IGNORECASE)
_INCIDENT_ID = re.compile(r"\bINC-\d{3,6}\b")


def detect_service(alert: str) -> str:
    m = _SERVICE.search(alert)
    return m.group(1).lower() if m else "unknown"


def detect_severity(alert: str) -> str:
    m = _SEVERITY.search(alert)
    if not m:
        return "sev2"
    tag = m.group(1).lower().replace(" ", "").replace("-", "")
    return tag if tag.startswith("sev") else f"sev{tag[1]}"


_ALERT_NAME = re.compile(r"\balert(?:name)?\s*[=:]\s*([A-Za-z0-9_.\-]+)", re.IGNORECASE)


def make_title(alert: str) -> str:
    lines = [ln.strip() for ln in alert.splitlines() if ln.strip()]
    if not lines:
        return "Untitled alert"
    first = re.sub(r"^\[?(firing|alert|critical|warning)\]?:?\s*", "", lines[0], flags=re.IGNORECASE)
    name, service = _ALERT_NAME.search(first), _SERVICE.search(first)
    if name and service:
        # "[FIRING] sev1 service=payments-api alert=ChargeErrorRate" -> "ChargeErrorRate on payments-api: <next line>"
        title = f"{name.group(1)} on {service.group(1)}"
        if len(lines) > 1:
            title += f": {lines[1]}"
    else:
        title = first
    return title[:90] + ("…" if len(title) > 90 else "")


def build_user_prompt(alert: str, service: str, memories: list[Memory]) -> str:
    if memories:
        mem_lines = "\n".join(
            f"{m.ref} [{m.kind.upper()}]{f' ({m.occurred[:10]})' if m.occurred else ''}: {m.text}" for m in memories
        )
    else:
        mem_lines = "(no memories: treat this as a first-time incident)"
    return f"SERVICE: {service}\n\nALERT:\n{alert.strip()}\n\nMEMORIES:\n{mem_lines}"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def coerce_triage(raw: dict[str, Any], memories: list[Memory]) -> TriageResult:
    """Turn a loosely-shaped LLM reply into a TriageResult, dropping citations to memories that don't exist."""
    valid_refs = {m.ref for m in memories}

    def refs(item: dict[str, Any]) -> list[str]:
        return [str(r) for r in _as_list(item.get("sources")) if str(r) in valid_refs]

    def items(key: str, cls: type) -> list[Any]:
        out = []
        for item in _as_list(raw.get(key)):
            if isinstance(item, str):
                item = {"action": item}
            if not isinstance(item, dict) or not str(item.get("action", "")).strip():
                continue
            out.append(cls(action=str(item["action"]).strip(), why=str(item.get("why", "")).strip(), sources=refs(item)))
        return out

    confidence = str(raw.get("confidence", "low")).lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "low"
    if not memories:
        confidence = "low"  # without history the agent cannot be confident

    # Only keep incident ids that actually appear in recalled memories.
    known_ids = {i for m in memories for i in _INCIDENT_ID.findall(m.text)}
    similar = [str(i) for i in _as_list(raw.get("similar_incidents")) if str(i) in known_ids]

    return TriageResult(
        summary=str(raw.get("summary") or "No summary returned.").strip(),
        likely_cause=str(raw.get("likely_cause") or "").strip(),
        confidence=confidence,  # type: ignore[arg-type]
        steps=items("steps", Step),
        avoid=items("avoid", Avoid),
        similar_incidents=list(dict.fromkeys(similar)),
        open_questions=[str(q) for q in _as_list(raw.get("open_questions")) if str(q).strip()],
        memories=memories,
    )


def fallback_triage(memories: list[Memory], error: str) -> TriageResult:
    """When the LLM is down, still hand the engineer what memory knows."""
    steps = [Step(action=m.text, sources=[m.ref]) for m in memories if m.kind == "fix"][:5]
    avoid = [Avoid(action=m.text, sources=[m.ref]) for m in memories if m.kind == "failure"][:5]
    summary = (
        "The language model is unavailable, so here is what memory recalled directly."
        if memories
        else "The language model is unavailable and memory has nothing on this alert. Start with recent deploys and dashboards."
    )
    log.error("Falling back to raw memories: %s", error)
    return TriageResult(summary=summary, steps=steps, avoid=avoid, memories=memories, degraded=True)


class IncidentAgent:
    def __init__(self, memory: MemoryBackend, llm: JSONLLM | None, store: IncidentStore):
        self.memory = memory
        self.llm = llm
        self.store = store

    async def triage(
        self,
        alert: str,
        *,
        service: str | None = None,
        severity: str | None = None,
        use_memory: bool = True,
    ) -> Incident:
        alert = alert.strip()
        if not alert:
            raise ValueError("alert text is empty")
        service = (service or "").strip().lower() or detect_service(alert)
        incident = await self.store.create(
            title=make_title(alert),
            service=service,
            severity=(severity or "").strip().lower() or detect_severity(alert),
            alert=alert,
        )

        memories: list[Memory] = []
        if use_memory:
            try:
                memories = await self.memory.recall_for_alert(alert, service)
            except Exception as exc:  # memory down should not block the page
                log.warning("recall failed, continuing without memory: %s", exc)

        if self.llm is None:
            result = fallback_triage(memories, "no LLM configured")
        else:
            try:
                raw = await self.llm.complete_json(SYSTEM_PROMPT, build_user_prompt(alert, service, memories))
                result = coerce_triage(raw, memories)
                result.model = self.llm.last_model
            except LLMError as exc:
                result = fallback_triage(memories, str(exc))

        result.memory_enabled = use_memory
        incident.triage = result
        await self.store.save(incident)
        return incident

    async def resolve(self, incident_id: str, resolution: Resolution) -> tuple[Incident, int]:
        incident = self.store.get(incident_id)
        if incident is None:
            raise KeyError(incident_id)
        resolution.worked = [w.strip() for w in resolution.worked if w.strip()]
        resolution.failed = [f.strip() for f in resolution.failed if f.strip()]
        if not resolution.root_cause.strip():
            raise ValueError("root cause is required")
        # Work on a copy so a failed memory write leaves the incident open and retryable.
        updated = incident.model_copy(update={"resolution": resolution, "status": "resolved", "resolved_at": utcnow()})
        written = await self.memory.retain_resolution(updated, resolution)
        await self.store.save(updated)
        return updated, written

    async def ask(self, question: str) -> str:
        question = question.strip()
        if not question:
            raise ValueError("question is empty")
        return await self.memory.ask(question)
