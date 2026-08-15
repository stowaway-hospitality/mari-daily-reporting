#!/usr/bin/env python3
"""Session claims — so two Cowork chats cannot quietly overwrite each other.

WHY THIS EXISTS
---------------
2026-08-14: two sessions worked this repo at once. One pushed a two-hunk commit
to .github/workflows/daily_pull.yml; the other rebased the same file and the
rebase kept ONE hunk and dropped the other. Nothing failed, nothing conflicted,
CI stayed green — and the dropped hunk was the one that made a sales backfill
write to the right DAY. Three days of sales silently would not heal, and it took
hours to find because the fix was still in the commit log, just not in the file.

That is the failure mode this guards: not a merge conflict (git tells you about
those) but a SILENT LOSS. The two habits that catch it are claiming an area
before you touch it, and verifying your change is actually on main after you push.

USAGE
    python3 scripts/session.py status
    python3 scripts/session.py start cost-book --who "ILG re-parse"
    python3 scripts/session.py verify modules/invoices/parsers/ilg.py
    python3 scripts/session.py end cost-book

Claims live in ops/session_claims.json, are committed, and EXPIRE after 12 hours
so a session that dies does not block the repo forever.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAIMS = ROOT / "ops" / "session_claims.json"
EXPIRY_H = 12
REPO = "zakstowaway/mari-daily-reporting"

# An area is a NAME plus the paths it owns. Overlap is computed on the paths, so
# two differently-named claims that touch the same file still collide.
AREAS: dict[str, list[str]] = {
    "cost-book": [
        "modules/recipes/", "modules/invoices/", "data/cogs_list.csv",
        "data/costs.csv", "data/recipes/", "scripts/build_cost_book_flags.py",
        "scripts/convert_lightspeed_recipes.py", "dashboard/_shared/recipe_",
        "dashboard/_shared/flags", "modules/recipes/app/",
    ],
    "inventory": [
        "modules/inventory/", "INVENTORY_ARCHITECTURE.md", "data/stock_",
        "dashboard/inventory/",
    ],
    "sales-pipeline": [
        "scripts/daily_aggregator.py", "scripts/ingest_insights_email.py",
        "scripts/cogs_blend.py", "data/*_daily_history.csv",
    ],
    "ops": [
        ".github/workflows/", "scripts/health_monitor.py", "ops/",
        "scripts/build_site.py", "scripts/alert_check.py",
    ],
    "dashboard": [
        "dashboard/sales/", "dashboard/home/", "dashboard/_shared/render.js",
        "dashboard/_shared/pnl.js", "dashboard/_shared/restatements.js",
    ],
    "bookings": ["dashboard/bookings/"],
}

# Touch these and you are in EVERY area at once. Claiming one of these means
# nobody else should be pushing at all.
GLOBAL_PATHS = [
    ".github/workflows/", "scripts/health_monitor.py", "scripts/build_site.py",
    "CLAUDE.md", "data/costs.csv", "data/cogs_list.csv",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load() -> dict:
    if CLAIMS.exists():
        try:
            return json.loads(CLAIMS.read_text())
        except Exception:                                    # noqa: BLE001
            pass
    return {"claims": []}


def _live(d: dict) -> list:
    """Claims that have not expired. A dead session must not block the repo."""
    out = []
    for c in d.get("claims", []):
        try:
            if _now() - datetime.fromisoformat(c["at"]) < timedelta(hours=EXPIRY_H):
                out.append(c)
        except Exception:                                    # noqa: BLE001
            continue
    return out


def _save(d: dict) -> None:
    CLAIMS.parent.mkdir(parents=True, exist_ok=True)
    CLAIMS.write_text(json.dumps(d, indent=1, sort_keys=True) + "\n")


def _sh(*a) -> str:
    try:
        return subprocess.run(a, cwd=ROOT, capture_output=True, text=True,
                              timeout=60).stdout.strip()
    except Exception:                                        # noqa: BLE001
        return ""


def _overlap(a: str, b: str) -> list[str]:
    pa, pb = AREAS.get(a, [a]), AREAS.get(b, [b])
    return [x for x in pa if any(x.startswith(y) or y.startswith(x) for y in pb)]


def _ci_status() -> str:
    """Latest Tests conclusion, or 'unknown' without a token. Never blocks."""
    tok = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
           or os.environ.get("GH_DISPATCH_PAT"))
    if not tok:
        for cand in (ROOT / ".secrets" / "github_pat_v2.txt",
                     Path(os.path.expanduser(
                         "~/Documents/STOW/Sales Reports/Daily Reporting"))
                     / ".secrets" / "github_pat_v2.txt"):
            try:
                if cand.exists():
                    tok = cand.read_text().strip()
                    break
            except Exception:                                # noqa: BLE001
                pass
    if not tok:
        return "unknown (no token)"
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/actions/workflows/tests.yml/runs?per_page=1",
            headers={"Authorization": f"token {tok}"})
        with urllib.request.urlopen(req, timeout=20) as r:
            runs = json.loads(r.read()).get("workflow_runs") or []
        return (runs[0].get("conclusion") or runs[0].get("status") or "?") if runs else "none"
    except Exception as e:                                   # noqa: BLE001
        return f"unknown ({type(e).__name__})"


def cmd_status() -> int:
    d = _load()
    live = _live(d)
    print(f"CI (Tests): {_ci_status()}")
    print(f"clone: {ROOT}")
    if str(ROOT).startswith("/Users/Shared/ClaudeShared"):
        print("  !! THIS IS THE SHARED MOUNT. Work in a /tmp clone instead.")
    if not live:
        print("claims: none — the repo is free")
        return 0
    print(f"claims ({len(live)} live, {EXPIRY_H}h expiry):")
    for c in live:
        age = _now() - datetime.fromisoformat(c["at"])
        print(f"  {c['area']:16} {c.get('who','?'):28} {int(age.total_seconds()//60)}m ago")
    return 0


def cmd_start(area: str, who: str) -> int:
    if area not in AREAS:
        print(f"unknown area '{area}'. Known: {', '.join(sorted(AREAS))}")
        return 2
    d = _load()
    live = _live(d)
    clashes = [(c, _overlap(area, c["area"])) for c in live if _overlap(area, c["area"])]
    print(f"CI (Tests): {_ci_status()}")
    if clashes:
        print("\nBLOCKED — someone else holds overlapping paths:")
        for c, paths in clashes:
            print(f"  {c['area']} ({c.get('who','?')}) — shared: {', '.join(paths[:4])}")
        print("\nEither wait, pick a different area, or agree a handover with Zak.")
        return 1
    if area in ("ops",) or any(p in GLOBAL_PATHS for p in AREAS[area]):
        print("\nNOTE: this area touches GLOBAL paths (workflows / guards / CLAUDE.md).")
        print("A change here affects every other session. Keep it short and tell Zak.")
    d.setdefault("claims", [])
    d["claims"] = live + [{"area": area, "who": who, "at": _now().isoformat(timespec="seconds")}]
    _save(d)
    print(f"\nclaimed '{area}' for: {who}")
    print("Commit ops/session_claims.json with your first push so other sessions see it.")
    return 0


def cmd_end(area: str) -> int:
    d = _load()
    keep = [c for c in _live(d) if c["area"] != area]
    d["claims"] = keep
    _save(d)
    print(f"released '{area}'. {len(keep)} claim(s) still live.")
    return 0


def cmd_verify(paths: list[str]) -> int:
    """Did what I just pushed actually land? The check that would have caught the
    2026-08-14 silent hunk loss the moment it happened."""
    _sh("git", "fetch", "-q", "origin", "main")
    bad = []
    for p in paths:
        diff = _sh("git", "diff", "origin/main", "--", p)
        print(f"  {'DIFFERS' if diff else 'on main'}  {p}")
        if diff:
            bad.append(p)
    if bad:
        print("\n!! origin/main does NOT match your copy for the files above.")
        print("   Either you have not pushed, or someone rebased over you.")
        print("   Re-apply and push again — do NOT assume the commit log is enough.")
        return 1
    print("\nall verified on origin/main")
    return 0


def main() -> int:
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if a[0] == "status":
        return cmd_status()
    if a[0] == "start":
        if len(a) < 2:
            print("usage: session.py start <area> --who \"<what you are doing>\"")
            return 2
        who = " ".join(a[a.index("--who") + 1:]) if "--who" in a else "unnamed session"
        return cmd_start(a[1], who)
    if a[0] == "end":
        return cmd_end(a[1]) if len(a) > 1 else 2
    if a[0] == "verify":
        return cmd_verify(a[1:] or ["."])
    print(f"unknown command '{a[0]}'")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
