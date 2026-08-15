#!/usr/bin/env python3
"""The stock movement ledger — one append-only row per change in stock.

    (ts, venue, item_id, qty_base, base_unit, direction, reason, source_ref, actor)

Six reasons cover the whole business (INVENTORY_ARCHITECTURE.md):

    receive     a supplier delivered            invoice pipeline
    sale        recipe x units sold             daily aggregation
    production  consumes ingredients, YIELDS a prep item
    waste       spill, spoil, comp, staff feed  a human
    transfer    venue -> venue
    count       sets truth and BOOKS the difference as its own row

    theoretical on-hand = sum of movements
    counted on-hand     = last count
    VARIANCE            = counted - theoretical

APPEND-ONLY. A correction is a NEW ROW, never an edit — so "what did we think
last Tuesday" stays answerable, the same property the restatement ledger gives
the P&L. Rows are sharded by year so a daily append does not rewrite history.

UNIT IDENTITY IS THE WHOLE GAME. In one day this repo found a CTN-6 read as one
tin (6x), ILG cases read as bottles (6x), Red Chilli (10x), Angostura (13x). In
COSTING a bad unit is one wrong dish. In INVENTORY it is wrong on every movement
forever, and it compounds. So:

  * one canonical base unit per item — g, ml or each, and nothing else;
  * every other unit is a DECLARED conversion with evidence;
  * an unprovable conversion RAISES. It does not guess, and it does not skip
    the line quietly either — a refused receipt is visible, a guessed one is
    not.

'box' and 'tray' are deliberately absent from the conversion table. A box of
what, how many? That number is not in the invoice, and inventing it would be
the flattering error: stock that lasts longer than it should.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, asdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
LEDGER_DIR = ROOT / "data" / "ledger"
BASE_UNITS_FILE = ROOT / "data" / "item_base_units.csv"

COLUMNS = ["ts", "venue", "item_id", "qty_base", "base_unit", "direction",
           "reason", "source_ref", "actor", "note",
           # Added 2026-08-15 for counts and goods-received. Additive only —
           # rows written before this simply carry blanks.
           "location", "counted_qty", "counted_unit"]

REASONS = {"receive", "sale", "production", "waste", "transfer", "count"}
DIRECTIONS = {"in", "out"}
BASE_UNITS = {"g", "ml", "each"}

# Declared conversions, with the dimension they land in. Anything not here has
# no provable size and must refuse. 'bunch', 'box' and 'tray' are absent ON
# PURPOSE — see the module docstring.
CONVERSIONS: dict[str, tuple[Decimal, str]] = {
    "g":  (Decimal(1),    "g"),
    "kg": (Decimal(1000), "g"),
    "mg": (Decimal("0.001"), "g"),
    "ml": (Decimal(1),    "ml"),
    "l":  (Decimal(1000), "ml"),
    "ea": (Decimal(1),    "each"),
    "each": (Decimal(1),  "each"),
}


def qty_str(q: Decimal) -> str:
    """Plain decimal text — never scientific notation.

    Decimal division produces exponent form for round numbers: a 5kg box came
    out as "5E+3" g. It is the right number and it round-trips through Decimal,
    but data/ is read by other things (and by people), and "5E+3" in a stock
    column is an invitation to a parse that returns 5.
    """
    q = q.normalize()
    if q.as_tuple().exponent > 0:          # 5E+3 -> 5000
        q = q.quantize(Decimal(1))
    return format(q, "f")


class UnprovableUnit(Exception):
    """Raised rather than guessing a conversion. See the module docstring."""


@dataclass(frozen=True)
class Movement:
    ts: str
    venue: str
    item_id: str
    qty_base: str          # Decimal as string — money and stock are never float
    base_unit: str
    direction: str
    reason: str
    source_ref: str
    actor: str
    note: str = ""
    location: str = ""          # where it physically is/was. Not a balance key.
    # WHAT THE HUMAN ACTUALLY SAID, kept verbatim beside the converted figure.
    # Nobody counts in millilitres — they count "three quarters of a bottle",
    # "0.8 of a keg", "0.035 of the 20L drum". qty_base is that times a declared
    # conversion. Keeping the original means a later correction to a bottle size
    # re-derives every historical count correctly, instead of silently baking
    # today's error into the past. Same reason mix lines keep name_as_reported.
    counted_qty: str = ""
    counted_unit: str = ""

    def validate(self) -> None:
        if self.reason not in REASONS:
            raise ValueError(f"unknown reason {self.reason!r}; expected one of {sorted(REASONS)}")
        if self.direction not in DIRECTIONS:
            raise ValueError(f"direction must be in/out, got {self.direction!r}")
        if self.base_unit not in BASE_UNITS:
            raise ValueError(
                f"base_unit must be g/ml/each, got {self.base_unit!r}. A ledger that "
                f"accepts 'box' has no idea how much stock it holds.")
        q = Decimal(self.qty_base)
        if q < 0:
            raise ValueError(
                f"qty_base is negative ({q}). Direction carries the sign — a negative "
                f"quantity plus direction 'out' is an addition nobody meant.")
        if not self.item_id or ":" not in self.item_id:
            raise ValueError(
                f"item_id {self.item_id!r} must be namespaced, e.g. 'lightspeed:21999746' — "
                f"the same key the recipe book uses, so the two cannot drift apart.")
        if not self.source_ref:
            raise ValueError("source_ref is required: every row must be traceable to its fact")


def to_base(qty: Decimal, unit: str) -> tuple[Decimal, str]:
    """(quantity, unit) -> (quantity in base units, base unit). Raises on
    anything without a declared conversion."""
    key = (unit or "").strip().lower()
    if key not in CONVERSIONS:
        raise UnprovableUnit(
            f"no declared conversion for unit {unit!r}. It is not enough to know a "
            f"'box' arrived — a box of what, how many? Guessing here is wrong on "
            f"every future movement for this item, in the direction that flatters "
            f"(stock lasting longer than it should). Declare the conversion with "
            f"evidence, or leave the line unbooked and visible.")
    factor, base = CONVERSIONS[key]
    return qty * factor, base


def load_base_units() -> dict[str, str]:
    """item_id -> its ONE canonical base unit."""
    if not BASE_UNITS_FILE.exists():
        return {}
    out = {}
    with BASE_UNITS_FILE.open() as f:
        for r in csv.DictReader(f):
            if r.get("conflict") == "true":
                continue          # an item with two base units is not usable
            out[r["item_id"]] = r["base_unit"]
    return out


def ledger_path(year: int | str) -> Path:
    return LEDGER_DIR / f"movements_{year}.csv"


def append(movements: list[Movement]) -> dict[str, int]:
    """Append rows, sharded by the year of their timestamp.

    Append-only: existing rows are never rewritten. Re-running a builder that
    produces the same source_refs will DUPLICATE them — dedupe on source_ref
    upstream, or rebuild a year wholesale with `rewrite_year`.
    """
    for m in movements:
        m.validate()
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    by_year: dict[str, list[Movement]] = {}
    for m in movements:
        by_year.setdefault(m.ts[:4], []).append(m)
    for year, rows in sorted(by_year.items()):
        path = ledger_path(year)
        exists = path.exists()
        with path.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
            if not exists:
                w.writeheader()
            for m in rows:
                w.writerow(asdict(m))
        counts[year] = len(rows)
    return counts


def rewrite_year(year: int | str, movements: list[Movement]) -> int:
    """Regenerate one year wholesale — for DERIVED movements (receive, sale)
    that are rebuilt from immutable facts and must stay reproducible.

    Never use this for `count` or `waste`: those are typed in by a human once
    and exist nowhere else.
    """
    for m in movements:
        m.validate()
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    rows = sorted(movements, key=lambda m: (m.ts, m.venue, m.item_id))
    with ledger_path(year).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for m in rows:
            w.writerow(asdict(m))
    return len(rows)


def count_scope_warning(item_id: str, locations_counted: set[str]) -> str | None:
    """None if this count may set truth for the item; a reason if it may not.

    THE TRAP THIS EXISTS FOR. Stock sits in several places — Bar & Kegroom,
    Storeroom - Bar, Pizza Shop, the HG line. A count is done by walking ONE of
    them. But a `count` row supersedes everything before it for that item, so
    counting 4 bottles of Aperol in the bar, while 6 sit unopened in the
    storeroom, writes "there are 4" and quietly destroys the other 6. The next
    variance report then shows 6 bottles of phantom waste, and it is
    indistinguishable from real theft.

    So: truth requires the count to cover every location that item is known to
    live in. Known, here, means "somewhere we have counted it before" — which is
    evidence, not a guess, and it grows as the count history does.

    A narrower count is not thrown away. It is still recorded, as evidence with
    its scope on it; it just does not get to supersede.
    """
    seen = locations_ever_counted().get(item_id)
    if not seen:
        return None                        # never counted anywhere: this is day zero
    missing = seen - locations_counted
    if missing:
        return (f"{item_id} has been counted in {sorted(seen)}; this count covers "
                f"only {sorted(locations_counted)}. Missing {sorted(missing)} — "
                f"recording it as truth would delete whatever is in there.")
    return None


def locations_ever_counted() -> dict[str, set[str]]:
    """item_id -> every location a count has ever found it in."""
    out: dict[str, set[str]] = {}
    for r in read_all():
        if r["reason"] != "count":
            continue
        loc = (r.get("location") or "").strip()
        if loc:
            out.setdefault(r["item_id"], set()).add(loc)
    return out


def read_all() -> list[dict]:
    rows: list[dict] = []
    if not LEDGER_DIR.exists():
        return rows
    for path in sorted(LEDGER_DIR.glob("movements_*.csv")):
        with path.open() as f:
            rows.extend(csv.DictReader(f))
    return rows


def on_hand(as_at: str | None = None) -> dict[str, Decimal]:
    """item_id -> theoretical on-hand in base units. ONE GLOBAL POOL.

    Stowaway and Harry Gatos draw from the same physical stock (Zak,
    2026-08-15), so the balance is per ITEM, not per venue. A bottle is not in
    two places, and partitioning the balance by venue would invent a second one.

    `venue` stays on every row, because "which venue's sales depleted this" is a
    real and useful question — it is just not a question about how much is on
    the shelf. The same applies to `transfer`: moving stock between venues no
    longer changes any balance, so a transfer row is a record of a physical
    move, not an adjustment.

    A `count` row is TRUTH, not an adjustment: everything before it for that
    item is superseded, and the difference it books is the variance. That is
    what makes a count worth doing — and why a PARTIAL count must never be
    written as a count row. See count_scope_warning().
    """
    rows = [r for r in read_all() if not as_at or r["ts"] <= as_at]
    rows.sort(key=lambda r: r["ts"])

    last_count: dict[str, str] = {}
    for r in rows:
        if r["reason"] == "count":
            last_count[r["item_id"]] = r["ts"]

    bal: dict[str, Decimal] = {}
    for r in rows:
        item = r["item_id"]
        cut = last_count.get(item)
        if cut and r["ts"] < cut:
            continue                      # superseded by a later count
        q = Decimal(r["qty_base"])
        bal[item] = bal.get(item, Decimal(0)) + (q if r["direction"] == "in" else -q)
    return bal


def consumption_by_venue(as_at: str | None = None) -> dict[tuple[str, str], Decimal]:
    """(venue, item_id) -> base units consumed. The question venue DOES answer.

    Stock is one pool; usage is not. This is what says Harry Gatos went through
    twice the coriander Stowaway did, which is a rostering and menu question
    even though both hands reach into the same fridge.
    """
    out: dict[tuple[str, str], Decimal] = {}
    for r in read_all():
        if as_at and r["ts"] > as_at:
            continue
        if r["reason"] not in ("sale", "production", "waste"):
            continue
        if r["direction"] != "out":
            continue
        k = (r["venue"], r["item_id"])
        out[k] = out.get(k, Decimal(0)) + Decimal(r["qty_base"])
    return out
