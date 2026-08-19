

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
