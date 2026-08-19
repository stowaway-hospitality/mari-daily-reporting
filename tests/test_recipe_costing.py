

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

    tand = book.get("Large Tandoori Chicken")
    assert tand, "Large Tandoori Chicken must be in the book"
    cost = float(tand["our_cost"])
    # Its siblings -- Hawaiian $3.17, BBQ Chicken $3.97 -- are the sanity band.
    assert 2.0 < cost < 6.0, f"Large Tandoori Chicken at ${cost}, out of family"
    assert float(tand["gp_pct"]) > 60

    # ...and the cap must not be silent. A bound that nobody can see is just a
    # quieter version of the same wrong number.
    capped = [ln for r in book.values() for ln in (r.get("ingredients") or [])
              if ln.get("capped_at_batch")]
    assert any(ln.get("ref") == "Tandoori Sauce [Batch]" for ln in capped), \
        "the capped Tandoori line must stay recorded on the line it fixed"

    # Legitimate multi-batch draws are left alone.
    lime = book.get("Super Lime Juice [1L]")
    if lime:
        assert not any(ln.get("capped_at_batch") for ln in lime["ingredients"]), \
            "3 whole limes is a real recipe, not a defect"
