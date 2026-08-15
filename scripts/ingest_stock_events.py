#!/usr/bin/env python3
"""Turn what a human recorded on a phone into ledger movements.

Reads stock_events (counts, goods-received, waste) and books the ones that can
be booked. The events themselves are never edited or discarded — the event store
keeps everything a person saw; the LEDGER only takes what is provable. Anything
unconvertible comes back as a worklist instead of a guess.

THREE THINGS IT REFUSES, each for a reason already paid for here:

  1. NO CONTAINER SIZE. "0.75 of a bottle" is not a quantity until somebody has
     said how big the bottle is. Assuming 700 ml because most spirits are 700 ml
     would be wrong on every future count of that item, forever.

  2. A PARTIAL COUNT SETTING TRUTH. A count supersedes everything before it, so
     counting the bar while stock sits in the storeroom writes off the storeroom
     as phantom waste — and phantom waste cannot be told from theft. A session
     must cover every location the item is known to live in.

  3. AN ITEM WITH NO CANONICAL BASE UNIT. If the recipes cannot agree whether a
     tin is `each` or `ml`, a count of it cannot be denominated either.

GOODS RECEIVED IS THE FACT; THE INVOICE IS THE SECOND OPINION. A receive event
carries what was ordered (`expected_qty`) and what turned up. The difference is
not an error to be smoothed over — it is a supplier credit claim with the
person's name and the time against it, and nobody in this business has that
number today.

Source of events, in order:
  --file <path.json>   a list of event dicts. Used by the tests and for a manual
                       import; also the offline path when the phone app exports.
  Supabase             the production path. Reads pending rows with the service
                       key, which comes from the environment and is NEVER
                       handled here — Zak sets SUPABASE_URL / SUPABASE_SERVICE_KEY
                       in the Actions secrets, exactly as invoice approvals do.

Run:
    python3 scripts/ingest_stock_events.py --file events.json           # dry
    python3 scripts/ingest_stock_events.py --file events.json --write
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ledger import (Movement, append, count_scope_warning,      # noqa: E402
                    load_base_units, qty_str)

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
CONTAINERS = ROOT / "data" / "container_sizes.csv"

# Units a person may count in that need no container table: they already ARE the
# base unit, or they convert by pure arithmetic.
DIRECT = {"g": (Decimal(1), "g"), "kg": (Decimal(1000), "g"),
          "ml": (Decimal(1), "ml"), "l": (Decimal(1000), "ml"),
          "each": (Decimal(1), "each"), "ea": (Decimal(1), "each")}

# Units that mean "one of whatever this item comes in" — resolved per item.
CONTAINER_WORDS = {"bottle", "keg", "container", "pack", "box", "bag", "tray",
                   "drum", "tin", "can", "carton", "case", "jar", "punnet"}

REASON_FOR = {"count": "count", "receive": "receive", "waste": "waste",
              "transfer": "transfer"}


def dec(x) -> Decimal | None:
    try:
        return Decimal(str(x).strip())
    except (InvalidOperation, AttributeError, TypeError, ValueError):
        return None


def load_containers() -> dict[str, tuple[Decimal, str, str]]:
    """item_id -> (base_qty per container, base unit, where that came from)."""
    out: dict[str, tuple[Decimal, str, str]] = {}
    if not CONTAINERS.exists():
        return out
    with CONTAINERS.open() as f:
        for r in csv.DictReader(f):
            q = dec(r["base_qty"])
            if q and q > 0:
                out[r["item_id"]] = (q, r["base_unit"], r["source"])
    return out


def convert(ev: dict, containers, base_units) -> tuple[Decimal, str] | str:
    """-> (qty in base units, base unit), or a string saying why not."""
    qty = dec(ev.get("counted_qty"))
    if qty is None:
        return "counted_qty is not a number"
    if qty < 0:
        return "negative count — direction carries the sign, not the quantity"

    unit = (ev.get("counted_unit") or "").strip().lower()
    item = ev.get("item_id") or ""
    want = base_units.get(item)

    if unit in DIRECT:
        factor, base = DIRECT[unit]
        if want and want != base:
            return (f"counted in {unit} but this item is measured in {want} — "
                    f"one of the two is wrong about what it is")
        return qty * factor, base

    if unit in CONTAINER_WORDS:
        hit = containers.get(item)
        if not hit:
            return (f"counted in {unit!r} and nobody has said how big one is. "
                    f"Add it to data/pack_overrides.yaml — guessing is wrong on "
                    f"every future count of this item")
        per, base, _src = hit
        if want and want != base:
            return f"container is in {base} but recipes use {want}"
        return qty * per, base

    return f"unit {unit!r} is not one this system knows how to convert"


def fetch_supabase() -> list[dict]:
    """Pending rows, read with the service key.

    The key comes from the environment and is never written down here — same as
    modules/auth/set_role.py and the invoice approvals poller. Zak sets
    SUPABASE_URL and SUPABASE_SERVICE_KEY as Actions secrets; nothing in this
    repo or in the browser ever holds it.
    """
    import urllib.parse
    import urllib.request

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not (url and key):
        raise SystemExit(
            "set SUPABASE_URL and SUPABASE_SERVICE_KEY (service_role, not anon), "
            "or pass --file <events.json>")
    q = urllib.parse.urlencode({"status": "eq.pending",
                                "order": "occurred_at.asc", "limit": "5000"})
    req = urllib.request.Request(
        f"{url}/rest/v1/stock_events?{q}",
        headers={"apikey": key, "authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or "[]")


def mark(ids_status: list[tuple[str, str, str]]) -> None:
    """Write each event's outcome back, so nothing is booked twice and a refusal
    is visible to the person who recorded it rather than dying in a log."""
    import urllib.request

    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not (url and key):
        return
    for ev_id, status, why in ids_status:
        body = json.dumps({"status": status, "ledger_note": why or None,
                           "booked_at": "now()" if status == "booked" else None}).encode()
        req = urllib.request.Request(
            f"{url}/rest/v1/stock_events?id=eq.{ev_id}", data=body, method="PATCH",
            headers={"apikey": key, "authorization": f"Bearer {key}",
                     "content-type": "application/json", "prefer": "return=minimal"})
        try:
            urllib.request.urlopen(req).read()
        except Exception as e:                      # noqa: BLE001
            print(f"  could not mark {ev_id}: {e}")


def main() -> int:
    if "--file" in sys.argv:
        path = Path(sys.argv[sys.argv.index("--file") + 1])
        events = json.loads(path.read_text())
        if isinstance(events, dict):
            events = events.get("events", [])
    else:
        events = fetch_supabase()

    containers = load_containers()
    base_units = load_base_units()

    # Which locations each session actually walked — needed before any count in
    # it is allowed to supersede.
    session_locs: dict[str, set[str]] = defaultdict(set)
    for ev in events:
        if ev.get("kind") == "count":
            ref = ev.get("session_ref") or ""
            for loc in (ev.get("session_locations") or
                        ([ev["location"]] if ev.get("location") else [])):
                session_locs[ref].add(loc)

    booked: list[Movement] = []
    unconvertible: list[tuple[dict, str]] = []
    not_truth: list[tuple[dict, str]] = []
    claims: list[dict] = []

    for ev in events:
        kind = ev.get("kind")
        if kind not in REASON_FOR:
            unconvertible.append((ev, f"unknown kind {kind!r}"))
            continue

        got = convert(ev, containers, base_units)
        if isinstance(got, str):
            unconvertible.append((ev, got))
            continue
        qty_base, base = got

        if kind == "count":
            warn = count_scope_warning(
                ev["item_id"], session_locs.get(ev.get("session_ref") or "", set()))
            if warn:
                not_truth.append((ev, warn))
                continue

        # Ordered vs turned up. The gap is the claim.
        if kind == "receive" and ev.get("expected_qty") is not None:
            exp = dec(ev["expected_qty"])
            got_q = dec(ev["counted_qty"])
            if exp is not None and got_q is not None and exp != got_q:
                claims.append({"item": ev["item_id"], "name": ev.get("item_name", ""),
                               "po": ev.get("po_ref"), "supplier": ev.get("supplier_key"),
                               "ordered": exp, "received": got_q,
                               "short_by": exp - got_q, "actor": ev.get("actor")})

        booked.append(Movement(
            ts=(ev.get("occurred_at") or "")[:10],
            venue=ev.get("venue") or "",
            item_id=ev["item_id"],
            qty_base=qty_str(qty_base),
            base_unit=base,
            direction="out" if kind in ("waste",) else "in",
            reason=REASON_FOR[kind],
            source_ref=(ev.get("session_ref") or ev.get("po_ref")
                        or f"event:{ev.get('id', '?')}"),
            actor=ev.get("actor") or "unknown",
            note=(ev.get("note") or "")[:60],
            location=ev.get("location") or "",
            counted_qty=str(ev.get("counted_qty")),
            counted_unit=(ev.get("counted_unit") or "").strip().lower(),
        ))

    print(f"{len(events)} event(s) in")
    print(f"  bookable:             {len(booked)}")
    print(f"  cannot convert:       {len(unconvertible)}")
    print(f"  recorded, not truth:  {len(not_truth)}  (partial counts)")
    print(f"  supplier claims:      {len(claims)}")

    for ev, why in unconvertible[:10]:
        print(f"\n  ! {ev.get('item_name') or ev.get('item_id')}: {why}")
    for ev, why in not_truth[:5]:
        print(f"\n  ~ {why}")
    for c in claims[:10]:
        print(f"\n  $ {c['name'] or c['item']} on {c['po']}: ordered {c['ordered']}, "
              f"received {c['received']} — short {c['short_by']} ({c['supplier']})")

    if "--write" in sys.argv and booked:
        wrote = append(booked)
        print(f"\nbooked {sum(wrote.values())} movement(s): {wrote}")
        outcomes = [(e["id"], "booked", "") for e in events
                    if e.get("id") and e not in [x[0] for x in unconvertible]
                    and e not in [x[0] for x in not_truth]]
        outcomes += [(e["id"], "needs_conversion", why)
                     for e, why in unconvertible if e.get("id")]
        outcomes += [(e["id"], "needs_conversion", why)
                     for e, why in not_truth if e.get("id")]
        if "--file" not in sys.argv:
            mark(outcomes)
            print(f"marked {len(outcomes)} event(s) back in Supabase")
    elif "--write" not in sys.argv:
        print("\n(dry run — pass --write to book these into data/ledger/)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
