"""
A row that states its own pack must not have the pack read out of its name.

THE GAP
-------
A `recipe-bridge-seed` row is a confirmed baseline. It records its pack in its
own columns ("1", "L") and its price is ALREADY per that pack. build_costs did
not read those columns: it called resolve_pack, which reads only the
DESCRIPTION, so the size in the name was applied a second time to a price that
had already been divided by it.

    Heinz BBQ Sauce [4L]       $3.475/L stated  -> recorded $0.87/L    4x UNDER
    Sunshine Smokey BBQ [3L]   $4.167/L stated  -> recorded $1.39/L    3x UNDER
    Milk Full Cream 2L         $1.75/L  stated  -> recorded $0.875/L   2x UNDER
    T2 Milk Bun [85g]          $11.53/kg stated -> recorded $135.64/kg 11.8x OVER

Large BBQ Chicken Pizza booked 61 ml of sauce at $0.085 instead of $0.254 and
published 82.8% GP with `fully_our_book: true`.

THE SECOND SYMPTOM, SAME ROOT
-----------------------------
`seed_conv[pid]` was set to `(1, "L")` verbatim. Invoices arrive in ml and g, so
that unit could never match and "L" is not a whole selling unit either — the
bridge emitted nothing and 118 invoice observations were silently dropped, every
affected product frozen on its January seed (Buffalo Trace [House] 18 dropped
across 12 recipes, Sailor Jerry [House] 13 across 9, Pizza Tomato Sauce 22).

WHAT THIS GUARDS
----------------
- a measurable stated pack (L, kg) is converted to base units and believed
- a CONTAINER ("box", "ea") is not: the price is per container and only the
  description says what is inside it — Barramundi states "1 box" at $83.00 and
  carries its 5 kg in the name, so taking the container literally is 5000x wrong
- a container seed still sets the seed's UNIT, or per-each invoices stop bridging
- ...but never a reference RATE, because a container seed is routinely a case
  price ($59.81 for one garlic bread) and arming the magnitude guard with it
  would refuse the correct invoice and keep the wrong seed
- kg<->g and L<->ml reconcile at the bridge; pack/each never converts to a base
"""

import csv
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from core.pack_overrides import load_pack_overrides                    # noqa: E402
from modules.recipes.pipeline.build_costs import (                     # noqa: E402
    build_seed_conv, stated_pack_in_base_units,
)

COGS = ROOT / "data" / "cogs_list.csv"


# --- reading a stated pack -------------------------------------------------

def test_a_measurable_pack_converts_to_the_unit_a_recipe_portions_in():
    assert stated_pack_in_base_units("1", "L") == (Decimal(1000), "ml")
    assert stated_pack_in_base_units("1", "kg") == (Decimal(1000), "g")
    assert stated_pack_in_base_units("2", "L") == (Decimal(2000), "ml")
    assert stated_pack_in_base_units("500", "ml") == (Decimal(500), "ml")


def test_a_container_is_not_a_measure():
    """Barramundi states "1 box" at $83.00 and carries the 5 kg in its NAME.
    Believing the container would publish $83/box against a 180 g portion."""
    assert stated_pack_in_base_units("1", "box") is None
    assert stated_pack_in_base_units("1", "ea") is None
    assert stated_pack_in_base_units("1", "bunch") is None


def test_a_missing_or_absurd_pack_is_refused():
    assert stated_pack_in_base_units("1", "") is None
    assert stated_pack_in_base_units("0", "L") is None
    assert stated_pack_in_base_units("-1", "kg") is None
    assert stated_pack_in_base_units("not a number", "L") is None


# --- what the seeds resolve to on the real file ---------------------------

def _seeds():
    rows = list(csv.DictReader(COGS.open(encoding="utf-8-sig")))
    overrides = load_pack_overrides(ROOT / "data" / "pack_overrides.yaml")
    return rows, build_seed_conv(rows, overrides)


def _families(rows):
    out = {}
    for r in rows:
        si = r.get("source_invoice") or ""
        if si.startswith(("bo-seed", "ls-recipe-seed", "bo-ingredient-seed",
                          "recipe-bridge-seed")):
            pid = f"lightspeed:{(r.get('supplier_code') or '').strip()}"
            out.setdefault(pid, set()).add(si)
    return out


def test_every_measurable_bridge_seed_is_held_in_a_base_unit():
    """(1, "L") could never meet an invoice in ml — this is the 118 dropped
    observations. The QUANTITY may legitimately come from elsewhere (see the
    next test); the UNIT must always be one a recipe can portion in."""
    rows, (seed_conv, _sp) = _seeds()
    bad = []
    for r in rows:
        if not (r.get("source_invoice") or "").startswith("recipe-bridge-seed"):
            continue
        pid = f"lightspeed:{(r.get('supplier_code') or '').strip()}"
        want = stated_pack_in_base_units(r.get("pack_qty"), r.get("pack_unit"))
        if want and (seed_conv.get(pid) or (None, None))[1] != want[1]:
            bad.append(f"{r['invoice_description']}: {seed_conv.get(pid)} not in {want[1]}")
    assert not bad, "bridge seed not in a base unit:\n  " + "\n  ".join(bad[:10])


def test_a_real_bottle_size_beats_the_bridge_seeds_price_basis():
    """"1 L" on a bridge seed means the confirmed price is PER LITRE. It is not
    a claim that the bottle holds a litre. Patron Silver, Kraken and Wolf Lane
    each carry one of these AND a bo-seed stating the real bottle, and the real
    bottle must win — the same rule as the per_L collision, decided by evidence
    rather than by which row happens to come last in the file."""
    rows, (seed_conv, _sp) = _seeds()
    for pid, size in (("lightspeed:20445825", Decimal(700)),    # Patron Silver
                      ("lightspeed:20445865", Decimal(700)),    # Kraken Rum
                      ("lightspeed:20445812", Decimal(500))):   # Wolf Lane Navy Gin
        assert seed_conv[pid] == (size, "ml"), f"{pid}: {seed_conv[pid]}"


def test_a_container_seed_keeps_its_unit_but_gets_no_reference_rate():
    """Garlic Bread is seeded $59.81 "ea" against a Gulli case of 40 (~$1.43
    each); Pizza Box Inserts $11.055 "ea" against a case of 100. The unit must
    survive so per-each invoices still bridge — 27 observations vanished when it
    did not — but the RATE must not, or the magnitude guard refuses the correct
    invoice and keeps the 40x seed."""
    rows, (seed_conv, seed_price) = _seeds()
    fams = _families(rows)
    checked = 0
    for r in rows:
        if not (r.get("source_invoice") or "").startswith("recipe-bridge-seed"):
            continue
        if stated_pack_in_base_units(r.get("pack_qty"), r.get("pack_unit")):
            continue                                    # measurable, covered above
        pid = f"lightspeed:{(r.get('supplier_code') or '').strip()}"
        if not (r.get("pack_unit") or "").strip():
            continue
        assert pid in seed_conv, f"{r['invoice_description']} lost its seed unit"
        # Another seed family may legitimately price this product (Edible Flower
        # has a bo-ingredient-seed at $11 a punnet, which IS a real per-punnet
        # price). Only the container bridge-seed itself must stay out.
        if fams[pid] - {r["source_invoice"]}:
            continue
        assert pid not in seed_price, (
            f"{r['invoice_description']} armed the magnitude guard with a "
            f"container 'per each' price")
        checked += 1
    assert checked, "fixture sanity: no container-only bridge seeds found"


def test_the_four_known_mispriced_seeds_now_read_their_stated_rate():
    """The regression, in the units the audit measured them in."""
    rows, (seed_conv, _sp) = _seeds()
    want = {                       # ProductID -> (stated rate, per base unit)
        "lightspeed:22989447": (Decimal("0.003475"), "ml"),   # Heinz BBQ, $3.475/L
        "lightspeed:22989451": (Decimal("0.0041667"), "ml"),  # Sunshine, $4.1667/L
        "lightspeed:22888650": (Decimal("0.00175"), "ml"),    # Milk, $1.75/L
        "lightspeed:22995947": (Decimal("0.0115294"), "g"),   # T2 bun, $11.5294/kg
    }
    by_pid = {}
    for r in rows:
        if (r.get("source_invoice") or "").startswith("recipe-bridge-seed"):
            by_pid[f"lightspeed:{(r.get('supplier_code') or '').strip()}"] = r
    for pid, (rate, unit) in want.items():
        r = by_pid.get(pid)
        assert r, f"{pid} is no longer a recipe-bridge-seed — update this test"
        qty, got_unit = seed_conv[pid]
        assert got_unit == unit
        got = Decimal(r["cost_per_unit_incl_gst"]) / qty
        assert abs(got - rate) < Decimal("0.000002"), f"{pid}: {got} != {rate}"
