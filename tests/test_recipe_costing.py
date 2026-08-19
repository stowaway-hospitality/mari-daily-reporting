

def test_a_portion_never_costs_more_than_twenty_batches():
    """The Tandoori defect, pinned.

    Tandoori Chicken [2Kg] draws "400 ml" of a batch that yields GRAMS.
    Lightspeed priced that draw at $2,940 against a $13 batch -- 225x -- and
    our converter, having correctly refused to price the line itself, handed
    the decision straight back to the number it had just refused. Six Tandoori
    products came out between -257% and -959% GP on $2,924 of quarterly
    revenue, and the audit ratchet went red on every commit.

    The cap is deliberately loose. At 1x it is wrong more often than right --
    Super Lime Juice really does draw three whole "Lime [ea]" batches, and
    capping that would UNDER-cost it. Only the impossible is capped.
    """
    import json
    from pathlib import Path

    book = json.loads((Path(__file__).resolve().parents[1]
                       / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]

    tand_batch = book.get("Tandoori Chicken [2Kg]")
    assert tand_batch, "the Tandoori prep must be in the book"
    tand = book.get("Large Tandoori Chicken")
    assert tand, "Large Tandoori Chicken must be in the book"
    cost = float(tand["our_cost"])
    # Its siblings -- Hawaiian $3.17, BBQ Chicken $3.97 -- are the sanity band.
    assert 2.0 < cost < 6.0, f"Large Tandoori Chicken at ${cost}, out of family"
    assert float(tand["gp_pct"]) > 60

    # THE CAP IS THE NET, NOT THE ANSWER, and the Tandoori no longer needs it:
    # the declared relabel in data/batch_yield_units.yaml now reaches the LIVE
    # converter (it only ever reached the staged book), so the line is costed as
    # 400 g of a 1,116 g batch rather than bounded at one whole batch. Zak,
    # 2026-08-19: "just estimate the tandoori batch yield until verified."
    sauce = [ln for ln in tand_batch["ingredients"]
             if ln.get("ref") == "Tandoori Sauce [Batch]"]
    assert sauce, "Tandoori Chicken [2Kg] must still draw its sauce"
    assert sauce[0]["unit"] == "g" and sauce[0].get("unit_was") == "ml", \
        "the relabel must be applied AND recorded on the line it changed"
    assert not sauce[0].get("capped_at_batch"), \
        "a relabelled line is costed, not capped"
    assert 3.0 < float(sauce[0]["eff_cost"]) < 7.0, \
        f"400 g of a 1,116 g batch, not a whole batch: ${sauce[0]['eff_cost']}"

    # Legitimate multi-batch draws are left alone.
    lime = book.get("Super Lime Juice [1L]")
    if lime:
        assert not any(ln.get("capped_at_batch") for ln in lime["ingredients"]), \
            "3 whole limes is a real recipe, not a defect"


def test_a_declaration_that_stops_binding_is_caught():
    """The failure that cost the most this week, and had no detector.

    Every ruling in this repo binds to a record it does not own. Chefs re-save
    recipes; suppliers drop the pack size out of a description. When the target
    moves, the declaration goes on sitting in the file with its paragraph of
    evidence, correcting nothing -- and reading the file tells you it is fixed.

    Three of those in one week, each found by accident. This pins the detector.
    """
    from scripts.check_declarations_bind import unbound

    found = unbound()
    for f in found:
        assert {"kind", "target", "detail", "why"} <= set(f), f
        assert f["why"], "an unbound declaration must say what it could not find"

    # The check must be able to SEE a broken binding, not merely return [].
    # A fix keyed to a quantity a chef has since edited is exactly the Tandoori
    # case, and it must land in the list.
    import json as _json
    from pathlib import Path as _P
    import scripts.check_declarations_bind as mod

    book = _json.loads((_P(mod.__file__).resolve().parents[1]
                        / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]
    real = next((n for n, r in book.items() if r.get("ingredients")), None)
    assert real, "need at least one recipe to test against"

    doc = {"line_qty_unit_fixes": [{"recipe": real, "ingredient": "Not A Real Line",
                                    "from_qty": 1, "from_unit": "ml",
                                    "to_qty": 1, "to_unit": "g"}]}
    import yaml as _yaml, tempfile, os
    orig = _P(mod.ROOT) / "data" / "batch_yield_units.yaml"
    keep = orig.read_text()
    try:
        orig.write_text(_yaml.safe_dump(doc))
        broken = mod.unbound()
        assert any(f["kind"] == "line_qty_unit_fix" and f["target"] == real
                   for f in broken), "a fix naming a line that isn't there must be caught"
    finally:
        orig.write_text(keep)


def test_the_builder_may_use_a_batch_the_builder_book_does_not_hold():
    """The sub-recipe picker offers 648 batches; the builder book holds 35.

    So a bartender can pick a real, weekly-made, invoice-costed batch and save a
    drink that then refuses to cost. Zak did it saving Classic Margarita on
    2026-08-19 and main was red within the hour.

    The architecture already said "builder recipes WIN where both exist"; the
    "otherwise" half was never written. This pins it — and pins that it is not a
    guess: no DECLARED yield, no fallback.
    """
    from datetime import date
    from decimal import Decimal

    from core.domain import CostSeries, load_cost_observations
    from modules.recipes.cost import _from_costed_book, cost_on, load_recipes

    costs = CostSeries(load_cost_observations())
    recipes = load_recipes("stowaway")
    marg = [r for r in recipes if r.product == "Classic Margarita"]
    if marg:
        c = cost_on(marg[0], costs, date(2026, 8, 4), recipes=recipes)
        assert Decimal("1.0") < c < Decimal("8.0"), f"Classic Margarita at ${c}"

    # A batch with a declared yield resolves...
    sub = _from_costed_book("Super Lime Juice [1L]")
    assert sub is not None and sub.yield_qty > 0 and sub.yield_unit == "ml"

    # ...and a name behind which there is nothing still refuses.
    assert _from_costed_book("Not A Real Batch At All") is None


def test_same_day_prices_take_the_lower_and_only_the_lower():
    """ILG bills freight across a delivery, so two lines for one product on one
    day are not two prices — the dearer is a freight remainder riding on fewer
    bottles. Zak, 2026-08-19: "just take the lower number as that's what's
    accurate."

    Before this, the winner was whichever row sorted last. Rooster Rojo landed
    on $88.73/L against a real $79.70/L and put 24c of imaginary tequila into
    every Margarita.
    """
    from core.domain import prefer_cost_row

    # newer always wins, dearer or not — a genuine price RISE must come through
    assert prefer_cost_row(("1.00", "ml", "2026-08-18"), ("2.00", "ml", "2026-08-19"))
    # same day: cheaper wins
    assert prefer_cost_row(("0.088726", "ml", "2026-08-18"),
                           ("0.079701", "ml", "2026-08-18"))
    assert not prefer_cost_row(("0.079701", "ml", "2026-08-18"),
                               ("0.088726", "ml", "2026-08-18"))
    # older never wins
    assert not prefer_cost_row(("0.01", "ml", "2026-08-18"), ("0.005", "ml", "2026-08-01"))
    # different units are not comparable numbers — keep what we have
    assert not prefer_cost_row(("1.00", "ml", "2026-08-18"), ("0.50", "ea", "2026-08-18"))
    assert prefer_cost_row(None, ("1.00", "ml", "2026-08-18"))


def test_one_rule_for_the_live_price_not_four():
    """It was written out by hand in four places, and build_ingredients' comment
    claimed they "can never disagree about which one it is" — true only while
    nobody changed one. Changing one is what happened, and for an afternoon a
    Margarita's tequila was $88.73/L on one screen and $79.70/L on another.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    hand_rolled = []
    for f in ("modules/recipes/pipeline/build_ingredients.py",
              "scripts/convert_lightspeed_recipes.py",
              "scripts/test_pipeline_integration.py"):
        src = (root / f).read_text(encoding="utf-8-sig")
        lines = src.splitlines()
        # The old shape: `if k not in d or date >= d[k][n]` picking a COST row.
        # Scoped to cost rows on purpose -- the converter picks a PACK SIZE the
        # same way a few lines further down, and "the cheaper one wins" is
        # meaningless for a pack size. Only prices get this rule.
        for n, ln in enumerate(lines):
            if not re.search(r"not in\s+\w+\s+or\s+\w+\s*>=\s*\w+\[\w+\]\[\d\]", ln):
                continue
            window = "\n".join(lines[max(0, n - 8):n + 3])
            if "cost_per_unit" in window:
                hand_rolled.append(f"{f}:{n + 1}")
        assert "prefer_cost_row" in src, f"{f} must use the shared rule"
    assert not hand_rolled, f"hand-rolled cost-row tie-break still in: {hand_rolled}"


def test_a_lowering_merge_ships_only_when_a_human_ruled_on_it():
    """The fence holds merges that DROP a rate, because under-costing flatters
    GP and nobody investigates it. But a hold is not a verdict, and holding a
    call Zak has already made just means the queue never empties.

    Monkey Shoulder: HG on a January seed at $88.67/L, Stowaway on an August ILG
    invoice at $81.56/L. Merging "lowers" HG 8% — toward the truth.

    The release requires the word RULED on the row in product_map.csv, so no
    lowering merge can ever ship silently.
    """
    import csv
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    src = (root / "scripts" / "build_ingredient_map.py").read_text(encoding="utf-8-sig")
    assert 'if lowering and "ruled:" in' in src, "the ruled-release path must survive"

    rows = list(csv.DictReader(
        (root / "data" / "product_map.csv").open(encoding="utf-8-sig")))
    ruled = [r for r in rows if "ruled:" in (r.get("confidence") or "").lower()]
    for r in ruled:
        assert len(r.get("confidence") or "") > 40, (
            f"a ruling must say WHY, not just RULED: {r.get('supplier_code')}")

    # and the released row is actually in the shipped map
    m = (root / "data" / "ingredient_map.csv").read_text(encoding="utf-8-sig")
    if ruled:
        assert "lightspeed:20744462" in m, "the ruled Monkey Shoulder merge must ship"


def test_the_last_save_wins_when_neither_is_dated():
    """max() returns the FIRST maximal element.

    Every pair of blocks the builder writes is undated — it stamps no
    effective_from — so both key on date.min and recipe_as_of handed back the
    EARLIER one. Zak saved Classic Margarita twice on 2026-08-19, correcting the
    lime garnish 1 g -> 1.95 g, and this would have returned the version he had
    just corrected.

    The recipe files are append-only logs where the tail wins. This was the one
    place reading them backwards.
    """
    from datetime import date
    from decimal import Decimal

    from modules.recipes.cost import Recipe, RecipeLine, recipe_as_of

    def block(qty):
        return Recipe(product="X", venue="stowaway",
                      lines=(RecipeLine(ingredient="i", qty=Decimal(qty), unit="g"),))

    on = date(2026, 8, 19)
    assert recipe_as_of([block("1"), block("1.95")], "X", on).lines[0].qty == Decimal("1.95")
    # order is what decides it, so the reverse must give the reverse
    assert recipe_as_of([block("1.95"), block("1")], "X", on).lines[0].qty == Decimal("1")

    # A DATE STILL BEATS POSITION. Effective-dating is the real mechanism; file
    # order is only the tie-break for saves that never got a date.
    dated = Recipe(product="X", venue="stowaway", effective_from=date(2026, 8, 1),
                   lines=(RecipeLine(ingredient="i", qty=Decimal("9"), unit="g"),))
    assert recipe_as_of([block("1"), dated], "X", on).lines[0].qty == Decimal("9")
    # ...and a future version is not in force yet
    future = Recipe(product="X", venue="stowaway", effective_from=date(2026, 9, 1),
                    lines=(RecipeLine(ingredient="i", qty=Decimal("99"), unit="g"),))
    assert recipe_as_of([dated, future], "X", on).lines[0].qty == Decimal("9")


def test_a_large_pizza_never_carries_less_than_a_regular():
    """Produce built the regular as "0.716 x the large", so the large was the
    source. Zak then WEIGHED the regulars and 16 lines came back heavier than
    the large they were scaled from — 33 g of Spanish onion on a regular against
    20 g on a large, seven pizzas over.

    Whatever the true ratio is, a 13" pizza does not get less onion than an 11"
    one. Under-costing flatters GP and nobody investigates it: this was $2,692 a
    year across the Large range.
    """
    import json
    import re
    from pathlib import Path

    book = json.loads((Path(__file__).resolve().parents[1]
                       / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]

    offenders = []
    for name, r in book.items():
        if not re.match(r"^Large\b", name, re.I):
            continue
        reg = book.get(re.sub(r"^Large\b", "Regular", name, flags=re.I))
        if not reg:
            continue
        rq = {str(l.get("name") or ""): float(l.get("qty") or 0)
              for l in reg["ingredients"]}
        for ln in r["ingredients"]:
            nm = str(ln.get("name") or "")
            if re.search(r"pizza box|box insert", nm, re.I):
                continue                      # a box is counted, not scaled
            if ln.get("kind") == "subrecipe" and ln.get("ref") in book:
                continue                      # a whole sold pizza, not a topping
            lq = float(ln.get("qty") or 0)
            if (ln.get("unit") or "") == "g" and lq > 0 and rq.get(nm, 0) > lq:
                # Only lines the WEIGHED sheet covers can be held to this. Where
                # nobody has measured a topping, Produce's two figures may still
                # disagree and no ratio can settle it — basil pesto is the one
                # left, large 30 g against regular 40 g, and it is reported by
                # audit_book rather than papered over here.
                if ln.get("weighed"):
                    offenders.append(f"{name}/{nm}: large {lq} < regular {rq[nm]}")
    assert not offenders, ("a WEIGHED large carries less than a weighed regular: "
                           + "; ".join(offenders[:5]))

    # THE LIFT IS GONE AND SHOULD STAY GONE. It derived a large as
    # regular/0.716 while nobody had weighed the larges; pizza_portions.yaml now
    # weighs both, and the sheet showed the derivation had been propagating a
    # WRONG regular (Spanish onion 33 g against a measured 10 g) onto seven
    # pizzas. An inference is what you reach for until somebody measures.
    assert not [ln for r in book.values() for ln in r["ingredients"]
                if ln.get("lifted_from_weighed_regular")], \
        "a derived large has come back; the sheet weighs both sizes now"

    # ...and the measurement is recorded on the lines it set.
    weighed = [ln for r in book.values() for ln in r["ingredients"]
               if ln.get("weighed")]
    assert len(weighed) > 500, f"only {len(weighed)} weighed lines"
    assert {w["weighed"]["size"] for w in weighed} <= {"regular", "large", "family"}
