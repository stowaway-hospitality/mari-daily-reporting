"""A can the bar sells as bought should cost what the invoice says, not Back Office.

WHY THE RESCUE EXISTS. `seed_matched_liquor_cost` puts a sizeless liquor line back
into the book by forming the case reading and the bottle reading and keeping
whichever agrees with the product's seed. It needs a seed expressed PER BASE UNIT
— per ml — because that is what it divides by. `build_seed_conv` produces one via
`stated_pack_in_base_units()`, which returns nothing for a container word.

So a product Lightspeed holds per CAN has no seed price at all, the rescue cannot
fire, and the line is dropped as "pack unreadable". 77 of 104 ILG codes were
missing from the cost book for that reason, nine of them the cans and bottles the
bar sells as bought — Peroni, Asahi, Two Bays, Young Henrys, Monteith's, Fellr,
Better Beer. Every one has a readable pack on the invoice and a real price, and
every one fell back to the Back Office cost instead.

THE ARGUMENT FOR NO CONVERSION. Back Office holds the product per can, the invoice
line is one can, so the invoice's own unit price IS the cost. The only question is
whether the line is one can or a CASE, and the band answers it: a 6-pack lands at
6x the seed and a carton at 24x, both refused. Peroni proves the two sides are the
same number — Back Office says $2.5217 and ILG 115-4173 invoices $2.5217,
identical to four decimals across four invoices.

THE RAIL MATTERS MORE THAN THE FEATURE. A countable SEED is not enough: brown
onion carries a per-"can" Back Office seed of $1.54 that actually means $1.54 a
KILO. The first cut matched it at 1.00x and emitted "one unit"; the number came
out right only because a chef-confirmed 1000 g override converted it afterwards.
On a product without that override it would have been a unit error. So anything
either source calls MEASURED is refused.
"""

from __future__ import annotations

import csv
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.recipes.pipeline.build_costs import seed_matched_countable_cost  # noqa: E402

COSTS = ROOT / "data" / "costs.csv"
pytestmark = pytest.mark.skipif(not COSTS.exists(), reason="costs.csv not built")

MARK = "seed-matched one unit"


def _rescued():
    return [r for r in csv.DictReader(COSTS.open(encoding="utf-8-sig"))
            if MARK in (r.get("pack") or "")]


# --- the judgement itself, on fixtures -------------------------------------

def test_one_can_against_the_seed_is_accepted():
    got = seed_matched_countable_cost("2.5471", "2.1800")      # Asahi, 1.17x
    assert got is not None and got[1] == "ea"
    assert got[0] == Decimal("2.547100")


def test_a_six_pack_priced_as_one_can_is_refused():
    """The whole reason for the band. 6 x $2.52 against a $2.52 seed is 6x."""
    assert seed_matched_countable_cost("15.13", "2.5217") is None


def test_a_carton_is_refused_too():
    assert seed_matched_countable_cost("60.52", "2.5217") is None


def test_nothing_is_invented_without_a_seed():
    assert seed_matched_countable_cost("2.52", None) is None
    assert seed_matched_countable_cost("2.52", 0) is None


# --- and on the real book ---------------------------------------------------

def test_the_rescue_only_ever_emits_a_countable_unit():
    """The rail. If this ever emits g or ml, a measured good has slipped through
    and is being priced as though it were bought by the piece."""
    bad = [(r["ingredient"], r["unit"], r["description"][:40])
           for r in _rescued() if r["unit"] not in ("ea", "can")]
    assert not bad, bad


def test_the_measured_goods_are_refused_by_name():
    """Brown onion is the case that taught this. It carries a per-'can' seed and is
    sold by the kilo; it must not appear in the rescued set."""
    assert not [r for r in _rescued() if r["ingredient"] == "lightspeed:22995352"]


def test_peroni_costs_what_ilg_invoiced():
    """The exact-match case: Back Office $2.5217, ILG 115-4173 $2.5217."""
    rows = [r for r in csv.DictReader(COSTS.open(encoding="utf-8-sig"))
            if r["ingredient"] == "lightspeed:20445700"]
    assert rows, "Peroni has no cost rows"
    inv = [r for r in rows if not r["source_invoice"].startswith(
        ("bo-", "ls-", "recipe-", "invoice-derived"))]
    assert inv, "Peroni is still priced from Back Office — the bridge is not landing"
    assert float(max(inv, key=lambda r: r["observed_on"])["cost_per_unit"]) == pytest.approx(
        2.5217, rel=1e-4)


def test_every_rescued_price_sits_within_the_band_of_its_seed():
    """Belt and braces on the live data: nothing accepted is more than 3x from the
    Back Office figure it was judged against."""
    seeds = {}
    cogs = ROOT / "data" / "cogs_list.csv"
    for r in csv.DictReader(cogs.open(encoding="utf-8-sig")):
        if (r.get("supplier") or "") == "Lightspeed" and str(
                r.get("source_invoice") or "").startswith(("bo-seed", "bo-ingredient-seed")):
            try:
                seeds["lightspeed:" + (r.get("supplier_code") or "").strip()] = float(
                    r["cost_per_unit_incl_gst"])
            except (ValueError, KeyError, TypeError):
                pass
    for r in _rescued():
        s = seeds.get(r["ingredient"])
        if not s:
            continue
        ratio = float(r["cost_per_unit"]) / s
        assert 1 / 3 <= ratio <= 3, (r["ingredient"], r["description"][:34], ratio)
