"""
A stated bottle size beats a price basis that only names the unit.

THE GAP
-------
`seed_conv[pid]` does double duty. It is the seed's UNIT ("this product is
costed per ml") and it is the DIVISOR that turns a whole-bottle invoice price
into that unit ("$17.56 a bottle / 750 ml"). Two different seed rows can set it,
and the loop kept whichever came last in the file.

cogs_list.csv is sorted oldest-first. The `bo-seed` family is dated 2026-01-01
and carries a real size in its description; the `ls-recipe-seed` family is dated
2026-01-02 and carries `basis = per_L`. resolve_pack must return 1000 for a
per-litre basis — that is what "per litre" means — so every wine, spirit, bitters
bottle and keg in the book lost its stated size to a 1000 it never claimed:

    2026-01-01  20655236  Geppetto Pinot Noir 750ML   $17.5608  basis ''      bo-seed
    2026-01-02  20655236  Geppetto Pinot Noir-Bottle  $23.4000  basis 'per_L' ls-recipe-seed

A bottle invoice was then divided by 1000 instead of 750 and costs.csv recorded
$0.017560/ml against a true $0.023413/ml — 25% under, on wine by the glass.
The ratio is exactly 0.75, which sits inside the magnitude guard's 0.1-10 band,
so nothing refused it. Published GP for Version Two Pinot Grigio read 87.9%
against a true 83.8%.

96 seeded ProductIDs carried a stated size the per_L row contradicted, 26 of them
bridged. The error runs both ways: 0.15x on 150 ml bitters (6.7x UNDER) through
to 50x on a 50 L keg (which the magnitude guard then refused outright, so keg
invoices never reached the book at all).

WHAT THIS GUARDS
----------------
- a stated size wins over a basis-derived 1000, whichever row is newer
- ...but only in the SAME base unit: a per_L row is still what gives a product
  a per-ml basis when its only other reading is a countable "bottle"
- a genuine 1 L bottle is unaffected — both readings say 1000
- last-row-wins is preserved WITHIN a tier, so no other product moves
- and the invariant on the real file: every seeded pack equals the size the
  Back Office export stated for that product
"""

import csv
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from core.pack_overrides import load_pack_overrides                     # noqa: E402
from modules.recipes.pipeline.build_costs import (                      # noqa: E402
    PACK_FROM_BASIS, PACK_STATED, better_seed_pack, build_seed_conv, pack_evidence,
)

COGS = ROOT / "data" / "cogs_list.csv"


# --- how a pack reading is classified -------------------------------------

def test_a_price_basis_is_the_weakest_evidence_of_a_bottles_size():
    """"per L (invoice)" means the PRICE is quoted per litre. It is a
    measurement denominator, not a claim that the bottle holds a litre."""
    assert pack_evidence("per L (invoice)") == PACK_FROM_BASIS
    assert pack_evidence("per kg (invoice)") == PACK_FROM_BASIS


def test_a_size_read_off_the_description_is_stated_evidence():
    assert pack_evidence("750ml") == PACK_STATED
    assert pack_evidence("6x700ml") == PACK_STATED
    assert pack_evidence("chef-confirmed") == PACK_STATED
    assert pack_evidence("code:punnet") == PACK_STATED
    assert pack_evidence("") == PACK_STATED


# --- the decision ----------------------------------------------------------

def _stated(q, u):
    return (Decimal(q), u, "750ml" if u == "ml" else "stated")


def _basis(q, u):
    return (Decimal(q), u, "per L (invoice)" if u == "ml" else "per kg (invoice)")


def test_the_stated_bottle_size_survives_a_later_per_litre_row():
    """Geppetto Pinot Noir. This is the whole finding."""
    keep = better_seed_pack(_stated(750, "ml"), _basis(1000, "ml"))
    assert keep[0] == Decimal(750)


def test_it_survives_regardless_of_which_row_came_first():
    """The fix must be about specificity, not file order — otherwise it only
    works while cogs_list.csv happens to be sorted the way it is today."""
    assert better_seed_pack(_basis(1000, "ml"), _stated(750, "ml"))[0] == Decimal(750)
    assert better_seed_pack(_stated(750, "ml"), _basis(1000, "ml"))[0] == Decimal(750)


def test_a_genuine_one_litre_bottle_is_untouched():
    """Many products really are 1 L and 1000 is right for them. Both readings
    agree, so there is nothing for this rule to do."""
    assert better_seed_pack(_stated(1000, "ml"), _basis(1000, "ml"))[0] == Decimal(1000)


def test_the_error_runs_both_ways_so_the_rule_is_not_a_minimum():
    """A 50 L keg and a 150 ml bottle of bitters fail in opposite directions.
    Keeping "the smaller number" would fix the wine and break the keg."""
    assert better_seed_pack(_stated(50000, "ml"), _basis(1000, "ml"))[0] == Decimal(50000)
    assert better_seed_pack(_stated(150, "ml"), _basis(1000, "ml"))[0] == Decimal(150)


def test_a_basis_row_still_wins_when_it_is_the_only_reading_in_that_unit():
    """A per_bottle seed resolves to a countable (1, "bottle") — a COUNT, not a
    size. The per_L row is what gives that product a per-ml basis at all, and a
    recipe portioning 30 ml can only read a per-ml cost. Narrowing the rule to
    one base unit is what keeps this working."""
    keep = better_seed_pack((Decimal(1), "bottle", "bottle"), _basis(1000, "ml"))
    assert (keep[0], keep[1]) == (Decimal(1000), "ml")


def test_last_row_still_wins_between_two_readings_of_equal_specificity():
    """Everything outside the basis-vs-stated collision must behave exactly as
    it did, or costs.csv moves for products this finding never touched."""
    assert better_seed_pack(_stated(700, "ml"), _stated(750, "ml"))[0] == Decimal(750)
    assert better_seed_pack(_basis(1000, "g"), _basis(1000, "g"))[0] == Decimal(1000)


def test_the_first_reading_is_taken_when_there_is_nothing_to_compare():
    assert better_seed_pack(None, _basis(1000, "ml"))[0] == Decimal(1000)
    assert better_seed_pack(_stated(750, "ml"), None)[0] == Decimal(750)


# --- the invariant on the real file ---------------------------------------

def _stated_bo_sizes():
    """Every ProductID whose Back Office seed states a size in a base unit."""
    out = {}
    for r in csv.DictReader(COGS.open(encoding="utf-8-sig")):
        if not (r.get("source_invoice") or "").startswith("bo-seed"):
            continue
        unit = (r.get("pack_unit") or "").strip().lower()
        if unit not in ("ml", "g"):
            continue
        try:
            q = Decimal(r["pack_qty"])
        except Exception:
            continue
        if q > 1:                      # a DefaultSize of 1 is an unconfigured product
            out[f"lightspeed:{(r.get('supplier_code') or '').strip()}"] = (q, unit, r)
    return out


def test_no_seeded_pack_contradicts_the_size_the_export_stated():
    """The regression, on the real book. Before the fix this fails for 96
    ProductIDs — every wine, spirit and keg whose stated size is not 1000."""
    rows = list(csv.DictReader(COGS.open(encoding="utf-8-sig")))
    overrides = load_pack_overrides(ROOT / "data" / "pack_overrides.yaml")
    seed_conv, _seed_price = build_seed_conv(rows, overrides)

    wrong = []
    for pid, (q, unit, r) in _stated_bo_sizes().items():
        if pid in overrides:
            continue               # a confirmed pack outranks the export
        got = seed_conv.get(pid)
        if not got or got[1] != unit:
            continue
        if got[0] != q:
            wrong.append(f"{r['invoice_description']}: export says {q}{unit}, "
                         f"seed resolved {got[0]}{got[1]}")
    assert not wrong, "seed pack contradicts the BO export for:\n  " + "\n  ".join(wrong[:12])


def test_the_seeded_rate_stays_the_price_the_row_actually_quoted():
    """seed_price is the reference the magnitude guard and the liquor
    discriminator are checked against. It must be built from the row's OWN
    divisor: a per_L row quotes $23.40 PER LITRE, so its rate is 23.40/1000
    whatever bottle size the product ends up carrying. Dividing it by the
    retained 750 would inflate the reference 33% and start refusing correct
    invoices."""
    rows = list(csv.DictReader(COGS.open(encoding="utf-8-sig")))
    overrides = load_pack_overrides(ROOT / "data" / "pack_overrides.yaml")
    _seed_conv, seed_price = build_seed_conv(rows, overrides)

    geppetto = seed_price.get("lightspeed:20655236")
    assert geppetto is not None
    assert Decimal("0.0230") < geppetto < Decimal("0.0240"), geppetto
