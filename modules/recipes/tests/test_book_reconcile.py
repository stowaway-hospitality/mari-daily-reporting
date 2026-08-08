"""
The recipe book, checked against ITSELF — the two defects a human caught by eye.

WHAT IS UNDER TEST
------------------
modules/recipes/book_reconcile.py. Every other check this platform owns verifies
arithmetic: stated quantity x sourced price. These four rules verify that the
stated QUANTITY agrees with the other 891 recipes, which is the only way the two
defects below were ever going to be found by a machine:

  1. "Lettuce Cos Baby Twin Pack [Each]" at qty 1 — a whole twin-pack of baby cos
     on an $8.20 burger, $2.75, against the 0.083 the two burgers that already
     carried it use. 12x.
  2. "Chicken Roast" without the Yorkshire Pudding and Gravy lines that Pork,
     Lamb, Beef and Nut Roast carry at 1 and at 145 g — the SAME quantity on all
     four. $1.0631 of a $30.00 plate, on 234 serves a year.

Neither needs to know anything about food. Both are the book contradicting
itself, and each has a named regression below.

THE NUMBERS THIS FILE HOLDS, MEASURED 2026-08-08 against the real feed
(892 recipes, 3,041 lines, 461 (ingredient, unit) groups). flagged / true:

    missing_standard_component   2 / 2   Cauliflower Burrito has no cheese where
                                         the other three burritos all carry 55 g;
                                         Fish Burrito has no lime where the other
                                         three all carry 0.125.
    batch_overflow               4 / 4   Cooked Beef Brisket [1Kg] states 11,454;
                                         Mango-Chilli Puree [1L] 9,794; Jalapeno
                                         Tequila [1L] 7,950; Coconut-washed
                                         Rooster Blanco [1L] 5,100.
    price_conflict               4 / 4   Massenez Elderflower 10.47x, Bittermen's
                                         Tiki 6.45x, Beans Edamame 2.86x, Noodles
                                         Instant Ayam 2.82x.
    whole_pack_outlier           0 / 0   nothing in the saved book, by design —
                                         the lettuce was stopped in the builder.

WHAT THIS GUARDS
----------------
- the Chicken Roast defect is found, from the book alone, with no list of roasts
- the lettuce defect is found, from the book alone, with no list of lettuces
- a family that does not already agree with itself cannot generate a finding
- carriers that disagree about the quantity are a menu, not a standard
- a batch may hold LESS than it makes (water) and may never hold more
- a price argument that is really a unit argument is not raised as a price
- a question data/product_map.csv has already settled is not asked again
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from modules.recipes import book_reconcile as br                    # noqa: E402

BOOK = ROOT / "data" / "lightspeed_recipes_costed.json"

ROASTS = ["Beef Roast", "Chicken Roast", "Lamb Roast", "Nut Roast", "Pork Roast"]
RESTORED = ("Yorkshire Pudding Prep [110 units]", "Gravy Prep")


@pytest.fixture(scope="module")
def book():
    return json.loads(BOOK.read_text(encoding="utf-8-sig"))["recipes"]


def _copy(book):
    return json.loads(json.dumps(book))


# --- 1. the Chicken Roast --------------------------------------------------

def test_the_chicken_roast_is_the_finding_this_detector_exists_for():
    """The book as it stood before 743c664, rebuilt by removing the two lines
    that commit restored — the only honest way to test a detector against a
    defect somebody has already fixed.

    The detector is told nothing about roasts. It groups dishes by the last word
    of their name, keeps only the groups whose members already agree about most
    of their ingredients (roast: 71%), and reports a component the survivors all
    carry AT THE SAME QUANTITY that one of them lacks. It returns both lines and
    nothing else about that family."""
    b = _copy(json.loads(BOOK.read_text(encoding="utf-8-sig"))["recipes"])
    b["Chicken Roast"]["ingredients"] = [
        ln for ln in b["Chicken Roast"]["ingredients"] if ln["name"] not in RESTORED]

    found = br.missing_standard_component(b)
    roast = {(f["recipe"], f["ingredient"]) for f in found if f["family"] == "roast"}
    assert roast == {("Chicken Roast", n) for n in RESTORED}, sorted(roast)

    gravy = next(f for f in found if f["ingredient"] == "Gravy Prep")
    assert gravy["qty"] == "145" and gravy["unit"] == "g"
    assert sorted(gravy["carriers"]) == ["Beef Roast", "Lamb Roast", "Nut Roast", "Pork Roast"]
    assert 0.80 < gravy["per_serve_cost"] < 0.95, gravy["per_serve_cost"]


def test_the_fixed_book_no_longer_reports_the_chicken_roast(book):
    """The other half of the same claim. A detector that still fired after the
    fix would be reporting its own list rather than the data."""
    assert not [f for f in br.missing_standard_component(book) if f["family"] == "roast"]


def test_the_nut_roast_keeps_its_place_in_the_family(book):
    """"Beware legitimate variation — a Nut Roast has no meat." It does not need
    special-casing: the rule only speaks about a component the siblings SHARE, and
    no two roasts share a protein, so a protein can never be reported missing."""
    proteins = {ln["name"] for r in ROASTS for ln in book[r]["ingredients"]
                if "Roast" not in ln["name"]}
    reported = {f["ingredient"] for f in br.missing_standard_component(book)}
    assert not (proteins & reported)


# --- 2. the lettuce --------------------------------------------------------

LETTUCE = "Lettuce Cos Baby Twin Pack [Each]"


def _burger(qty, cost):
    return {"ingredients": [
        {"name": "Wagyu Patty [140g]", "kind": "id", "ref": "lightspeed:1",
         "qty": "1", "unit": "ea", "ls_cost": "4.10", "our_cost": "4.10", "eff_cost": 4.10},
        {"name": LETTUCE, "kind": "id", "ref": "lightspeed:2",
         "qty": str(qty), "unit": "ml", "ls_cost": str(cost), "our_cost": None,
         "eff_cost": cost},
        {"name": "T2 Milk Bun [85g]", "kind": "id", "ref": "lightspeed:3",
         "qty": "1", "unit": "ea", "ls_cost": "0.98", "our_cost": "0.98", "eff_cost": 0.98},
    ]}


def test_a_whole_twin_pack_of_baby_cos_on_one_burger_is_found():
    """Zak's first finding, as a book. Two burgers take 0.083 of the pack — a
    twelfth, 23c. The third takes 1, which is the whole $2.75 pack.

    The rule needs no idea what a twin pack is. A quantity below 1 can only mean
    a share of something countable, so when most recipes take a share and one
    takes the lot, the odd one out is claiming the whole pack."""
    b = {"American Standard Burger": _burger(0.083, 0.228),
         "Beef Burger D": _burger(0.083, 0.228),
         "Chicken Shindig Burger": _burger(1, 2.75)}

    got = br.whole_pack_outliers(b)
    assert len(got) == 1, got
    f = got[0]
    assert f["recipe"] == "Chicken Shindig Burger" and f["ingredient"] == LETTUCE
    assert f["multiple"] == 12.0
    assert abs(f["extra_per_serve"] - 2.522) < 0.001
    assert sorted(f["peers"]) == ["American Standard Burger", "Beef Burger D"]


def test_the_lettuce_rule_says_nothing_about_a_large_pizza_taking_one_shake():
    """The calibration that keeps it quiet. A Large pizza takes 1 of an oregano
    shake where a Regular takes 0.716 — 86 lines of the real book do exactly
    this, and the bare rule flags all of them. 1.4x is not 12x, and 1c is not
    $2.52, so both the multiple and the money have to clear a bar."""
    b = {f"Regular Pizza {i}": {"ingredients": [
            {"name": "Oregano Leaves Rubbed", "kind": "id", "ref": "lightspeed:9",
             "qty": "0.716", "unit": "g", "ls_cost": "0.02", "our_cost": None,
             "eff_cost": 0.02}]} for i in range(3)}
    b["Large Pizza"] = {"ingredients": [
        {"name": "Oregano Leaves Rubbed", "kind": "id", "ref": "lightspeed:9",
         "qty": "1", "unit": "g", "ls_cost": "0.03", "our_cost": None, "eff_cost": 0.03}]}
    assert br.whole_pack_outliers(b) == []


def test_the_saved_book_has_no_whole_pack_outlier(book):
    """0 of 3,041 lines. Not a broken rule — the lettuce never reached the saved
    book, because dashboard/_shared/recipe_line_guard.js warns on it as it is
    typed. This is the tripwire for the day one gets past that."""
    assert br.whole_pack_outliers(book) == []


# --- 3. what stops the family rules crying wolf ----------------------------

def test_a_family_must_already_agree_with_itself(book):
    """The head noun alone is a bad key: "vegan" pairs a Sanchez VEGAN pizza with
    a Seitan Katsu Curry and "1kg" pairs Chipotle Mayo with Cooked Beef Brisket,
    so every ingredient of one reads as missing from the other. Requiring the
    members to already share 30% of their ingredients leaves five families, all
    of them genuinely one plate or one build."""
    fams = {h: (m, c) for h, m, c in br.coherent_families(book)}
    assert set(fams) == {"burrito", "fash", "margarita", "roast", "stormy"}, sorted(fams)
    assert fams["roast"][0] == ROASTS
    assert fams["roast"][1] >= 0.70


def test_dropping_the_coherence_bar_lets_the_junk_families_back_in(book, monkeypatch):
    """The mutation check for the constant above. At 0.0 the rule admits families
    it has no business comparing — proof that the number is doing the work and
    not the family list."""
    monkeypatch.setattr(br, "FAMILY_MIN_COHERENCE", 0.0)
    heads = {h for h, _m, _c in br.coherent_families(book)}
    # "gin" is fourteen different gins, "liqueur" pairs an Add Mac. Liqueur with
    # a Baileys, "house" is ten unrelated house pours. Every ingredient of one is
    # "missing" from the others.
    assert {"gin", "whisky", "liqueur", "house"} <= heads
    assert len(heads) > 20


def test_a_margarita_that_uses_agave_instead_is_not_missing_the_syrup(book):
    """Nine of eleven margaritas carry triple sec — but at 9.25 ml and at 10 ml,
    and the two that skip it are a Cadillac (Grand Marnier instead) and a Tommy's
    (agave instead). Substitution, not omission. The carriers disagreeing about
    the quantity is what tells the two apart, with no cocktail knowledge."""
    found = {(f["recipe"], f["ingredient"]) for f in br.missing_standard_component(book)}
    assert not [f for f in found if f[0] in ("Tommy's Margarita", "Cadillac Margarita")]


def test_carriers_that_disagree_about_the_quantity_are_not_a_standard(book):
    """Mutation: move ONE roast's gravy from 145 g to 150 g and the finding must
    vanish, even though four of five still carry the line. "Most of them have it"
    is a menu; "all of them have it at the same quantity" is a standard."""
    b = _copy(json.loads(BOOK.read_text(encoding="utf-8-sig"))["recipes"])
    b["Chicken Roast"]["ingredients"] = [
        ln for ln in b["Chicken Roast"]["ingredients"] if ln["name"] not in RESTORED]
    assert [f for f in br.missing_standard_component(b) if f["ingredient"] == "Gravy Prep"]

    for ln in b["Pork Roast"]["ingredients"]:
        if ln["name"] == "Gravy Prep":
            ln["qty"] = "150"
    assert not [f for f in br.missing_standard_component(b) if f["ingredient"] == "Gravy Prep"]


def test_the_current_book_yields_only_the_two_burrito_findings(book):
    """The measured output, pinned. Both are plausible omissions and neither is
    obviously wrong, which is the bar a rule has to clear to ship: two findings
    on 892 recipes that a human can settle in a minute each."""
    got = [(f["recipe"], f["ingredient"]) for f in br.missing_standard_component(book)]
    assert got == [("Cauliflower Burrito", "Cheese Mexican Blend Shredded [2kg]"),
                   ("Fish Burrito", "Lime [ea]")], got


# --- 4. batches -------------------------------------------------------------

def test_a_batch_may_hold_less_than_it_makes_and_never_more(book):
    """One direction, and it is the whole design. Super Lime Juice [1L] is three
    limes, 135 g of acids and sugar, and a litre of water that no recipe line
    records; Burrito Rice [3kg] is 924 g of dry rice that comes out at three
    kilos. Both state a fraction of their yield and both are correct. Nothing
    makes the other side innocent: a litre bottle cannot hold seven litres."""
    low = {"Super Lime Juice [1L]", "Super Lemon Juice [1L]", "Burrito Rice [3kg]",
           "Buffalo Aioli [1L]", "Dragon Soda [1L]"}
    assert not (low & {f["recipe"] for f in br.batch_overflow(book)})


def test_the_four_batches_that_state_more_than_they_make(book):
    """The measured output. Each states between 5 and 11 times its own name."""
    got = {f["recipe"]: f["multiple"] for f in br.batch_overflow(book)}
    assert set(got) == {"Cooked Beef Brisket [1Kg]", "Mango-Chilli Puree [1L]",
                        "Jalapeño Tequila [1L]", "Coconut-washed Rooster Blanco [1L]"}, sorted(got)
    assert got["Cooked Beef Brisket [1Kg]"] == 11.45
    assert got["Jalapeño Tequila [1L]"] == 7.95


def test_a_batch_finding_names_both_readings_not_one(book):
    """It must not decide. "Cooked Beef Brisket [1Kg]" states 10 kg of raw
    brisket: either the name is wrong or the quantity is, and the two have
    opposite consequences for the per-kilo rate everything downstream divides
    by. The finding carries the declared yield, the input total and the biggest
    line, so a human can answer it without opening Produce."""
    f = next(x for x in br.batch_overflow(book) if x["recipe"] == "Cooked Beef Brisket [1Kg]")
    assert f["declared"] == 1000.0 and f["declared_unit"] == "g"
    assert f["inputs"] == 11454.0
    assert f["biggest_line"][0] == "Beef Brisket YG Point End [1kg]"


def test_the_declared_yield_convention_is_real(book):
    """The rule rests on a naming convention, so the convention is measured
    rather than assumed: of the recipes whose name declares a yield, seven state
    inputs that come to that figure exactly."""
    exact = 0
    for name, r in book.items():
        y = br.declared_yield(name)
        if not y:
            continue
        tot = sum(float(ln["qty"]) * br._TO_BASE[str(ln["unit"]).lower()]
                  for ln in r["ingredients"]
                  if str(ln.get("unit") or "").lower() in br._TO_BASE
                  and float(ln.get("qty") or 0) > 0)
        if abs(tot - y[0]) < 0.5:
            exact += 1
    assert exact >= 7, exact


# --- 5. one product, two prices --------------------------------------------

def test_the_book_and_lightspeed_agree_about_almost_everything(book):
    """The measurement that justifies the 2x threshold. If disagreement were
    common, 2x would be an arbitrary line; it is not. The median group agrees to
    a tenth of a percent."""
    from statistics import median
    groups = {}
    for name, r in book.items():
        for ln in r["ingredients"]:
            if ln.get("kind") != "id" or ln.get("our_cost") in (None, ""):
                continue
            q, ls = float(ln.get("qty") or 0), float(ln.get("ls_cost") or 0)
            if q <= 0 or ls <= 0:
                continue
            groups.setdefault((ln["ref"], ln["unit"]), []).append((q, ls, float(ln["our_cost"])))
    ratios = []
    for v in groups.values():
        ours, theirs = median(x[2] for x in v), median(x[1] / x[0] for x in v)
        if ours > 0 and theirs > 0:
            ratios.append(max(ours / theirs, theirs / ours))
    ratios.sort()
    assert len(ratios) > 400
    assert ratios[len(ratios) // 2] < 1.01
    assert sum(1 for r in ratios if r < 1.10) / len(ratios) > 0.80


def test_a_unit_argument_is_not_raised_as_a_price_argument(book):
    """Above 50x the two figures are not two opinions about a price — they are
    two different units. Lightspeed charges a whole 4 L jug against a line that
    says "2 ml"; our per-millilitre rate meets its per-bottle one. Reporting
    those as a price sends someone to Back Office to correct a price that is
    right. They belong to the quantity rules."""
    got = {f["ingredient"] for f in br.price_conflicts(book)}
    assert not {"Heinz BBQ Sauce [4L]", "White Truffle Oil Sandhurst [250ml]",
                "700ml Glass Bottle", "Mushroom Shiitake Dried [1kg]"} & got


def test_a_settled_question_is_not_asked_again(book):
    """Havana Club sits at exactly 2.0000x and data/product_map.csv already says
    why our figure is the right one — ILG's own book price is $49.20 a bottle, so
    Lightspeed's $29.09 seed is impossible. Passing that file in must remove it."""
    assert "Havana 3yr [700ml]" in {f["ingredient"] for f in br.price_conflicts(book)}
    assert "Havana 3yr [700ml]" not in {
        f["ingredient"] for f in br.price_conflicts(book, {"lightspeed:21999746"})}


def test_the_four_price_conflicts_that_are_left(book):
    """What survives once the adjudicated and the unit-shaped are gone. Every one
    is a real question about a real product, and three of the four are our book
    holding the DEARER figure — the direction that shows up as a worse GP than
    the dish really has, and so never gets investigated.

    Angostura is excluded by the OTHER list: Back Office holds it twice, 14.93x
    apart, and that already has a flag of its own in the back_office family. Two
    flags for one edit is how a queue starts arguing with itself."""
    import csv
    adjud = {"lightspeed:" + r["product_id"] for r in
             csv.DictReader((ROOT / "data" / "product_map.csv").open(encoding="utf-8-sig"))}
    from audit_book import bo_product_names, cost_book_latest, twin_identity_conflicts
    twins = {m[1] for _r, members in
             twin_identity_conflicts(cost_book_latest(), bo_product_names()) for m in members}
    assert "Angostura Bitters - Bottle 200ml" in {
        f["ingredient"] for f in br.price_conflicts(book, adjud)}
    got = {f["ingredient"]: f for f in br.price_conflicts(book, adjud, twins)}
    assert set(got) == {"Massenez Elderflower [5L]", "Bittermen's Tiki Bitters [Bottle]",
                        "Beans Edamame Soy Frozen [350g]", "Noodles Instant Ayam [700g]"}, sorted(got)
    assert got["Massenez Elderflower [5L]"]["ratio"] == 10.47
    assert got["Massenez Elderflower [5L]"]["ours_is_dearer"] is True
    assert got["Bittermen's Tiki Bitters [Bottle]"]["ours_is_dearer"] is False


# --- 6. the rules are pure --------------------------------------------------

def test_every_rule_is_pure_and_says_the_same_thing_twice(book):
    """No file reads, no clock. The flags feed rebuilds on every deploy and a
    rule whose answer moved with the day would change a work queue for no
    reason — the same contract test_cost_book_flags.py puts on the ids."""
    for fn in (br.missing_standard_component, br.batch_overflow,
               br.whole_pack_outliers, br.price_conflicts):
        assert fn(book) == fn(book), fn.__name__


def test_an_empty_book_is_not_a_finding():
    for fn in (br.missing_standard_component, br.batch_overflow,
               br.whole_pack_outliers, br.price_conflicts):
        assert fn({}) == []
        assert fn(None) == []
