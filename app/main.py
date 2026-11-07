"""HTTP API and web console for Rewind.

Run with:  uvicorn app.main:app --reload
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import IncidentAgent
from .config import ROOT, settings
from .llm import GroqLLM, LLMError
from .memory import IncidentMemory, is_daily_limit, is_rate_limited
from .models import Incident, Resolution
from .store import IncidentStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rewind")

STATIC = ROOT / "app" / "static"
DEMO_ALERTS = ROOT / "data" / "demo_alerts.json"


@asynccontextmanager
async def lifespan(app: FastAPI):
    memory = IncidentMemory.from_settings(settings)
    await memory.ensure_bank()
    try:
        llm = GroqLLM.from_settings(settings)
    except LLMError as exc:
        log.warning("LLM disabled: %s. Triage will show raw memories only.", exc)
        llm = None
    app.state.memory = memory
    app.state.agent = IncidentAgent(memory, llm, IncidentStore(settings.incidents_file))
    yield


app = FastAPI(title="Rewind", description="On-call incident agent with Hindsight memory", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class TriageRequest(BaseModel):
    alert: str = Field(min_length=1, max_length=20000)
    service: str | None = None
    severity: str | None = None
    use_memory: bool = True


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


def agent() -> IncidentAgent:
    return app.state.agent


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
async def health() -> dict:
    return {
        "hindsight": await app.state.memory.healthy(),
        "llm": agent().llm is not None,
        "bank_id": settings.bank_id,
        "model": settings.llm_model,
    }


@app.get("/api/samples")
async def samples() -> list[dict]:
    if not DEMO_ALERTS.exists():
        return []
    return json.loads(DEMO_ALERTS.read_text(encoding="utf-8"))


@app.get("/api/incidents")
async def list_incidents() -> list[dict]:
    return [
        {
            "id": i.id,
            "title": i.title,
            "service": i.service,
            "severity": i.severity,
            "status": i.status,
            "created_at": i.created_at,
            "memory_enabled": i.triage.memory_enabled if i.triage else None,
        }
        for i in agent().store.list()
    ]


@app.get("/api/incidents/{incident_id}")
async def get_incident(incident_id: str) -> Incident:
    inc = agent().store.get(incident_id)
    if inc is None:
        raise HTTPException(404, f"{incident_id} not found")
    return inc


@app.post("/api/triage")
async def triage(req: TriageRequest) -> Incident:
    try:
        return await agent().triage(req.alert, service=req.service, severity=req.severity, use_memory=req.use_memory)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/incidents/{incident_id}/resolve")
async def resolve(incident_id: str, res: Resolution) -> dict:
    try:
        incident, written = await agent().resolve(incident_id, res)
    except KeyError:
        raise HTTPException(404, f"{incident_id} not found")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        log.exception("Saving to memory failed")
        raise HTTPException(502, f"Could not save to Hindsight: {exc}") from exc
    return {"incident": incident, "memories_written": written}


@app.post("/api/ask")
async def ask(req: AskRequest) -> dict:
    try:
        return {"answer": await agent().ask(req.question)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        log.exception("reflect failed")
        if is_daily_limit(exc):
            raise HTTPException(
                503, "The LLM's free-tier daily token limit is used up. It frees up gradually over the next hours."
            ) from exc
        if is_rate_limited(exc):
            raise HTTPException(
                503, "The LLM's free-tier rate limit is busy (memory processing shares it). Try again in a minute."
            ) from exc
        raise HTTPException(502, f"Hindsight reflect failed: {exc}") from exc
