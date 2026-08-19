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
REPO = "stowaway-hospitality/mari-daily-reporting"

# An area is a NAME plus the paths it owns. Overlap is computed on the paths, so
# two differently-named claims that touch the same file still collide.
AREAS: dict[str, list[str]] = {
    "cost-book": [
        "modules/recipes/", "modules/invoices/", "data/cogs_list.csv",
        "data/costs.csv", "data/recipes/", "scripts/build_cost_book_flags.py",
        "scripts/convert_lightspeed_recipes.py", "dashboard/_shared/recipe_",
        "dashboard/_shared/flags", "modules/recipes/app/",
    ],
    # The Uber delivery feed (Eats marketplace + Uber Direct), added 2026-08-19
    # on Zak's instruction. It had NO claimable area at all: the whole feed —
    # three CSVs, a log, the upsert/probe scripts and its three test modules —
    # matched nothing in AREAS, so two sessions could both rewrite
    # data/uber_daily.csv and the register would say nothing. That is exactly
    # the hole the par comment below describes.
    #
    # Boundaries, because _overlap() is prefix-based and symmetric:
    #   * .github/workflows/uber_direct_dispatch.yml and probe_uber_direct_api
    #     .yml are WORKFLOWS: ops-owned, per rule 7. Listing any ".github/" path
    #     here would make uber clash with every ops claim forever.
    #   * "data/uber_" is safe against cost-book (data/costs.csv,
    #     data/cogs_list.csv), inventory (data/stock_) and par (data/par_) —
    #     no prefix relation in either direction.
    #   * sales-pipeline owns "data/*_daily_history.csv", a literal string that
    #     does not prefix-match data/uber_daily.csv.
    "uber": [
        "data/uber_", "tests/test_uber_", "pipedream/uber_direct_ingest.js",
        "scripts/uber_direct_upsert.py", "scripts/enter_uber_direct.py",
        "scripts/probe_uber_direct_api.py",
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
        # The claim machinery itself. Editing it unclaimed is how you break the
        # thing that stops two sessions breaking each other.
        "scripts/session.py", "SESSIONS.md",
    ],
    "dashboard": [
        "dashboard/sales/", "dashboard/home/", "dashboard/_shared/render.js",
        "dashboard/_shared/pnl.js", "dashboard/_shared/restatements.js",
    ],
    "bookings": ["dashboard/bookings/"],
    # The weekly par model. Added 2026-08-15 on Zak's instruction — the entire
    # par v3 landing happened with no claimable area, which is how it collided
    # with cost-book on build_ingredients.py the same afternoon.
    #
    # Boundaries are deliberate, because _overlap() is prefix-based:
    #   * the par HELPER scripts live in ops/ (build_upload.py, stockout_audit
    #     .py, make_par_xlsx.py...) and stay OPS-owned — listing any "ops/..."
    #     path here would make par clash with every ops claim forever.
    #   * data/stock_counts/ feeds the shrinkage engine but sits under
    #     inventory's "data/stock_" prefix; committing a new count file needs
    #     the inventory area (or its holder's nod).
    #   * .github/workflows/par_model.yml is a workflow: ops, per rule 7.
    "par": [
        "modules/par/", "scripts/build_par_model.py",
        "scripts/par_flag_report.py", "scripts/par_v3_impact.py",
        "tests/test_par_model.py", "data/par_", "data/_par_review/",
        "data/_scrape_", "data/_upload_", "data/_downgrade_",
        "data/_correction_",
    ],
}

# Touch these and you are in EVERY area at once. Claiming one of these means
# nobody else should be pushing at all.
GLOBAL_PATHS = [
    ".github/workflows/", "scripts/health_monitor.py", "scripts/build_site.py",
    "CLAUDE.md", "data/costs.csv", "data/cogs_list.csv",
    "scripts/session.py", "SESSIONS.md",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


CLAIMS_PATH = "ops/session_claims.json"


def _token() -> str | None:
    tok = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
           or os.environ.get("GH_DISPATCH_PAT"))
    if tok:
        return tok.strip()
    for cand in (ROOT / ".secrets" / "github_pat_v2.txt",
                 Path("/Users/Shared/ClaudeShared/STOW/Sales Reports/Daily Reporting")
                 / ".secrets" / "github_pat_v2.txt"):
        try:
            if cand.exists():
                return cand.read_text().strip()
        except Exception:                                    # noqa: BLE001
            pass
    return None


def _api(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    import urllib.error
    import urllib.request
    tok = _token()
    if not tok:
        return 0, {}
    req = urllib.request.Request(
        f"https://api.github.com{path}", method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"token {tok}",
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:                                    # noqa: BLE001
            return e.code, {}
    except Exception:                                        # noqa: BLE001
        return 0, {}


def _load() -> dict:
    """Read the claims that are ON MAIN — never the local working copy.

    This used to read the file in your clone, which is the same silent-loss trap
    the tool exists to close: your copy goes stale the moment another session
    claims something, so status cheerfully reported "the repo is free" while two
    areas were held. Measured on 2026-08-15, on this repo, by this tool.
    """
    st, js = _api("GET", f"/repos/{REPO}/contents/{CLAIMS_PATH}?ref=main")
    if st == 200 and js.get("content"):
        import base64
        try:
            d = json.loads(base64.b64decode(js["content"]))
            d["_sha"] = js.get("sha")
            return d
        except Exception:                                    # noqa: BLE001
            pass
    # No token / no network: fall back to origin/main via git, and SAY SO.
    _sh("git", "fetch", "-q", "origin", "main")
    txt = _sh("git", "show", f"origin/main:{CLAIMS_PATH}")
    if txt:
        try:
            d = json.loads(txt)
            d["_readonly"] = "git (no API token — cannot claim or release)"
            return d
        except Exception:                                    # noqa: BLE001
            pass
    return {"claims": [], "_readonly": "nothing readable — treat as UNKNOWN, not free"}


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


def _save(d: dict, msg: str) -> int:
    """Write the claim register straight to main, atomically.

    Claims are worthless unless every session sees them at once, so this does NOT
    wait for your next push. The old behaviour printed "commit it with your first
    push" — and a release that was never committed left a dead claim blocking the
    repo, which happened within an hour of shipping the tool. The `sha` makes this
    compare-and-swap: if another session claimed something meanwhile, the write is
    rejected rather than clobbering them.
    """
    if d.get("_readonly"):
        print(f"CANNOT WRITE — {d['_readonly']}")
        return 1
    import base64
    body = {k: v for k, v in d.items() if not k.startswith("_")}
    payload = json.dumps(body, indent=1, sort_keys=True) + "\n"
    st, js = _api("PUT", f"/repos/{REPO}/contents/{CLAIMS_PATH}", {
        "message": msg,
        "content": base64.b64encode(payload.encode()).decode(),
        "sha": d.get("_sha"), "branch": "main"})
    if st in (200, 201):
        # Deliberately do NOT write the local copy. main is the register; a file
        # in your clone that drifts from it is the bug this commit removes, and
        # writing it also left the tree dirty enough to block `git pull --rebase`.
        return 0
    print(f"claim register write FAILED (HTTP {st}) — {js.get('message', '?')}")
    print("Do NOT proceed as though you hold the area.")
    return 1


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
    tok = _token()
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
    src = d.get("_readonly")
    if src:
        print(f"  !! reading via {src}")
    if not live:
        print("claims: none — the repo is free" if not src
              else "claims: none VISIBLE — but the register is not readable; assume it is held")
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
    d["claims"] = live + [{"area": area, "who": who, "at": _now().isoformat(timespec="seconds")}]
    if _save(d, f"session: claim {area} — {who}"):
        return 1
    print(f"\nclaimed '{area}' for: {who}")
    print("Recorded on main — every other session can see it now. Do not commit")
    print("ops/session_claims.json yourself; an older copy in your clone would")
    print("silently un-claim whoever landed after you.")
    return 0


def cmd_end(area: str) -> int:
    d = _load()
    keep = [c for c in _live(d) if c["area"] != area]
    d["claims"] = keep
    if _save(d, f"session: release {area}"):
        print(f"'{area}' is STILL HELD on main. Tell Zak rather than leaving it.")
        return 1
    print(f"released '{area}' on main. {len(keep)} claim(s) still live.")
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
