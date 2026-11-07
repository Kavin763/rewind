"""A small JSON-file log of incidents the agent has triaged.

This is the agent's working list (open and resolved incidents shown in the UI).
Long-term knowledge lives in Hindsight, not here.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from .models import Incident

FIRST_ID = 2101


class IncidentStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = asyncio.Lock()
        self._incidents: dict[str, Incident] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return  # a corrupt log should not stop the agent from starting
        for item in raw:
            inc = Incident.model_validate(item)
            self._incidents[inc.id] = inc

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        data = [i.model_dump(mode="json") for i in self._incidents.values()]
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic on Windows and POSIX

    def _next_id(self) -> str:
        nums = [int(i.split("-")[1]) for i in self._incidents if i.startswith("INC-") and i.split("-")[1].isdigit()]
        return f"INC-{max([*nums, FIRST_ID - 1]) + 1}"

    async def create(self, *, title: str, service: str, severity: str, alert: str) -> Incident:
        async with self._lock:
            inc = Incident(id=self._next_id(), title=title, service=service, severity=severity, alert=alert)
            self._incidents[inc.id] = inc
            self._flush()
            return inc

    async def add(self, incident: Incident) -> None:
        """Insert an incident that already has an id (used by the seed script)."""
        await self.save(incident)

    async def save(self, incident: Incident) -> None:
        async with self._lock:
            self._incidents[incident.id] = incident
            self._flush()

    def get(self, incident_id: str) -> Incident | None:
        return self._incidents.get(incident_id)

    def list(self) -> list[Incident]:
        return sorted(self._incidents.values(), key=lambda i: i.created_at, reverse=True)
