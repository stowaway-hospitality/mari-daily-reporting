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

from check_pack_agreement import book_findings, findings  # noqa: E402

COGS = ROOT / "data" / "cogs_list.csv"
COSTS = ROOT / "data" / "costs.csv"

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


def test_the_book_itself_has_no_pack_factor_outlier():
    """PASS 2, against data/costs.csv — the rate a recipe actually costs off.

    Pass 1 reads the invoice; this reads the book, and they are not the same
    test. Foodlink BEANS BLACK WHOLE TIN A10 sat at $0.0174/g on one delivery
    between two at $0.0029/g — a CTN-6 carton divided by one tin — and pass 1
    could not see it, because the misread had moved the pack_unit too and filed
    the outlier apart from its own siblings.

    Only an outlier IN TIME counts: the median must be observed both before and
    after it, so a genuine price change (coriander $15.40 -> $7.70 at the end of
    May, and never back) is not reported as a 2x misread."""
    if not COSTS.exists():
        pytest.skip("costs.csv not built")
    found = book_findings(list(csv.DictReader(COSTS.open(encoding="utf-8-sig"))))
    assert not found, "\n".join(
        f"{f['code']} {f['description']} — {f['date']} reads {f['rate']} against "
        f"a median {f['median']} per {f['unit']}, exactly {f['factor']}x "
        f"{f['direction']} (invoice {f['invoice']})" for f in found)


def test_the_book_pass_can_actually_fire():
    """Same discipline as pass 1: plant a 6x outlier BETWEEN two good
    observations and it must be named. Plant it at the END and it must not — that
    is a price change, not a misread."""
    def row(ing, when, rate):
        return {"ingredient": ing, "observed_on": when, "cost_per_unit": rate,
                "unit": "g", "source_invoice": f"i{when}", "description": "thing"}
    sandwiched = [row("t:1", "2026-01-01", "0.0029"), row("t:1", "2026-02-01", "0.0174"),
                  row("t:1", "2026-03-01", "0.0029")]
    got = book_findings(sandwiched)
    assert len(got) == 1 and got[0]["factor"] == 6 and got[0]["direction"] == "OVER"

    regime = [row("t:2", "2026-01-01", "0.0029"), row("t:2", "2026-02-01", "0.0029"),
              row("t:2", "2026-03-01", "0.0174")]
    assert not book_findings(regime), "a price change was reported as a misread"


def test_the_detector_can_actually_fire():
    """A detector that cannot fire proves nothing. Feed it a planted 6x misread
    against a steady code and it must name it.

    The dates used to be identical and the bad row last, which stopped meaning
    anything on 2026-08-14 when pass 1 gained the regime test pass 2 already had:
    a rate at the END of a series is a price that changed, not an outlier. Dated
    so the misread sits BETWEEN good deliveries, which is the shape the detector
    is actually for."""
    rows = [{"supplier": "T", "supplier_code": "C", "pack_unit": "L",
             "cost_per_base_unit": v, "source_invoice": f"i{i}",
             "invoice_date": d, "invoice_description": "thing"}
            for i, (v, d) in enumerate([("60.00", "2026-01-01"),
                                        ("60.00", "2026-01-02"),
                                        ("10.00", "2026-01-03"),   # <- the misread
                                        ("60.00", "2026-01-04")])]
    got = findings(rows)
    assert len(got) == 1 and got[0]["factor"] == 6 and got[0]["direction"] == "UNDER"


# --------------------------------------------------------------------------
# The guard must stay testable after the book is clean
# --------------------------------------------------------------------------

def test_the_scan_still_bites_on_a_planted_case_priced_as_a_bottle():
    """A FIXTURE, not the live book — and that is the whole point.

    On 2026-08-14 the real data reached "no line sits on a whole pack factor of
    its own code's median (4201 rows)", which is the goal. But it also meant the
    mutation `PACK_FACTORS`/`TOL` -> "tolerance too tight to ever match" produced
    the SAME empty result and survived: with nothing left to find, a working guard
    and a dead one are indistinguishable from outside.

    So the guard gets its own defect to catch, one that cannot be fixed away. Six
    deliveries of one code at $0.0739/ml (a 700 mL bottle) and a seventh at
    $0.0123/ml — exactly 6x low, which is the case/bottle error this file exists
    for. Re-break the tolerance and this reds even when the book is spotless.
    """
    # DATED, and the dates are load-bearing: the bad delivery sits in the MIDDLE
    # of six good ones, because that is what makes it a misread rather than a
    # price change. _outlier_in_time() requires the median either side of it.
    rows = [{"supplier": "ILG", "supplier_code": "285-0409", "pack_unit": "ml",
             "cost_per_base_unit": "0.073900", "source_invoice": f"INV{i}",
             "invoice_date": f"2026-04-{i + 1:02d}",
             "invoice_description": "VEUVE CLICQUOT NV 750ML"} for i in range(6)]
    rows.append({"supplier": "ILG", "supplier_code": "285-0409", "pack_unit": "ml",
                 "cost_per_base_unit": "0.012317",          # 0.0739 / 6
                 "source_invoice": "INVBAD", "invoice_date": "2026-04-04",
                 "invoice_description": "VEUVE CLICQUOT NV 750ML"})
    got = findings(rows)
    assert got, "a 6x outlier against six agreeing siblings must be reported"
    bad = [f for f in got if f["invoice"] == "INVBAD"]
    assert bad, [f["invoice"] for f in got]
    assert bad[0]["factor"] == 6
    assert bad[0]["direction"] == "UNDER", "6x LOW is the flattering direction"


def test_a_group_that_simply_moved_price_is_not_reported():
    """The other half of the contract: ordinary drift must stay silent, or the
    fixture above would pass with a guard that reports everything."""
    rows = [{"supplier": "ILG", "supplier_code": "285-0409", "pack_unit": "ml",
             "cost_per_base_unit": r, "source_invoice": f"INV{i}",
             "invoice_date": f"2026-04-{i + 1:02d}",
             "invoice_description": "VEUVE CLICQUOT NV 750ML"}
            for i, r in enumerate(("0.0739", "0.0742", "0.0751", "0.0768", "0.0777"))]
    assert not findings(rows), findings(rows)
