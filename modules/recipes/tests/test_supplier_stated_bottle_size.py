"""
A per-bottle invoice divided by a size the supplier's own documents contradict.

THE DEFECT
----------
`seed_conv[pid]` is the divisor that turns a whole-bottle invoice price into the
per-ml cost a recipe reads. For a bridged bottle it came entirely from the
Lightspeed side: a size typed into a Back Office product name, or a `per_L`
basis that names the unit and claims nothing at all about the bottle. Neither is
a statement by the people who filled it.

`lightspeed:20484285` is named "Antica Formula Rosso Vermouth **700ML**" in the
BO export, so seed_conv = (700, "ml"). ILG's price book says code 175-042-0 is
1000 ml, and every ILG delivery note for that code reads "6x1LT". ILG invoice
03729959 arrived `per_bottle` at $64.27 and the bridge published

    64.27 / 700  = $0.091814/ml      <- 43% OVER
    64.27 / 1000 = $0.064270/ml      <- what the bottle actually cost

Ten bridged ProductIDs carried a seed the price book contradicts, measured on
this tree. (The audit counted fifteen; that was before `better_seed_pack`
landed, which settled De Bortoli 15 L at 0.07x and Fee Bros bitters at 6.67x —
both now agree with the book exactly.) It runs BOTH ways, and the direction
that flatters is the one nobody was going to notice:

    20484285 Antica Formula          seed  700  book 1000  note 6x1LT     43% OVER
    20445895 Aperol                  seed 1000  book  700  note 6x700ML   30% UNDER
    20445833 Rooster Rojo Blanco     seed 1000  book  700  note 6x700ML   30% UNDER
    21999746 Havana 3yr              seed 1000  book  700  note 6x700ML
    20445887 Dolin Blanc Vermouth    seed 1000  book  700  note 6x700ML
    20445832 Don Julio 1942          seed 1000  book  750  note 6x750ML
    20484784 Fever-Tree Light Tonic  seed 1000  book  500  note 8x500ML
    20487286 Four Pillars Bloody Sh. seed  750  book  700  note 6x700ML
    20492689 Domaine de Canton       seed  700  book  750  note 6x750ML
    20727770 Bickfords Raspberry     seed  700  book  750  note 12x750ML

Three of them had actually received a per-bottle line and were live on
2026-07-14 (invoice 03729959):

    Antica Formula        0.091814 -> 0.064270   43% over
    Aperol                0.029082 -> 0.041545   30% under, the flattering way
    Rooster Rojo Blanco   0.051661 -> 0.073802   30% under, the flattering way

Aperol is the one that proves it. The SAME DAY, invoice 03729960 priced the
same bottle through the seed-matched single-bottle path at $0.043014/ml. One
identity carried 0.029082 and 0.043014 — 48% apart, on one day, from one
supplier — and nothing said so.

THE RULE — two sources, or nothing
----------------------------------
A size is corroborated only where ILG's PUBLISHED PRICE BOOK and ILG's OWN
DELIVERY NOTES for the same code state the same thing. Two separate artefacts,
a catalogue and a docket, and neither is derived from the seed under suspicion —
which is exactly what the Havana Club $29.09 seed lacked for months.

Where they disagree the seed stands and the refusal is printed. That is not
hypothetical: Corona 115-3762 is priced in the book per SIX-PACK (2130 ml) and
invoiced per CAN (355 ml), and Heaps Normal 117-4213 the same at 1500/375.
Believing the book alone there would have divided a can price by a six-pack.
"""

import csv
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from core.domain import canonical_purchasable                            # noqa: E402
from core.pack_overrides import load_pack_overrides                      # noqa: E402
from modules.recipes.pipeline.build_costs import (                       # noqa: E402
    PACK_OVERRIDES, _ilg_key, bo_stated_rates, build_seed_conv,
    corroborated_bottle_ml, invoice_stated_bottle_ml, load_bridge,
    pricebook_selling_unit_ml, _read_cogs_rows,
)

COSTS = ROOT / "data" / "costs.csv"

# seed as it was, and the size two ILG documents agree on. Measured 2026-08-08.
CONTRADICTED = {
    "lightspeed:20484285": (Decimal(700), Decimal(1000)),    # Antica Formula
    "lightspeed:20445895": (Decimal(1000), Decimal(700)),    # Aperol
    "lightspeed:20445833": (Decimal(1000), Decimal(700)),    # Rooster Rojo
    "lightspeed:21999746": (Decimal(1000), Decimal(700)),    # Havana 3yr
    "lightspeed:20445887": (Decimal(1000), Decimal(700)),    # Dolin Blanc
    "lightspeed:20445832": (Decimal(1000), Decimal(750)),    # Don Julio 1942
    "lightspeed:20484784": (Decimal(1000), Decimal(500)),    # Fever-Tree Light
    "lightspeed:20487286": (Decimal(750), Decimal(700)),     # 4 Pillars Bloody
    "lightspeed:20492689": (Decimal(700), Decimal(750)),     # Domaine de Canton
    "lightspeed:20727770": (Decimal(700), Decimal(750)),     # Bickfords Rasp.
}


def _seeds():
    overrides = {canonical_purchasable(k): v
                 for k, v in load_pack_overrides(PACK_OVERRIDES).items()}
    rows = _read_cogs_rows()
    bridge = load_bridge()
    bo = bo_stated_rates(rows)
    pack, refused = corroborated_bottle_ml(rows, bridge)
    return rows, overrides, bo, pack, refused


# --- the evidence itself ---------------------------------------------------

def test_the_price_book_and_the_delivery_notes_both_say_antica_is_a_litre():
    """The two independent statements, asserted separately.

    If either source stops being read the corroboration collapses to one
    opinion, which is the state this finding is about."""
    book = pricebook_selling_unit_ml()
    assert book, "no ILG price book — re-run scripts/build_ilg_pricebook.py"
    assert book[_ilg_key("175-042-0")] == Decimal(1000)
    notes = invoice_stated_bottle_ml(_read_cogs_rows())
    assert notes[_ilg_key("175-0420")] == {Decimal(1000)}, (
        "ILG's own delivery notes for Antica read 6x1LT")


def test_one_source_is_not_enough_corona_is_priced_by_the_six_pack():
    """A book price whose denominator is a multipack must NOT pin a bottle size.

    ILG publish Corona 115-376-2 as a six-pack of 355 ml (2130 ml a selling
    unit) and invoice it per can. Believing the book alone would divide a can
    price by six. The two sources disagree, so nothing is pinned."""
    rows, _o, _b, pack, refused = _seeds()
    why = {k: v for k, v in refused}
    assert "ilg:115-3762" in why and "2130" in why["ilg:115-3762"]
    assert "ilg:117-4213" in why, "Heaps Normal is the same shape at 1500/375"
    for pid in ("lightspeed:20445701", "lightspeed:20445707"):
        assert pid not in pack, f"{pid} pinned on a single, contradicted source"


# --- the correction --------------------------------------------------------

def test_every_contradicted_seed_now_takes_the_supplier_stated_size():
    rows, overrides, bo, pack, _r = _seeds()
    conv, _p = build_seed_conv(rows, overrides, bo, pack)
    wrong = [f"{pid}: {conv.get(pid)} (want {want} ml, was {was})"
             for pid, (was, want) in CONTRADICTED.items()
             if conv.get(pid) != (want, "ml")]
    assert not wrong, "\n  ".join([""] + wrong)


def test_without_the_book_every_one_of_them_reverts(request):
    """The same call with `book_pack` omitted reproduces the defect exactly.

    This is what the file looked like before, and it is how the ten numbers
    above were measured."""
    rows, overrides, bo, _p, _r = _seeds()
    conv, _ = build_seed_conv(rows, overrides, bo, None)
    for pid, (was, _want) in CONTRADICTED.items():
        assert conv.get(pid) == (was, "ml"), pid


def test_a_price_basis_keeps_its_rate_even_when_the_bottle_is_repinned():
    """Aperol's `per_L` seed says $41.55 PER LITRE. That is a rate.

    Pinning the bottle at 700 ml must not turn it into 41.55/700 — the
    reference the magnitude guard is checked against would jump 43% and start
    refusing correct invoices. Only a row whose size was a claim about its own
    selling unit re-divides (Antica: $64.02 was a bottle price, and the bottle
    is a litre, so the rate is 0.06402 not 0.091457)."""
    rows, overrides, bo, pack, _r = _seeds()
    _c, price = build_seed_conv(rows, overrides, bo, pack)
    assert abs(price["lightspeed:20445895"] - Decimal("0.04155")) < Decimal("0.0001")
    assert abs(price["lightspeed:20484285"] - Decimal("0.06402")) < Decimal("0.0001")


# --- the published numbers -------------------------------------------------

def test_the_three_live_per_bottle_costs_are_the_corrected_ones():
    """costs.csv, invoice 03729959, 2026-07-14 — the only lines that moved."""
    want = {"lightspeed:20484285": "0.064270",     # was 0.091814, 43% over
            "lightspeed:20445895": "0.041545",     # was 0.029082, 30% under
            "lightspeed:20445833": "0.073802"}     # was 0.051661, 30% under
    got = {}
    for r in csv.DictReader(COSTS.open(encoding="utf-8-sig")):
        if r["source_invoice"] == "03729959" and r["ingredient"] in want:
            got[r["ingredient"]] = r["cost_per_unit"]
    assert got == want


def test_the_same_bottle_does_not_carry_two_costs_48_percent_apart():
    """Aperol, 2026-07-14, two ILG invoices, one bottle.

    03729959 prices it per bottle and 03729960 per carton; both are bridged
    onto lightspeed:20445895. They now agree to 3.5%, which is the broken-carton
    premium ILG charge. They used to be 48% apart and neither was flagged."""
    seen = [Decimal(r["cost_per_unit"])
            for r in csv.DictReader(COSTS.open(encoding="utf-8-sig"))
            if r["ingredient"] == "lightspeed:20445895"
            and r["observed_on"] == "2026-07-14"]
    assert len(seen) == 2, seen
    assert max(seen) / min(seen) < Decimal("1.10"), seen


def test_antica_invoices_now_reach_the_product_the_recipes_use():
    """20484285 is referenced by NO recipe; 20445890 is the one two use.

    The pair was held back from the §3 sibling sweep because their seed rates
    were 1.42x apart — which was the pack disagreement all along, 700 against
    1000. Corrected, they agree to 0.995x, so the sibling row is now safe to
    add and the invoice observations reach the recipes.

    THE COUNT IS NOT THE POINT AND MUST NOT BE PINNED. This asserted `== 3`
    and went red on 2026-08-14 when ILG 03744948 (2026-08-11, $0.064080/ml)
    became the fourth — a normal delivery landing on a working bridge, i.e.
    exactly what this test wants to happen. A guard that fails every time the
    thing it is protecting succeeds is one people learn to skip past, and this
    one sat in front of pytest in CI, so the whole suite stopped running with
    it. What actually matters is the floor (the bridge still reaches the
    product) and the AGREEMENT below, which now covers four invoices."""
    obs = [r for r in csv.DictReader(COSTS.open(encoding="utf-8-sig"))
           if r["ingredient"] == "lightspeed:20445890"
           and r["source_invoice"] not in ("ls-recipe-seed",)]
    assert len(obs) >= 3, [o["source_invoice"] for o in obs]
    rates = sorted(Decimal(o["cost_per_unit"]) for o in obs)
    assert rates[-1] / rates[0] < Decimal("1.01"), (
        f"{len(obs)} ILG invoices for one bottle should agree: {rates}")
