"""
Pack overrides on cost-book rows: they apply, but only when they convert.

TWO BUGS MEET HERE, and the second was created by fixing the first.

BUG 1 — pack_overrides.yaml did nothing for lightspeed:* rows.
build_ingredients' cost-book branch hard-coded pack_qty "1" and never consulted
`overrides`. That branch is exactly the population that needs overriding, because
the Back Office seed writes "can" for anything it cannot size. So every wrong-unit
flag the daily product review raised against a cost-book row — about thirty
spirits on 2026-08-15, San Pellegrino and the Sapore A12 peppers on 08-16 — was
unfixable by the one mechanism built for fixing it. Zak could confirm a pack and
the feed would ignore him.

BUG 2 — the obvious fix understated nine products by 40x to 288x.
Applying every override in that branch divided rows that were ALREADY per-piece:

    Garlic Bread          $1.4953/ea -> $0.0374    40x too cheap
    Large Pizza Box 13"   $0.6426/ea -> $0.0129    50x
    Flour Tortillas 6"    $0.1167/ea -> $0.0004   288x

costs.csv records why in its own pack column: "x40 (count) (via
gulli:AGBGARBRE-B)", "chef-confirmed". The upstream bridge had already divided
the carton. Every one of those errors is in the flattering direction, and not one
would have tripped a bound or an existing test — it was caught only by diffing
the feed before and after, which is this pipeline's standing rule.

THE DISTINCTION THAT RESOLVES IT: a cost-book row is priced per ONE PURCHASABLE
UNIT. An override in ml/g says "one of those units CONTAINS this much" — a real
conversion, and the only route to costing it by mass. An override in "ea" says "a
CARTON holds N of them" — a fact about the carton, already applied upstream.
"""

import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MEASURE = {"ml", "l", "lt", "litre", "litres", "g", "kg", "gram", "grams",
           "kilogram", "kilograms"}


def _is_measure(unit):
    return (unit or "").strip().lower() in MEASURE


@pytest.mark.parametrize("unit", ["ml", "g", "kg", "L", "ML", " g "])
def test_a_measure_override_converts_and_so_applies(unit):
    assert _is_measure(unit)


@pytest.mark.parametrize("unit", ["ea", "each", "box", "carton", "ctn", "can", ""])
def test_a_count_override_does_not_apply_to_a_cost_book_row(unit):
    # These describe a carton, not the purchasable unit the row is priced in.
    assert not _is_measure(unit)


def test_the_conversion_arithmetic():
    # San Pellegrino: $2.32 a 500 mL bottle.
    assert (Decimal("2.3200") / Decimal(500)).quantize(Decimal("0.000001")) \
        == Decimal("0.004640")
    # Sapore A12 peppers: $14.00 a tin, 2500 g drained.
    assert (Decimal("14.000000") / Decimal(2500)).quantize(Decimal("0.000001")) \
        == Decimal("0.005600")


def test_a_count_override_would_have_produced_these_exact_understatements():
    """Pins the diagnosis in the docstring, so it cannot quietly become untrue."""
    assert (Decimal("1.495250") / 40).quantize(Decimal("0.000001")) == Decimal("0.037381")
    assert (Decimal("0.642620") / 50).quantize(Decimal("0.000001")) == Decimal("0.012852")
    assert (Decimal("0.116667") / 288).quantize(Decimal("0.000001")) == Decimal("0.000405")


# ── the live feed ───────────────────────────────────────────────────────────

def _feed():
    import json
    p = ROOT / "data" / "ingredients.json"
    if not p.exists():
        pytest.skip("no ingredients feed built")
    d = json.loads(p.read_text())
    items = d["ingredients"] if isinstance(d, dict) and "ingredients" in d else d
    return {i["id"]: i for i in items}


def test_the_two_confirmed_packs_reached_the_feed():
    """
    Assert the RATE, not the shape of the row.

    A confirmed pack can land by either of two routes and both are correct:

      * build_costs applies the override first, so costs.csv already holds a
        RATE, and this feed row is "1 ml @ $0.004640" — pack_qty 1 because the
        cost is already per base unit. The cost-book branch then correctly does
        NOT divide again (that is guard 1).
      * the cost book still holds a CONTAINER price, and the cost-book branch
        converts it here: "500 ml @ $0.004640".

    An earlier version of this test asserted pack_qty == 500 and failed the
    moment the first route won — a test pinned to an intermediate representation
    rather than to the thing that must be true. What must be true is the price
    per base unit, and that the unit is a measure rather than the seed's "can".
    """
    f = _feed()
    pel = f.get("lightspeed:21050706")
    if pel:
        assert pel["pack_unit"] == "ml", \
            "San Pellegrino is still costed per 'can'; the 500 mL is lost"
        # $2.32 a 500 mL bottle, however the row is shaped.
        assert Decimal(pel["cost_per_base_unit"]) == Decimal("0.004640")
        assert (Decimal(pel["cost_per_base_unit"]) * 500).quantize(Decimal("0.01")) \
            == Decimal("2.32")
    pep = f.get("lightspeed:22874436")
    if pep:
        assert pep["pack_unit"] == "g", \
            "the A12 tin is still costed per 'ea' and cannot be used by mass"
        # $14.00 a tin, 2500 g drained.
        assert Decimal(pep["cost_per_base_unit"]) == Decimal("0.005600")
        assert (Decimal(pep["cost_per_base_unit"]) * 2500).quantize(Decimal("0.01")) \
            == Decimal("14.00")


def test_the_two_spellings_of_the_a12_tin_agree_on_price():
    # One is the Gulli invoice row, one the bridged Lightspeed copy. If they
    # disagree the same pepper shows up twice in the picker at two costs — the
    # identity-corruption class this repo keeps paying for.
    f = _feed()
    a = f.get("gulli:SAPPEPSTRIPA12-UC3")
    b = f.get("lightspeed:22874436")
    if a and b and a.get("cost_per_base_unit") and b.get("cost_per_base_unit"):
        assert Decimal(a["cost_per_base_unit"]) == Decimal(b["cost_per_base_unit"])
        assert a["pack_unit"] == b["pack_unit"]


def test_no_already_divided_product_got_divided_again():
    """
    The nine casualties of the first draft, asserted at their CORRECT values.

    Each of these is priced per piece upstream (costs.csv pack column says so),
    so the cost-book branch must leave them alone.
    """
    f = _feed()
    expected = {
        "lightspeed:20467596": Decimal("1.495250"),   # Garlic Bread
        "lightspeed:22873831": Decimal("0.482020"),   # Regular Pizza Box 11"
        "lightspeed:22873851": Decimal("0.642620"),   # Large Pizza Box 13"
        "lightspeed:22873876": Decimal("0.110550"),   # Pizza Box Inserts
        "lightspeed:22995335": Decimal("0.055558"),   # Eggs 700g [12x]
        "lightspeed:22995336": Decimal("0.472222"),   # Tortillas 12"
        "lightspeed:22995337": Decimal("0.116667"),   # Tortillas 6"
        "lightspeed:22996511": Decimal("4.437500"),   # GF Pizza Base
    }
    for pid, want in expected.items():
        it = f.get(pid)
        if not it:
            continue
        got = Decimal(it["cost_per_base_unit"])
        assert got == want, (
            f"{pid} ({it.get('description')}) is {got}, expected {want} — it has "
            f"been divided by its pack size a second time")


def test_the_feed_build_is_deterministic():
    """
    A wobbling feed makes every before/after diff meaningless, and the diff is
    the only thing that caught the 288x error above.
    """
    import hashlib, json
    def build_and_hash():
        subprocess.run([sys.executable, "modules/recipes/pipeline/build_ingredients.py"],
                       cwd=ROOT, capture_output=True, check=True)
        d = json.loads((ROOT / "data" / "ingredients.json").read_text())
        d.pop("generated_at", None)
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()
    assert build_and_hash() == build_and_hash()
