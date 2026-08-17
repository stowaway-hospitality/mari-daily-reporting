"""No published day may claim an impossible amount of GST.

WHAT HAPPENED. Lightspeed emails two product-export shapes. Shape A carries a
`Total Tax` column; shape B (Position / Product Number / Sale Amount) carries
none. daily_aggregator formed the day's GST by summing that column raw, so
every shape-B row counted as $0.00 of tax. Because ex-GST revenue is
(inc - tax), missing tax does not lose revenue — it INVENTS it.

Two ways it surfaced, both found on 2026-08-17:

  * Harry Gatos 2026-08-10. HG's own export is shape A, but the ~$1,208 of HG
    food that rings through the STOW till is reallocated in from the Stow file,
    which arrived that day in shape B. The day summed $3,740.00 inc against
    $230.03 of tax and published $3,509.97 ex — a 6.15% effective rate on a
    9.09% day, overstating Harry Gatos by $109.82. MONDAYS are the exposed day:
    Stow is shut, so nearly all HG food crosses on the Stow till.

  * Stowaway 2026-08-13, worse. The whole export was shape B, so the day
    published $5,269.05 ex on $5,267.67 inc — ex-GST revenue HIGHER than
    inc-GST, an implied tax of MINUS $1.38. No GST came off at all.

WHY A DATA TEST AND NOT A UNIT TEST. daily_aggregator.py does its work at
import time, so row_tax() cannot be imported and called in isolation. That is
not worth a refactor here: the property that actually matters is a property of
the PUBLISHED numbers, and asserting it directly catches any future route to
the same wrong answer, not just this one. It would have caught both days above.

THE BAND. GST is 1/11 of an inc-GST price, so 9.0909% is the ceiling and no day
can exceed it by more than rounding. The floor is 8.0%: genuinely GST-free
lines exist (Online Surcharge, Online Discount, several add-ons) and pull a
real day slightly under, but nothing legitimate drags it to 6% or 0%. Across
the 116 published venue-days on 2026-08-17 the median sat at 9.089%.

Tiny days are exempt (inc <= $100): Marilyna's rings days of a few dollars off
the Stow till, where a single rounded cent is worth more than 1%.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

GST_RATE_PCT = 100.0 / 11.0        # 9.0909... — the statutory ceiling
FLOOR_PCT = 8.0
CEILING_PCT = 10.0                 # rate cannot exceed statutory; slack for
                                   # EatClub/rounding adjustments to ex
MATERIAL_INC = 100.0


def _published_days():
    for f in sorted(DATA.glob("*_daily_2026-*.json")):
        try:
            sales = json.loads(f.read_text())["sales"]
        except Exception:
            continue
        inc, ex = sales.get("revenue_inc_gst"), sales.get("revenue_ex_gst")
        if inc is None or ex is None or inc <= 0:
            continue
        yield f.name, float(inc), float(ex)


def test_there_are_published_days_to_check():
    assert sum(1 for _ in _published_days()) > 50, "fixtures missing — data/ is empty?"


def test_ex_gst_never_exceeds_inc_gst():
    """The hard one. Stowaway 2026-08-13 published ex ABOVE inc — negative GST.

    There is no arrangement of discounts, give-aways or GST-free lines that
    makes a day's ex-GST revenue larger than its inc-GST revenue. If this fails,
    tax is being ADDED to revenue somewhere.
    """
    bad = [(n, inc, ex) for n, inc, ex in _published_days() if ex > inc + 0.01]
    assert not bad, "ex-GST revenue exceeds inc-GST (negative tax):\n" + "\n".join(
        f"  {n}: inc ${inc:,.2f} < ex ${ex:,.2f}  (tax ${inc-ex:,.2f})" for n, inc, ex in bad)


def test_implied_gst_rate_is_plausible():
    """Harry Gatos 2026-08-10 sat at 6.15% because reallocated shape-B rows
    counted as tax-free. Anything outside 8-10% is a tax-basis failure, not a
    trading pattern."""
    bad = []
    for n, inc, ex in _published_days():
        if inc <= MATERIAL_INC:
            continue
        rate = (inc - ex) / inc * 100.0
        if not (FLOOR_PCT <= rate <= CEILING_PCT):
            bad.append((n, inc, ex, rate))
    assert not bad, (
        "implied GST rate outside 8-10% — a day is being taxed on the wrong "
        "basis (see tests/test_gst_basis.py):\n" + "\n".join(
            f"  {n}: {r:.2f}%  inc ${inc:,.2f} ex ${ex:,.2f}"
            for n, inc, ex, r in sorted(bad)))


def test_no_day_can_beat_the_statutory_rate():
    """A sanity bound in the other direction: more than 1/11 of the inc price
    cannot be GST. A little slack for EatClub give-away, which reduces ex."""
    bad = []
    for n, inc, ex in _published_days():
        if inc <= MATERIAL_INC:
            continue
        rate = (inc - ex) / inc * 100.0
        if rate > GST_RATE_PCT + 1.0:
            bad.append((n, inc, ex, rate))
    assert not bad, "implied GST above the statutory 9.09% by more than 1pt:\n" + "\n".join(
        f"  {n}: {r:.2f}%  inc ${inc:,.2f} ex ${ex:,.2f}" for n, inc, ex, r in sorted(bad))
