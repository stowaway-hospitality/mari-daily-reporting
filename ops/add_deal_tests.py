p = "/Users/Shared/ClaudeShared/par-build/tests/test_par_model.py"
t = open(p).read()

extra = '''

# ── drinks bundled inside deals ─────────────────────────────────────────────
def test_deal_bundled_drink_reaches_the_par(stow_build):
    """Zak: "the pars on soft drinks are definitely way too low". A "$60 BANQUET"
    takes a 1.25L Coke off the shelf and the bottle never rings as its own sale.
    The deal ingredient is spelled `1.25L Coke`, the par SKU `Coke 1.25L` —
    reversed word order, so nothing ever matched it."""
    recs, _ = stow_build
    assert recs["Coke 1.25L"]["drivers"]["pour_wk"] > 10.0, (
        "deal-bundled Coke is missing: " + str(recs["Coke 1.25L"]["drivers"]))


def test_token_match_is_order_insensitive_but_not_loose():
    from modules.par import deals as D
    assert D._tokens("1.25L Coke") == D._tokens("Coke 1.25L")
    # the extra token IS the difference — a subset match would merge these two
    assert D._tokens("Coke 1.25L") != D._tokens("Coke Zero 1.25L")
    idx = D.build_index(["Coke 1.25L", "Coke Zero 1.25L"])
    assert idx[D._tokens("1.25L Coke")] == "Coke 1.25L"
    assert idx[D._tokens("1.25L Coke Zero")] == "Coke Zero 1.25L"


def test_ml_ingredients_are_not_ingested_by_the_deal_path():
    """The whole point of WHOLE_UNITS. Lightspeed's book measures spirits and
    kegs in ml; those are already converted by the recipe path, so ingesting
    them here would count every Rooster nip and every keg twice."""
    from modules.par import deals as D
    weeks = ["2026-08-09"]
    series, resolved, _ = D.deal_units(
        DATA, ["Rooster Rojo Blanco Tequila [Bottle]"],
        {"Classic Margarita": [100.0]}, weeks)
    assert not series, f"ml ingredient was ingested: {series}"
    assert not resolved


def test_sold_as_bought_self_reference_is_refused():
    """Lightspeed costs ~47 resale lines with a "recipe" that is the product
    itself. That volume is already counted by the pour path; ingesting it
    inflated Coke 1.25L by +5.4/wk on top of the genuine deal volume."""
    from modules.par import deals as D
    import json as _json, os as _os
    book = _json.load(open(_os.path.join(DATA, "lightspeed_recipes_costed.json")))
    assert "Coke 1.25L" in book["recipes"], "fixture drifted"
    _, resolved, refused = D.deal_units(
        DATA, ["Coke 1.25L"], {"Coke 1.25L": [10.0]}, ["2026-08-09"])
    assert not resolved, "a sold-as-bought self-reference was counted twice"
    assert any("self-reference" in r["reason"] for r in refused)


def test_deal_ingestion_left_the_ml_skus_untouched(stow_build):
    """Belt and braces on the real build: if any ml/g-driven SKU moved when the
    deal path landed, it is double-counting."""
    recs, _ = stow_build
    expected = {"Rooster Rojo Blanco Tequila [Bottle]": 0.52,
                "Bombay Dry [Bottle]": 1.03,
                "Stone & Wood [Keg]": 1.36,
                "Guinness [Keg]": 1.20}
    for sku, pour in expected.items():
        assert recs[sku]["drivers"]["pour_wk"] == pytest.approx(pour, abs=0.005), sku
'''

if "test_deal_bundled_drink_reaches_the_par" not in t:
    open(p, "w").write(t + extra)
    print("appended deal tests")
else:
    print("already present")
