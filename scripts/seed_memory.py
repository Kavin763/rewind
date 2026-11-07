"""Load past incidents into Hindsight so Rewind starts with two months of on-call history.

Usage (from the project root, with Hindsight running):
    python scripts/seed_memory.py               # reset the bank, load history, show incidents in the sidebar
    python scripts/seed_memory.py --memory-only # same, without adding them to the sidebar list
    python scripts/seed_memory.py --keep        # add to the existing bank instead of resetting it

Note: by default this deletes the bank first, including anything Rewind learned since.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.memory import IncidentMemory  # noqa: E402
from app.models import Incident, Resolution  # noqa: E402
from app.store import IncidentStore  # noqa: E402

SEED_FILE = ROOT / "data" / "seed_incidents.json"


def load_seed() -> list[tuple[Incident, Resolution]]:
    out = []
    for row in json.loads(SEED_FILE.read_text(encoding="utf-8")):
        started = datetime.fromisoformat(row["date"].replace("Z", "+00:00"))
        res = Resolution(
            root_cause=row["root_cause"],
            worked=row["worked"],
            failed=row["failed"],
            minutes_to_resolve=row.get("minutes"),
            notes=row.get("notes", ""),
        )
        inc = Incident(
            id=row["id"],
            title=row["title"],
            service=row["service"],
            severity=row["severity"],
            alert=row["alert"],
            status="resolved",
            created_at=started,
            resolved_at=started + timedelta(minutes=row.get("minutes") or 30),
            resolution=res,
        )
        out.append((inc, res))
    return out


async def main(memory_only: bool, wait_minutes: float = 20, keep: bool = False) -> None:
    memory = IncidentMemory.from_settings(settings)
    if not await memory.healthy():
        sys.exit(f"Hindsight is not reachable at {settings.hindsight_url}. Start it with: docker compose up -d")
    if keep:
        await memory.ensure_bank()
    else:
        print(f"Resetting memory bank '{settings.bank_id}' so the history loads cleanly...")
        await memory.reset()
    store = None if memory_only else IncidentStore(settings.incidents_file)

    incidents = load_seed()
    total = 0
    for n, (inc, res) in enumerate(incidents, 1):
        written = await memory.retain_resolution(inc, res)
        total += written
        if store:
            await store.add(inc)
        print(f"[{n:>2}/{len(incidents)}] {inc.id} {inc.service:<14} {written} facts queued  {inc.title}")
    print(f"\nQueued {len(incidents)} post-mortems ({total} fixes, failures and causes) in bank '{settings.bank_id}'.")
    if not settings.retain_async:
        return
    # Free Groq tiers allow ~8k tokens/minute, so Hindsight's worker extracts facts gradually.
    print("Hindsight is extracting facts in the background (free LLM tiers are rate limited).")
    for _ in range(int(wait_minutes * 6)):
        pending = await memory.pending_operations()
        if pending is None:
            print("Could not read Hindsight's queue; continuing. Memories keep loading in the background.")
            return
        if pending == 0:
            print("All memories are processed and ready.")
            return
        print(f"  {pending} memory jobs still processing...", flush=True)
        await asyncio.sleep(10)
    print("Still processing; Rewind can start now and memories will keep arriving in the background.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-only", action="store_true", help="don't add seed incidents to the sidebar list")
    parser.add_argument("--wait-minutes", type=float, default=20, help="how long to wait for Hindsight to finish processing")
    parser.add_argument("--keep", action="store_true", help="add to the existing bank instead of resetting it first")
    args = parser.parse_args()
    asyncio.run(main(args.memory_only, args.wait_minutes, args.keep))
