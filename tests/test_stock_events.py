"""Counts and goods-received: what a person saw, turned into stock.

The event store keeps everything anyone recorded. The LEDGER only takes what is
provable. These tests hold that line, because every way of crossing it is
invisible once crossed:

  * "0.75 of a bottle" is not a quantity until somebody says how big the bottle
    is. Assuming 700 ml because most spirits are 700 ml is wrong on every future
    count of that item.
  * A count supersedes. Counting the bar while stock sits in the storeroom
    writes the storeroom off as phantom waste, and phantom waste cannot be told
    from theft.
"""

from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from ingest_stock_events import convert, load_containers      # noqa: E402
from ledger import load_base_units                            # noqa: E402

CONTAINERS = ROOT / "data" / "container_sizes.csv"
SQL = ROOT / "modules" / "inventory" / "stock_events.sql"

HAVANA = "lightspeed:21999746"          # "Havana 3yr [700ml]" — size in the name
CORIANDER = "lightspeed:20445580"       # "[bunch]" — no provable size


def ev(**kw):
    base = {"kind": "count", "item_id": HAVANA, "counted_qty": "1",
            "counted_unit": "bottle"}
    base.update(kw)
    return base


def test_a_bottle_fraction_becomes_millilitres():
    c, b = load_containers(), load_base_units()
    got = convert(ev(counted_qty="0.75"), c, b)
    assert not isinstance(got, str), got
    qty, unit = got
    assert (qty, unit) == (Decimal("525.00"), "ml"), (
        f"0.75 of a 700ml bottle is 525 ml; got {qty}{unit}")


def test_an_unknown_container_refuses_rather_than_assuming():
    c, b = load_containers(), load_base_units()
    got = convert(ev(item_id=CORIANDER, counted_unit="bunch", counted_qty="3"), c, b)
    assert isinstance(got, str), "a bunch has no provable size and must refuse"


def test_base_units_pass_straight_through():
    c, b = load_containers(), load_base_units()
    assert convert(ev(counted_unit="ml", counted_qty="30"), c, b) == (Decimal(30), "ml")
    assert convert(ev(counted_unit="l", counted_qty="1.5"), c, b) == (Decimal("1500.0"), "ml")


def test_a_negative_count_refuses():
    """Direction carries the sign. A negative count is an addition nobody meant."""
    c, b = load_containers(), load_base_units()
    got = convert(ev(counted_qty="-2"), c, b)
    assert isinstance(got, str) and "negative" in got


def test_counting_in_the_wrong_dimension_refuses():
    c, b = load_containers(), load_base_units()
    got = convert(ev(counted_unit="kg", counted_qty="1"), c, b)
    assert isinstance(got, str), "a millilitre item counted in kg must refuse"


def test_container_sizes_record_where_each_came_from():
    """The weakest source is the one most likely to be wrong, so a variance
    traced back to a name-derived size should be a suspect, not a mystery."""
    import csv
    assert CONTAINERS.exists(), "run scripts/build_container_sizes.py"
    with CONTAINERS.open() as f:
        rows = list(csv.DictReader(f))
    assert rows
    sources = {r["source"] for r in rows}
    assert sources <= {"pack_override", "product_name", "unit_is_each"}
    for r in rows:
        assert Decimal(r["base_qty"]) > 0
        assert r["base_unit"] in {"g", "ml", "each"}
        assert r["evidence"], f"{r['item_id']} has no evidence recorded"


def test_a_short_delivery_becomes_a_claim(tmp_path):
    """Ordered 3, got 2. That gap is a supplier credit, and it is the reason the
    goods-received check is the fact and the invoice only the second opinion."""
    events = [{"kind": "receive", "occurred_at": "2026-08-15T09:30:00+10:00",
               "venue": "stow", "item_id": HAVANA, "item_name": "Havana",
               "counted_qty": "2", "counted_unit": "bottle", "expected_qty": "3",
               "po_ref": "PO-1", "supplier_key": "ilg", "actor": "Steph"}]
    p = tmp_path / "e.json"
    p.write_text(json.dumps(events))
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "ingest_stock_events.py"),
                        "--file", str(p)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "supplier claims:      1" in r.stdout
    assert "ordered 3, received 2" in r.stdout


def test_a_partial_count_is_recorded_but_not_booked(tmp_path, monkeypatch):
    """The storeroom must survive somebody counting only the bar."""
    import ledger as L
    monkeypatch.setattr(L, "LEDGER_DIR", tmp_path)
    L.append([L.Movement(ts="2026-08-01", venue="stow", item_id=HAVANA,
                         qty_base="6", base_unit="each", direction="in",
                         reason="count", source_ref="stocktake:0", actor="Kris",
                         location="Storeroom - Bar", counted_qty="6",
                         counted_unit="bottle")])
    warn = L.count_scope_warning(HAVANA, {"Bar & Kegroom"})
    assert warn and "Storeroom - Bar" in warn


def test_the_sql_keeps_the_browser_out_of_the_secrets():
    """RLS is the whole reason a phone can write to this repo safely."""
    sql = SQL.read_text()
    assert "enable row level security" in sql
    assert "app_metadata" in sql, "the role must come from the JWT, not the client"
    assert "for delete" not in sql.lower(), (
        "nothing here is ever deleted — a wrong count is still a fact about what "
        "somebody saw")
