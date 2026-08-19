"""A can of Corona has no recipe, and that is not the same as having no cost.

The costed book is built from Lightspeed Produce, which answers "what goes into
this". For a packaged drink the answer is the drink, and Produce has no way to
say so — so every beer, cider, seltzer and bottled soft drink at every venue was
absent from the book, fell through to Lightspeed's stale Average-Cost figure,
and counted against recipe coverage. 45 products, ~$80k of lifetime revenue.

add_passthrough_products fills that by reading Back Office's own cost for the
product being sold. It derives nothing, so the risk is not a wrong number — it
is SCOPE. Pull in one product that should have had a recipe and you have swapped
a visible zero for a confident wrong answer, which is strictly worse. These
tests pin the boundary rather than the arithmetic.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

BOOK = ROOT / "data" / "lightspeed_recipes_costed.json"


def _book():
    if not BOOK.exists():
        return None
    return json.loads(BOOK.read_text())["recipes"]


def _passthroughs(b):
    return {n: r for n, r in b.items() if r.get("passthrough")}


def test_a_passthrough_is_one_unit_of_itself():
    b = _book()
    if b is None:
        return
    for name, r in _passthroughs(b).items():
        lines = r.get("ingredients") or []
        assert len(lines) == 1, f"{name}: a pass-through has exactly one line"
        ln = lines[0]
        assert ln["name"] == name, f"{name}: the line must BE the product"
        assert str(ln["qty"]) == "1" and ln["unit"] == "ea", f"{name}: 1 ea, not a pour"
        assert float(ln["our_cost"]) > 0, f"{name}: costed from Back Office"


def test_a_passthrough_never_overrides_a_real_recipe():
    """Produce wins wherever it has an answer. This only fills absences —
    otherwise a Back Office unit price would quietly replace a costed build."""
    b = _book()
    if b is None:
        return
    raw = ROOT / "data" / "lightspeed_recipes.json"
    if not raw.exists():
        return
    produce = set(json.loads(raw.read_text()))
    clash = sorted(set(_passthroughs(b)) & produce)
    assert not clash, f"pass-through shadowing a Produce recipe: {clash[:5]}"


def _bo_rows():
    import csv
    for f in ("stowaway_products.csv", "harry_gatos_products.csv"):
        p = ROOT / "data" / "bo_exports" / f
        if not p.exists():
            continue
        for row in csv.reader(p.open(encoding="utf-8-sig")):
            if len(row) >= 12 and row[0].isdigit():
                yield row


def test_no_pourable_stock_became_a_passthrough():
    """The trap this scope guards against, with the one exception it had to make.

    A bottle priced per millilitre is normally an INGREDIENT — you pour 30 ml of
    it. Cost it as a serve and a nip books at a whole bottle, which is the exact
    shape of the errors this project spent weeks removing.

    THE EXCEPTION, and why it is not a hole. Back Office files the WINE LIST per
    ml, because the Regular and Large pours come out of the same stock — but a
    product named "Kuku Sauvignon Blanc - Bottle" is not poured, it is handed
    over, and its CostPriceIncTax (14.5742) is what the bottle cost, not a rate.
    The old rule excluded all 32 of them, $137,523 of lifetime revenue with no
    recipe at all, and the size-variant split on 2026-08-18 is what stopped the
    merged name hiding it.

    So the boundary moved from "per-ml is out" to "per-ml is out unless the cost
    is 2-90% of the sell price", which is a claim about the NUMBER rather than
    the name. A genuine per-ml rate sits near 0.02% and cannot pass. This pins
    the new boundary; the previous version of this test pinned the old one and
    was correct until the day the wine list needed costing.
    """
    b = _book()
    if b is None:
        return
    from convert_lightspeed_recipes import _is_whole_bottle
    # ANY row may be the one that qualified it, not every row. Both venues carry
    # "Ottelia Cab Sav - Bottle": Stowaway's has the $24.20 cost that costed it,
    # Harry Gatos' sits at 0.0000 because HG has no drinks supplier. Requiring
    # every row to pass failed the product for the existence of its twin.
    pourable, proved = set(), set()
    for row in _bo_rows():
        name = row[2].strip()
        if row[6] in ("ml", "g"):
            pourable.add(name)
        if _is_whole_bottle(name, row[6], row[8], row[10]):
            proved.add(name)
    bad = sorted((set(_passthroughs(b)) & pourable) - proved)
    assert not bad, f"per-ml/g stock costed as a whole serve: {bad[:5]}"


def test_a_pour_never_costs_what_its_own_bottle_costs():
    """The half of the wine rule that would cost real money if it broke.

    Every bottle has "- Regular" and "- Large" siblings: the SAME stock, sold by
    the glass. If a bottle's cost ever reached one of those, a 150 ml pour would
    book at the price of the whole bottle — a 5x overstatement on the highest-
    volume wine lines in the venue.

    NOT "a pour is never a pass-through", which is what this asserted first and
    is wrong: Back Office holds a real per-glass cost for some of them, and those
    are correct and already in the book (Barolo Large Glass $30.80 against $96,
    San Giorgio Large $8.14 against $48). The defect is not that a glass has a
    cost. It is a glass wearing its BOTTLE's cost, so that is what this compares.
    """
    b = _book()
    if b is None:
        return
    bad = []
    for name, r in _passthroughs(b).items():
        head, _, size = name.rpartition(" - ")
        if size.strip().lower() not in ("regular", "large", "glass", "large glass"):
            continue
        bottle = b.get(f"{head} - Bottle")
        if not bottle:
            continue
        try:
            if abs(float(r["our_cost"]) - float(bottle["our_cost"])) < 0.005:
                bad.append(f"{name} costs the same as its bottle "
                           f"(${r['our_cost']})")
        except (TypeError, ValueError, KeyError):
            continue
    assert not bad, "a pour costed as a whole bottle: " + "; ".join(bad[:5])


def test_no_prep_or_batch_became_a_passthrough():
    """A batch's unit price is not its batch cost — the Dragon Soda trap, where a
    builder booked $37.20 against a $9.00 drink.

    The name heuristics stay in force for everything EXCEPT a proved whole
    bottle. _PREP_NAME matches the bare word "blend", and two wines are named
    after how the winemaker made them — Sigurd GSM Red Blend and Sigurd White
    Blend, $5,679 between them — so the batch-name rule was excluding a bottle of
    wine for containing the word. The ratio test has already established what
    those are; a name heuristic should not overrule a measurement.
    """
    b = _book()
    if b is None:
        return
    from convert_lightspeed_recipes import (_PACK_BRACKET, _PREP_NAME,
                                            _is_whole_bottle)
    proved = {row[2].strip() for row in _bo_rows()
              if _is_whole_bottle(row[2].strip(), row[6], row[8], row[10])}
    bad = [n for n in _passthroughs(b)
           if n not in proved and (_PREP_NAME.search(n) or _PACK_BRACKET.search(n))]
    assert not bad, f"prep/pack name costed as a serve: {bad[:5]}"


def test_it_actually_found_the_wine():
    """A filler that fills nothing passes every test above — same argument as
    test_it_actually_found_the_beer, for the list this scope was widened for.

    Named products rather than a count. Kuku and Version Two are the two
    highest-revenue uncosted bottles ($14,317 and $11,045); Sigurd GSM Red Blend
    is the one the batch-name heuristic was throwing away.
    """
    b = _book()
    if b is None:
        return
    pt = _passthroughs(b)
    if not pt:
        return          # no Back Office exports here — nothing to say
    for name in ("Kuku Sauvignon Blanc - Bottle",
                 "Version Two Pinot Grigio - Bottle",
                 "Sigurd GSM Red Blend - Bottle"):
        assert name in pt, f"{name} should be costed as a pass-through"
        assert float(b[name]["our_cost"]) > 0


def test_the_passthrough_gps_are_at_least_arithmetically_possible():
    """A smoke alarm, not a KPI, and the band is wider than I first guessed.

    I wrote 40-92% expecting a population of packaged beer. It is not: bottle
    service on prestige wine runs thin (Dom Pérignon 16.9%, Barolo 30.6%), the
    1.25 L soft drinks that go out with delivery deals sit at 22-32%, and
    "Gluten-free Base" is an add-on sold at 9.8% over cost. Every one of those is
    a real trading decision and none is a costing defect, so the test moved
    rather than the data.

    What is still worth alarming on is a cost and a price that cannot be
    describing the same thing — a keg costed as a schooner, a $0.01 placeholder,
    a case price on a single can. Those land outside 0-97% or go negative.
    """
    b = _book()
    if b is None:
        return
    off = [(n, r["gp_pct"]) for n, r in _passthroughs(b).items()
           if r.get("gp_pct") is not None and not (0 < r["gp_pct"] <= 97)]
    assert not off, f"pass-through GP arithmetically implausible: {off[:8]}"


def test_it_actually_found_the_beer():
    """A filler that fills nothing passes every test above.

    Named products rather than a count, because a count drifts with the menu and
    tells you nothing about whether the RIGHT things were caught. These are the
    highest-revenue absences it was written for.
    """
    b = _book()
    if b is None:
        return
    pt = _passthroughs(b)
    if not pt:
        return          # no Back Office exports here — nothing to say
    for name in ("Corona", "Heaps Normal Tin", "Peroni 0%"):
        assert name in pt, f"{name} should be costed as a pass-through"
        assert float(b[name]["our_cost"]) > 0


def test_a_borrowed_cost_names_the_stowaway_product_it_came_from():
    """Harry Gatos sells cans Back Office leaves at $0.00 that Stowaway costs.

    The reason to read them as the same stock is purchasing, not naming: of 449
    non-seed supplier rows filed to harry_gatos, every one is food except two
    lines of White Light Vodka. HG has no drinks supplier — the cans it sells
    came off a Stowaway invoice because there is no other invoice they could
    have come from. Same argument that aliases Whispering Angel and Veuve.

    A borrowed cost must SAY it is borrowed and from what, so the inference is
    auditable rather than an unexplained number. And it may only ever flow one
    way: Stowaway buys, Harry Gatos serves.
    """
    b = _book()
    if b is None:
        return
    for name, r in _passthroughs(b).items():
        twin = (r.get("ingredients") or [{}])[0].get("stock_twin")
        if not twin:
            continue
        assert r["passthrough"] == "harry_gatos", (
            f"{name}: only Harry Gatos borrows a cost, never the other way")
        assert twin != name, f"{name}: borrowed from itself"
        assert float(r["our_cost"]) > 0

