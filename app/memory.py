"""Everything Rewind remembers goes through this module.

Hindsight does the heavy lifting: `retain` extracts facts and entities from each
post-mortem, `recall` runs semantic + keyword + graph + temporal search over them,
and `reflect` reasons over the whole bank to answer open questions.

The design choice that matters most here: a failed fix is stored as its own
memory, worded as a warning, instead of being buried inside a long post-mortem.
That makes "what NOT to do" as easy to recall as "what worked".
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any, Iterable

from .models import Incident, Memory, Resolution

log = logging.getLogger("rewind.memory")

BANK_MISSION = (
    "I am the on-call memory for a platform engineering team. I remember every "
    "production incident: the alert, the root cause, the fix that worked, how long "
    "it took, and, just as importantly, the fixes that failed or made things worse. "
    "I prefer fixes with evidence from past incidents over generic advice, and I "
    "always warn about actions that previously caused harm."
)

# Hindsight stores facts in its own words, so match the ideas, not exact phrases.
_FAILURE_PATTERNS = re.compile(
    r"\b(failed fix|fix (that )?failed|did not (work|help)|didn't (work|help)|did nothing|"
    r"(made|making) (it|things|the (outage|incident))( even)? worse|worsen(ed|ing)?|backfired|"
    r"cascade|stampede|do not repeat|should not be repeated|never (restart|flush|purge|run)|"
    r"skipped \d|had to be replayed|oom-killed|added \d+ minutes)\b",
    re.IGNORECASE,
)
_FIX_PATTERNS = re.compile(
    r"\b(fix that worked|fix:|worked|resolv(ed|ing|es)|restored|mitigated|fixed|cleared|drained|recovered|went green)",
    re.IGNORECASE,
)

MAX_QUERY_CHARS = 1500

_RATE_LIMIT = re.compile(r"\b(429|rate limit|quota exhausted|too many requests)\b", re.IGNORECASE)
_TRY_AGAIN = re.compile(r"try again in ([0-9.]+)\s*s", re.IGNORECASE)


def is_rate_limited(exc: BaseException) -> bool:
    return bool(_RATE_LIMIT.search(str(exc)))


def is_daily_limit(exc: BaseException) -> bool:
    """A per-day token quota won't clear in seconds, so retrying is pointless."""
    return "per day" in str(exc).lower() or "(tpd)" in str(exc).lower()


def retry_delay(exc: BaseException, default: float = 20.0, cap: float = 45.0) -> float:
    """Seconds to wait before retrying, from the provider's 'try again in Xs' hint."""
    m = _TRY_AGAIN.search(str(exc))
    return min(cap, float(m.group(1)) + 2.0) if m else default


def classify(text: str) -> str:
    """Label a recalled fact as a failure, a fix, or plain context."""
    if _FAILURE_PATTERNS.search(text):
        return "failure"
    if _FIX_PATTERNS.search(text):
        return "fix"
    return "context"


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _occurred(result: Any) -> str | None:
    for name in ("occurred_start", "mentioned_at", "occurred_at", "timestamp"):
        value = _field(result, name)
        if value:
            return value.isoformat() if isinstance(value, datetime) else str(value)
    return None


def merge_results(groups: Iterable[Iterable[Any]], limit: int = 12) -> list[Memory]:
    """Merge several recall result lists, dropping duplicates, and number them M1..Mn."""
    seen: set[str] = set()
    merged: list[Memory] = []
    for group in groups:
        for result in group:
            text = (_field(result, "text") or "").strip()
            key = re.sub(r"\s+", " ", text.lower())
            if not text or key in seen:
                continue
            seen.add(key)
            merged.append(
                Memory(
                    ref=f"M{len(merged) + 1}",
                    text=text,
                    type=str(_field(result, "type") or "world"),
                    kind=classify(text),  # type: ignore[arg-type]
                    occurred=_occurred(result),
                )
            )
            if len(merged) >= limit:
                return merged
    return merged


def postmortem_text(incident: Incident, res: Resolution) -> str:
    """One incident as it is stored in Hindsight.

    Every fix and every failed fix is its own self-contained sentence (service, incident id,
    root cause), so Hindsight extracts each one as a separate fact that can be recalled alone.
    """
    when = (incident.resolved_at or incident.created_at).date().isoformat()
    lines = [
        f"Incident {incident.id} on {when}: {incident.title}.",
        f"Service: {incident.service}. Severity: {incident.severity}.",
        f"Alert: {incident.alert[:400]}",
        f"Root cause of {incident.id}: {res.root_cause}",
    ]
    lines += [worked_text(incident, res, fix) for fix in res.worked]
    lines += [failure_text(incident, res, bad) for bad in res.failed]
    if res.notes:
        lines.append(f"Notes on {incident.id}: {res.notes}")
    return "\n".join(lines)


def failure_text(incident: Incident, res: Resolution, bad: str) -> str:
    return (
        f"Failed fix during {incident.service} incident {incident.id}: {bad.rstrip('.')}. "
        f"Do not repeat this for a similar alert on {incident.service}."
    )


def worked_text(incident: Incident, res: Resolution, fix: str) -> str:
    took = f" (resolved in {res.minutes_to_resolve} minutes)" if res.minutes_to_resolve else ""
    return f"Fix that worked for {incident.service} incident {incident.id}{took}: {fix.rstrip('.')}."


class IncidentMemory:
    """Thin async wrapper over the Hindsight client, shaped around incidents."""

    def __init__(
        self,
        client: Any,
        bank_id: str,
        recall_max_tokens: int = 2048,
        recall_budget: str = "mid",
        retain_async: bool = True,
    ):
        self.client = client
        self.bank_id = bank_id
        self.recall_max_tokens = recall_max_tokens
        self.recall_budget = recall_budget
        self.retain_async = retain_async

    @classmethod
    def from_settings(cls, settings: Any) -> "IncidentMemory":
        from hindsight_client import Hindsight  # imported lazily so tests don't need it

        kwargs: dict[str, Any] = {"base_url": settings.hindsight_url, "timeout": 60.0}
        if settings.hindsight_api_key:
            kwargs["api_key"] = settings.hindsight_api_key
        return cls(
            Hindsight(**kwargs),
            settings.bank_id,
            recall_max_tokens=settings.recall_max_tokens,
            recall_budget=settings.recall_budget,
            retain_async=settings.retain_async,
        )

    async def reset(self) -> None:
        """Delete the bank (memories and queued jobs) and create it again, empty."""
        try:
            await self.client.adelete_bank(self.bank_id)
        except Exception as exc:
            log.info("delete_bank skipped: %s", exc)
        await self.ensure_bank()

    async def pending_operations(self) -> int | None:
        """How many retain jobs Hindsight is still processing, or None if it can't tell us."""
        try:
            total = 0
            for status in ("pending", "processing"):
                resp = await self.client.operations.list_operations(bank_id=self.bank_id, status=status, limit=1)
                total += int(_field(resp, "total", 0) or 0)
            return total
        except Exception as exc:
            log.debug("could not list operations: %s", exc)
            return None

    # ---------- setup ----------

    async def ensure_bank(self) -> None:
        """Create the memory bank with its mission. Safe to call when it already exists."""
        try:
            await self.client.acreate_bank(
                bank_id=self.bank_id,
                name="Rewind on-call memory",
                mission=BANK_MISSION,
                # Skeptical: weigh evidence, don't over-trust a single anecdote.
                disposition={"skepticism": 4, "literalism": 3, "empathy": 2},
            )
            log.info("Created memory bank %s", self.bank_id)
        except Exception as exc:  # most often: the bank already exists
            log.debug("create_bank skipped: %s", exc)

    async def healthy(self) -> bool:
        try:
            await self.client.aget_version()
            return True
        except Exception as exc:
            log.warning("Hindsight not reachable: %s", exc)
            return False

    # ---------- retain ----------

    async def retain_resolution(self, incident: Incident, res: Resolution) -> int:
        """Store a resolved incident as one post-mortem in Hindsight.

        Returns how many facts it describes (root cause + each fix + each failed fix).
        """
        # One retain per incident keeps LLM extraction cost to a single call (free Groq tiers
        # allow ~8k tokens/minute and 200k/day). retain_async queues it in Hindsight's worker,
        # which paces and retries extraction instead of failing on rate limits.
        await self.client.aretain(
            bank_id=self.bank_id,
            content=postmortem_text(incident, res),
            context="production incident post-mortem: what fixed it and which fixes failed or made it worse",
            timestamp=incident.resolved_at or incident.created_at,
            document_id=f"{incident.id}-postmortem",
            metadata={
                "incident_id": incident.id,
                "service": incident.service,
                "severity": incident.severity,
                "fixes": str(len(res.worked)),
                "failed_fixes": str(len(res.failed)),
            },
            retain_async=self.retain_async,
        )
        return 1 + len(res.worked) + len(res.failed)

    # ---------- recall ----------

    async def recall_for_alert(self, alert: str, service: str) -> list[Memory]:
        """Pull what worked and what failed for alerts like this one, in parallel."""
        alert = alert[:MAX_QUERY_CHARS]
        where = f"on {service}" if service and service != "unknown" else ""
        queries = [
            f"Past incidents similar to this alert {where} and the fixes that resolved them: {alert}",
            f"Fixes that failed or made things worse for incidents like this {where}: {alert}",
        ]
        responses = await asyncio.gather(
            *(
                self.client.arecall(
                    bank_id=self.bank_id,
                    query=q,
                    max_tokens=self.recall_max_tokens,
                    budget=self.recall_budget,
                )
                for q in queries
            ),
            return_exceptions=True,
        )
        groups = []
        for resp in responses:
            if isinstance(resp, Exception):
                log.warning("recall failed: %s", resp)
                continue
            groups.append(_field(resp, "results") or [])
        return merge_results(groups)

    # ---------- reflect ----------

    async def ask(self, question: str, attempts: int = 3) -> str:
        """Answer an open question by reasoning over everything the bank remembers.

        Free LLM tiers return 429s when Hindsight's background work shares the per-minute
        token budget, so a rate-limited reflect waits for the suggested delay and retries.
        """
        for attempt in range(1, attempts + 1):
            try:
                answer = await self.client.areflect(
                    bank_id=self.bank_id,
                    query=question,
                    budget="mid",
                    context="an on-call engineer asking about past production incidents",
                )
                return _field(answer, "text") or ""
            except Exception as exc:
                if attempt == attempts or not is_rate_limited(exc) or is_daily_limit(exc):
                    raise
                delay = retry_delay(exc)
                log.info("reflect rate limited, retrying in %.0fs (attempt %d/%d)", delay, attempt, attempts)
                await asyncio.sleep(delay)
        return ""
