"""
Liveness heartbeats for the unattended jobs.

An unattended job that only writes to its log when it has WORK to do looks
identical, from the outside, to one that has silently died — both go quiet. A
heartbeat fixes that: the job records "I ran to completion at T" every cycle,
work or no work, so a monitor can tell "nothing to do" (fresh heartbeat) from
"dead" (stale heartbeat).

    from core.heartbeat import beat
    beat("xero_approvals")     # call at the END of a successful cycle

Writing a heartbeat must NEVER be able to break the job it reports on, so every
failure here is swallowed — a missing heartbeat is a monitor's problem to notice,
not a reason to crash a working poller.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "data" / "heartbeats"


def beat(job: str, ok: bool = True, note: str = "") -> None:
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        (DIR / f"{job}.json").write_text(json.dumps({
            "job": job,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ok": bool(ok),
            "note": note,
        }))
    except Exception:
        pass   # a heartbeat must never take down the job it measures
