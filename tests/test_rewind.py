"""Tests run without Hindsight, Groq, or network: the memory client and LLM are faked."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent import (
    IncidentAgent,
    build_user_prompt,
    coerce_triage,
    detect_service,
    detect_severity,
    make_title,
)
from app.llm import LLMError, parse_json_loose
from app.memory import IncidentMemory, classify, merge_results
from app.models import Incident, Memory, Resolution
from app.store import IncidentStore

ROOT = Path(__file__).resolve().parent.parent
run = asyncio.run

POOL_ALERT = (
    "[FIRING] sev1 service=payments-api alert=ChargeErrorRate\n"
    "HikariPool-1 - Connection is not available, request timed out after 30000ms"
)


# ---------- fakes ----------


class FakeMemory:
    def __init__(self, memories=None, fail_retain=False):
        self.memories = memories or []
        self.recall_calls = 0
        self.retained = []
        self.fail_retain = fail_retain

    async def recall_for_alert(self, alert, service):
        self.recall_calls += 1
        return self.memories

    async def retain_resolution(self, incident, res):
        if self.fail_retain:
            raise ConnectionError("hindsight down")
        self.retained.append((incident.id, res))
        return 1 + len(res.worked) + len(res.failed)

    async def ask(self, question):
        return f"answer to: {question}"


class FakeLLM:
    last_model = "fake-model"

    def __init__(self, reply=None, error=None):
        self.reply = reply or {}
        self.error = error
        self.prompts = []

    async def complete_json(self, system, user):
        self.prompts.append(user)
        if self.error:
            raise self.error
        return self.reply


class FakeHindsightClient:
    """Mimics the async methods of hindsight_client.Hindsight that Rewind uses."""

    def __init__(self, recall_results=None, fail_second_recall=False):
        self.retains = []
        self.recall_queries = []
        self.recall_results = recall_results or []
        self.fail_second_recall = fail_second_recall

    async def aretain(self, **kwargs):
        self.retains.append(kwargs)

    async def arecall(self, **kwargs):
        self.recall_queries.append(kwargs["query"])
        if self.fail_second_recall and len(self.recall_queries) == 2:
            raise TimeoutError("slow")
        return SimpleNamespace(results=self.recall_results)

    async def areflect(self, **kwargs):
        return SimpleNamespace(text="payments-api had two pool incidents")

    class _Ops:
        def __init__(self):
            self.calls = []

        async def list_operations(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(total=2 if kwargs["status"] == "pending" else 1, operations=[])

    operations = _Ops()


MEMS = [
    Memory(ref="M1", text="Fix that worked for payments-api incident INC-2041: scaled back to 8 replicas.", kind="fix"),
    Memory(
        ref="M2",
        text="Failed fix during incident INC-2041 on payments-api: restarting all pods caused a cascade. Do not repeat this.",
        kind="failure",
    ),
]


@pytest.fixture
def store(tmp_path):
    return IncidentStore(tmp_path / "incidents.json")


# ---------- parsing and helpers ----------


def test_parse_json_handles_reasoning_fences_and_trailing_commas():
    assert parse_json_loose('<think>hmm</think>```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_loose('Sure! Here it is: {"a": [1, 2,],} hope that helps') == {"a": [1, 2]}
    with pytest.raises(LLMError):
        parse_json_loose("no json here")
    with pytest.raises(LLMError):
        parse_json_loose("")


def test_classify_labels_failures_before_fixes():
    assert classify("Failed fix: restarting pods made things worse") == "failure"
    assert classify("Fix that worked: paused the refund batch") == "fix"
    assert classify("payments-db-primary allows 300 connections") == "context"
    # Hindsight rewords facts, so paraphrases must still be caught
    assert classify("Restarting all payments-api pods at once caused a 20-minute cascade") == "failure"
    assert classify("Scaling orders-worker to 24 replicas did not help") == "failure"
    assert classify("Running FLUSHDB triggered a login stampede") == "failure"
    assert classify("Errors cleared within 4 minutes after scaling back to 8 replicas") == "fix"
    assert classify("Fix: lowered maximumPoolSize from 20 to 12, resolving the incident in 41 minutes") == "fix"
    assert classify("Failed fix: restarted all pods at once, causing a 20-minute cascade") == "failure"


def test_merge_results_dedupes_and_numbers():
    a = [SimpleNamespace(text="Fix that worked: scale down", type="world"), {"text": "  ", "type": "world"}]
    b = [{"text": "fix that worked:   scale down", "type": "observation"}, {"text": "Did not work: restart", "type": "experience"}]
    merged = merge_results([a, b])
    assert [m.ref for m in merged] == ["M1", "M2"]
    assert merged[1].kind == "failure" and merged[1].type == "experience"


def test_detectors():
    assert detect_service(POOL_ALERT) == "payments-api"
    assert detect_service("no service here") == "unknown"
    assert detect_severity(POOL_ALERT) == "sev1"
    assert detect_severity("P2 page") == "sev2"
    assert detect_severity("nothing") == "sev2"
    assert make_title(POOL_ALERT).startswith("ChargeErrorRate on payments-api: HikariPool-1")
    assert make_title("[FIRING] Disk almost full on db-3") == "Disk almost full on db-3"
    assert make_title("  \n ") == "Untitled alert"


def test_prompt_marks_failures():
    prompt = build_user_prompt(POOL_ALERT, "payments-api", MEMS)
    assert "M2 [FAILURE]" in prompt and "M1 [FIX]" in prompt
    assert "first-time incident" in build_user_prompt(POOL_ALERT, "x", [])


def test_coerce_drops_fake_citations_and_unknown_incidents():
    raw = {
        "summary": "Seen before",
        "confidence": "HIGH",
        "steps": [{"action": "Scale down", "sources": ["M1", "M9"]}, "Check deploys", {"action": ""}],
        "avoid": [{"action": "Restart all pods", "why": "cascade", "sources": "M2"}],
        "similar_incidents": ["INC-2041", "INC-9999"],
    }
    t = coerce_triage(raw, MEMS)
    assert t.confidence == "high"
    assert [s.action for s in t.steps] == ["Scale down", "Check deploys"]
    assert t.steps[0].sources == ["M1"]
    assert t.avoid[0].sources == ["M2"]
    assert t.similar_incidents == ["INC-2041"]
    assert coerce_triage({"confidence": "high"}, []).confidence == "low"


# ---------- agent ----------


def test_triage_with_memory_warns_about_past_failures(store):
    llm = FakeLLM({"summary": "Pool exhaustion again", "confidence": "high", "avoid": [{"action": "Restart all pods", "sources": ["M2"]}]})
    mem = FakeMemory(MEMS)
    inc = run(IncidentAgent(mem, llm, store).triage(POOL_ALERT))
    assert mem.recall_calls == 1
    assert "[FAILURE]" in llm.prompts[0]
    assert inc.triage.avoid[0].action == "Restart all pods"
    assert inc.triage.memory_enabled and inc.triage.model == "fake-model"
    assert store.get(inc.id).triage is not None  # persisted


def test_triage_without_memory_skips_recall(store):
    mem = FakeMemory(MEMS)
    inc = run(IncidentAgent(mem, FakeLLM({"summary": "generic"}), store).triage(POOL_ALERT, use_memory=False))
    assert mem.recall_calls == 0
    assert inc.triage.memories == [] and not inc.triage.memory_enabled


def test_llm_failure_falls_back_to_raw_memories(store):
    inc = run(IncidentAgent(FakeMemory(MEMS), FakeLLM(error=LLMError("429")), store).triage(POOL_ALERT))
    assert inc.triage.degraded
    assert inc.triage.steps[0].sources == ["M1"]
    assert inc.triage.avoid[0].sources == ["M2"]


def test_empty_alert_rejected(store):
    with pytest.raises(ValueError):
        run(IncidentAgent(FakeMemory(), FakeLLM(), store).triage("   "))


def test_resolve_retains_and_closes(store):
    agent = IncidentAgent(FakeMemory(), FakeLLM({"summary": "x"}), store)
    inc = run(agent.triage(POOL_ALERT))
    res = Resolution(root_cause="pool too big", worked=["scale down", " "], failed=["restart all pods"], minutes_to_resolve=12)
    updated, written = run(agent.resolve(inc.id, res))
    assert written == 3
    assert updated.status == "resolved" and store.get(inc.id).status == "resolved"
    assert updated.resolution.worked == ["scale down"]


def test_resolve_keeps_incident_open_when_memory_write_fails(store):
    agent = IncidentAgent(FakeMemory(fail_retain=True), FakeLLM({"summary": "x"}), store)
    inc = run(agent.triage(POOL_ALERT))
    with pytest.raises(ConnectionError):
        run(agent.resolve(inc.id, Resolution(root_cause="x")))
    assert store.get(inc.id).status == "open"


def test_resolve_validation(store):
    agent = IncidentAgent(FakeMemory(), FakeLLM({"summary": "x"}), store)
    inc = run(agent.triage(POOL_ALERT))
    with pytest.raises(ValueError):
        run(agent.resolve(inc.id, Resolution(root_cause="  ")))
    with pytest.raises(KeyError):
        run(agent.resolve("INC-0000", Resolution(root_cause="x")))


# ---------- memory layer against a fake Hindsight client ----------


def test_retain_writes_one_postmortem_with_each_fix_and_failure_as_its_own_sentence():
    client = FakeHindsightClient()
    mem = IncidentMemory(client, "bank")
    inc = Incident(id="INC-1", title="pool", service="payments-api", severity="sev1", alert=POOL_ALERT)
    res = Resolution(root_cause="too many replicas", worked=["scale to 8"], failed=["restart all pods"], minutes_to_resolve=41)
    assert run(mem.retain_resolution(inc, res)) == 3  # root cause + 1 fix + 1 failure
    assert len(client.retains) == 1  # one LLM extraction per incident (free-tier token budgets)
    call = client.retains[0]
    assert call["bank_id"] == "bank" and call["document_id"] == "INC-1-postmortem"
    assert call["retain_async"] is True  # queued, so rate limits can't drop it
    lines = call["content"].splitlines()
    failure = next(l for l in lines if "restart all pods" in l)
    fix = next(l for l in lines if "scale to 8" in l)
    assert "INC-1" in failure and "payments-api" in failure and "Do not repeat" in failure
    assert classify(failure) == "failure" and classify(fix) == "fix"


def test_pending_operations_counts_pending_and_processing():
    assert run(IncidentMemory(FakeHindsightClient(), "bank").pending_operations()) == 3
    assert run(IncidentMemory(SimpleNamespace(), "bank").pending_operations()) is None


def test_recall_runs_two_queries_and_survives_one_failing():
    client = FakeHindsightClient(recall_results=[SimpleNamespace(text="Fix that worked: scale down", type="world")], fail_second_recall=True)
    mems = run(IncidentMemory(client, "bank").recall_for_alert(POOL_ALERT, "payments-api"))
    assert len(client.recall_queries) == 2
    assert any("failed" in q.lower() for q in client.recall_queries)
    assert [m.kind for m in mems] == ["fix"]


def test_ask_uses_reflect():
    assert "pool" in run(IncidentMemory(FakeHindsightClient(), "bank").ask("what breaks?"))


# ---------- store and data ----------


def test_store_ids_and_persistence(tmp_path):
    path = tmp_path / "i.json"
    s = IncidentStore(path)
    a = run(s.create(title="a", service="x", severity="sev2", alert="a"))
    b = run(s.create(title="b", service="x", severity="sev2", alert="b"))
    assert (a.id, b.id) == ("INC-2101", "INC-2102")
    reloaded = IncidentStore(path)
    assert [i.id for i in reloaded.list()] == ["INC-2102", "INC-2101"]
    path.write_text("{broken")
    assert IncidentStore(path).list() == []


def test_seed_and_demo_data_are_valid():
    sys_path_seed = ROOT / "scripts" / "seed_memory.py"
    assert sys_path_seed.exists()
    import importlib.util

    spec = importlib.util.spec_from_file_location("seed_memory", sys_path_seed)
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    pairs = seed.load_seed()
    assert len(pairs) >= 10
    assert sum(len(r.failed) for _, r in pairs) >= 8
    for _, res in pairs:
        assert res.root_cause and res.worked
    demos = json.loads((ROOT / "data" / "demo_alerts.json").read_text())
    for d in demos:
        assert detect_service(d["alert"]) == d["service"]


def test_main_module_imports_when_fastapi_installed():
    pytest.importorskip("fastapi")
    pytest.importorskip("openai")
    pytest.importorskip("hindsight_client")
    import app.main  # noqa: F401


def test_reset_deletes_and_recreates_bank():
    calls = []

    class C:
        async def adelete_bank(self, bank_id):
            calls.append(("delete", bank_id))

        async def acreate_bank(self, **kw):
            calls.append(("create", kw["bank_id"]))

    run(IncidentMemory(C(), "bank").reset())
    assert calls == [("delete", "bank"), ("create", "bank")]


def test_ask_retries_after_rate_limit(monkeypatch):
    import app.memory as memory_mod

    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(memory_mod.asyncio, "sleep", fake_sleep)

    class C:
        calls = 0

        async def areflect(self, **kw):
            C.calls += 1
            if C.calls == 1:
                raise RuntimeError("HTTP 429: Rate limit reached ... Please try again in 5.2s")
            return SimpleNamespace(text="restarting all pods made INC-2041 worse")

    assert "INC-2041" in run(IncidentMemory(C(), "bank").ask("what got worse?"))
    assert C.calls == 2 and 7 <= sleeps[0] <= 8


def test_ask_does_not_retry_other_errors():
    class C:
        async def areflect(self, **kw):
            raise RuntimeError("bank not found")

    with pytest.raises(RuntimeError):
        run(IncidentMemory(C(), "bank").ask("x"))


def test_ask_does_not_retry_daily_limit():
    class C:
        calls = 0

        async def areflect(self, **kw):
            C.calls += 1
            raise RuntimeError("429 Rate limit reached on tokens per day (TPD): Limit 200000. Please try again in 9m11s")

    with pytest.raises(RuntimeError):
        run(IncidentMemory(C(), "bank").ask("x"))
    assert C.calls == 1
