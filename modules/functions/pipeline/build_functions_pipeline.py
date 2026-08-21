#!/usr/bin/env python3
"""Build `data/functions_pipeline.json` — every enquiry on the monday tracker.

    monday board 5027645686  ──►  data/functions_monday_raw.json  ──►  data/functions_pipeline.json  ──►  /functions/
    FUNCTIONS ENQUIRY TRACKER      the capture, committed                 the feed                        the Pipeline tab

Run:
    python3 modules/functions/pipeline/build_functions_pipeline.py
    python3 modules/functions/pipeline/build_functions_pipeline.py --check
    MONDAY_API_TOKEN=... python3 modules/functions/pipeline/build_functions_pipeline.py --fetch

TWO STEPS, AND WHY THEY ARE SEPARATE
------------------------------------
The board is read in one step and derived in another, with a committed file in
between. That is not ceremony:

* **The derivation has to reproduce in CI.** MODULES.md rule 4 — "a derived
  file that no longer reproduces from source is a fossil and every number on
  it is quietly wrong". If the only source were the live board, CI could never
  check the feed at all: it has no monday token, and the board changes hourly.
  With the capture committed, `--check` rebuilds and byte-compares on every
  pytest run, exactly as `data/costs.csv` does.

* **The capture is dated evidence.** `captured_at` rides through onto the feed
  and onto the screen, so a stale feed announces its own age instead of
  passing for live. `data/function_tabs/cost_book_<date>.json` exists for the
  same reason.

**There is no monday token in this repo's Actions secrets yet**, so `--fetch`
cannot run unattended. `modules/functions/feed.md` records the one secret that
needs adding. Until it is added the committed capture is what the screen
shows, dated, which is why the age is on the page in words rather than in a
tooltip.

THE FETCH
---------
`--fetch` asks monday's GraphQL API for the board, writes the capture, and
then derives. It pages at 100 items with a cursor because `items_page` will
not return the whole board in one call and a silent first page is exactly the
failure this feed exists to end.

Column values come back as `text`, which is the label for a status, the ISO
date for a date, and the raw string for everything else — the same shape the
MCP capture holds. The one known difference is the phone column, which
GraphQL suffixes with an ISO country code; `enquiries._phone` strips it, so
both routes produce the same feed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.functions.enquiries import BOARD_ID, COL, build   # noqa: E402

RAW = ROOT / "data" / "functions_monday_raw.json"
FEED = ROOT / "data" / "functions_pipeline.json"

API = "https://api.monday.com/v2"
API_VERSION = "2024-10"
PAGE = 100

QUERY = """
query ($board: [ID!], $limit: Int!, $cursor: String) {
  boards (ids: $board) {
    id name updated_at
    groups { id title }
    items_page (limit: $limit, cursor: $cursor) {
      cursor
      items {
        id name url created_at updated_at
        group { id title }
        column_values { id text }
      }
    }
  }
}
"""


def _post(token: str, variables: dict) -> dict:
    req = urllib.request.Request(
        API, method="POST",
        data=json.dumps({"query": QUERY, "variables": variables}).encode(),
        headers={"Authorization": token, "Content-Type": "application/json",
                 "API-Version": API_VERSION})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.loads(r.read())
    if body.get("errors"):
        raise SystemExit(f"monday API said: {body['errors']}")
    return body["data"]


def fetch(token: str, captured_at: str) -> dict:
    """The whole board, in the shape `data/functions_monday_raw.json` holds."""
    items, cursor, board = [], None, None
    while True:
        data = _post(token, {"board": [BOARD_ID], "limit": PAGE,
                             "cursor": cursor})
        board = data["boards"][0]
        page = board["items_page"]
        for it in page["items"]:
            cv = {c["id"]: (c["text"] or None) for c in it["column_values"]}
            items.append({
                "id": it["id"], "name": it["name"], "url": it["url"],
                "created_at": it["created_at"], "updated_at": it["updated_at"],
                "group": it["group"],
                # Only the columns this feed publishes, in the board's own
                # order. A column added to the board tomorrow lands in neither
                # the capture nor the feed until somebody adds it to COL, which
                # is the right way round: an unread column is not a silent one.
                "column_values": {cid: cv.get(cid) for cid in COL.values()},
            })
        cursor = page.get("cursor")
        if not cursor:
            break
    return {
        "schema": "functions_monday_raw/1",
        "captured_at": captured_at,
        "captured_by": "modules/functions/pipeline/build_functions_pipeline.py "
                       "--fetch (monday GraphQL items_page)",
        "board_id": board["id"],
        "board_name": board["name"],
        "board_url": f"https://stowaway-bar.monday.com/boards/{board['id']}",
        "board_updated_at": board["updated_at"],
        "items_count": len(items),
        "groups": board["groups"],
        "columns": json.loads(RAW.read_text(encoding="utf-8"))["columns"]
                   if RAW.exists() else [],
        "items": items,
    }


def render(doc: dict) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="rebuild the feed and byte-compare instead of writing")
    ap.add_argument("--fetch", action="store_true",
                    help="re-read the board first (needs MONDAY_API_TOKEN)")
    ap.add_argument("--captured-at", default=None,
                    help="the capture timestamp to stamp on a --fetch")
    a = ap.parse_args()

    if a.fetch:
        token = os.environ.get("MONDAY_API_TOKEN", "").strip()
        if not token:
            print("MONDAY_API_TOKEN is not set, so the board cannot be "
                  "re-read. The committed capture is what will be used, and "
                  "the screen will show its age. See modules/functions/feed.md "
                  "for the secret that needs adding.")
            return 2
        from datetime import datetime, timezone
        stamp = a.captured_at or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        raw = fetch(token, stamp)
        RAW.write_text(render(raw), encoding="utf-8")
        print(f"re-read the board — {raw['items_count']} item(s) into "
              f"{RAW.relative_to(ROOT)}")

    if not RAW.exists():
        print(f"{RAW.relative_to(ROOT)} is missing and there is no token to "
              "rebuild it from. Nothing written.")
        return 1

    text = render(build(json.loads(RAW.read_text(encoding="utf-8"))))
    if a.check:
        have = FEED.read_text(encoding="utf-8") if FEED.exists() else ""
        if have != text:
            print(f"{FEED.relative_to(ROOT)} does NOT reproduce from "
                  f"{RAW.relative_to(ROOT)}.")
            print("Rebuild: python3 modules/functions/pipeline/"
                  "build_functions_pipeline.py")
            return 1
        print(f"{FEED.relative_to(ROOT)} reproduces byte-identically.")
        return 0

    FEED.write_text(text, encoding="utf-8")
    feed = json.loads(text)
    c = feed["counts"]
    print(f"wrote {FEED.relative_to(ROOT)} — {c['total']} enquiries "
          f"(captured {feed['captured_at']}); whose move: " +
          ", ".join(f"{k} {v}" for k, v in c["by_whose_move"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
