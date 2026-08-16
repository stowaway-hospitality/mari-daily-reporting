"""Patron Silver is the tripwire for pack-basis regressions.

History: feeding ILG's own pack_qty/pack_unit straight into the resolver once
dropped Patron Silver from $2.94 to $0.61 a pour — a 4.8x under-cost, because
ILG prices some lines per case and some per bottle with no field saying which.
It was caught by a failing test and reverted (HANDOFF_20260809_ilg.md).

The declared-conversion layer (COST_BOOK_ARCHITECTURE_PLAN.md, next cost-book
task) touches exactly this ground, so the plan's instruction is explicit:
"write the Patron regression test first." This is that test.

Live rate at pinning (2026-08-16): 0.109443/ml, invoice 03744948, seed-matched.
The band is generous — it exists to catch basis errors (4.8x drops, 6-12x
case-read-as-bottle spikes), not price moves.
"""
from __future__ import annotations

from datetime import date

from core.domain import CostSeries, load_cost_observations

BAND = (0.08, 0.16)          # $/ml — a real Patron price lives here
PATRON_IDS = [
    "lightspeed:20445825",   # Patron Silver [House] lineage (invoice-fed)
    "lightspeed:20487926",   # Patron Silver 700ML, stowaway
    "lightspeed:20744467",   # Patron Silver 700ML, harry_gatos
]


def test_patron_silver_rate_stays_in_band():
    s = CostSeries(load_cost_observations())
    hits = 0
    for pid in PATRON_IDS:
        try:
            o = s.as_of(pid, date.today())
        except LookupError:
            continue
        hits += 1
        assert o.unit == "ml", f"{pid}: unit {o.unit!r} — a pack basis leaked in"
        rate = float(o.cost_per_unit)
        assert BAND[0] <= rate <= BAND[1], (
            f"{pid}: {rate:.6f}/ml outside {BAND} — the same failure shape that "
            f"took Patron to $0.61/pour. Check case-vs-bottle basis before "
            f"trusting any conversion change.")
    assert hits >= 2, "Patron Silver series went missing entirely"
