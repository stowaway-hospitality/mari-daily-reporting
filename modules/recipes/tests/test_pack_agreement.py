"""
No cost row may sit on an exact pack multiple of its own code's median.

WHAT THIS IS FOR
----------------
The ILG case/bottle defect was not a typo, it was a CLASS: a line whose price
counts one thing while its pack describes another. It hid for months because the
wrong number was self-consistent — $17.95/L is a perfectly ordinary-looking rate
— and because every ILG line was wrong the same way at the same time, so nothing
stood out against anything.

The signature is that a pack misread does not move a cost by a plausible amount.
It moves it by a WHOLE PACK FACTOR: 6x, 12x, 24x, whatever the carton holds.
Real price movement is a few percent and never lands on 6.000. So the same
supplier code, delivered week after week, is its own control — take the MEDIAN
of its deliveries and any member sitting within 1% of an exact pack multiple of
that median is a misread, not a price change.

The median, not the mean, precisely because when the ILG history was wrong it
was wrong together and a mean would have been dragged along with it.

This test asserts the SHAPE — "nothing sits on a pack factor" — and never a
count, because a count goes red the moment a finding is legitimately settled.

THE EXCEPTION LIST IS EMPTY, AND KEEPING IT IS THE POINT. It briefly held six
ILG invoices whose PDFs were not in data/invoice_corpus, so run.py could not
re-read them and they kept a per-bottle price against a case pack. They were
recovered from the accounts@ mailbox — every PDF's sha256 matching the
source_pdf its own invoice JSON already recorded — re-parsed, and the list
emptied. It stays here, empty, because the next unreadable invoice should have
to be written down by name rather than quietly widening a tolerance.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from check_pack_agreement import findings  # noqa: E402

COGS = ROOT / "data" / "cogs_list.csv"

# Invoices that cannot be re-read from data/invoice_corpus. Empty, and meant to
# stay that way — an entry here is a cost the book knows is wrong.
UNREACHABLE: set[str] = set()

pytestmark = pytest.mark.skipif(not COGS.exists(), reason="cogs_list.csv not built")


def _found():
    return findings(list(csv.DictReader(COGS.open(encoding="utf-8-sig"))))


def test_no_unexplained_line_sits_on_a_pack_factor():
    surprises = [f for f in _found() if f["invoice"] not in UNREACHABLE]
    assert not surprises, "\n".join(
        f"{f['supplier']} {f['code']} {f['description']} — {f['invoice']} "
        f"{f['date']} reads {f['rate']} against a median {f['median']} "
        f"per {f['unit']}, exactly {f['factor']}x {f['direction']}"
        for f in surprises)


def test_the_king_brown_punnet_stays_fixed():
    """MKB500PUNN was 4x under on INB00099549 — a 200 g punnet read as 800 g off
    a wrapped description. Re-break the UOM precedence and this reds."""
    assert not [f for f in _found() if f["code"] == "MKB500PUNN"]


def test_every_remaining_finding_is_a_known_unreachable_invoice():
    """The exception list is exact, not a blanket. A finding on any OTHER
    invoice means a re-parse missed one."""
    for f in _found():
        assert f["invoice"] in UNREACHABLE, (
            f"{f['supplier']} {f['code']} on {f['invoice']} is not a known "
            f"unreachable invoice — {f['factor']}x {f['direction']}")


def test_the_six_recovered_ilg_invoices_stay_fixed():
    """They carried a per-bottle price against a case pack and read ~6x under —
    Buffalo Trace at $12.65/L against a $76 median. Recovered from accounts@ and
    re-parsed. Re-break one_unit_pack and these red."""

    rows = list(csv.DictReader(COGS.open(encoding="utf-8-sig")))
    recovered = {"03739295", "03739296", "03739297", "03741446", "03741447", "03741448"}
    seen = [r for r in rows if r.get("source_invoice") in recovered]
    assert seen, "the recovered invoices are not in the cost book at all"
    for r in seen:
        note = (r.get("note") or "")
        pq = (r.get("pack_qty") or "").strip()
        # a "Nx<size>" note means the pack must be ONE inner unit, not the case
        import re as _re
        m = _re.match(r"^\s*(\d+)\s*[xX]\s*(\d+(?:\.\d+)?)\s*(ML|LTR|LT|L)\s*\.?\s*$",
                      note, _re.I)
        if not m or int(m.group(1)) <= 1 or not pq:
            continue
        per, size, u = int(m.group(1)), float(m.group(2)), m.group(3).lower()
        one = size / 1000 if u == "ml" else size
        assert abs(float(pq) - one) < 1e-6, (
            f"{r['source_invoice']} {r['supplier_code']}: pack {pq} is not one "
            f"unit ({one}) of {note}")


def test_the_detector_can_actually_fire():
    """A detector that cannot fire proves nothing. Feed it a planted 6x misread
    against a steady code and it must name it."""
    rows = [{"supplier": "T", "supplier_code": "C", "pack_unit": "L",
             "cost_per_base_unit": v, "source_invoice": f"i{i}",
             "invoice_date": "2026-01-01", "invoice_description": "thing"}
            for i, v in enumerate(["60.00", "60.00", "60.00", "10.00"])]
    got = findings(rows)
    assert len(got) == 1 and got[0]["factor"] == 6 and got[0]["direction"] == "UNDER"
