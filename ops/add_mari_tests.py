p = "/Users/Shared/ClaudeShared/par-build/tests/test_par_model.py"
t = open(p).read()

extra = '''

# ── Marilyna's: no till, stock is Stowaway's ────────────────────────────────
def test_mari_volume_reaches_the_stowaway_par(stow_build):
    """Zak: "marilynas consumption ... all gets lumped into the stowaway
    lightspeed pars". Mari has no till and no pars; its soft drinks draw on
    Stowaway stock, so the volume must land on the Stowaway par SKU."""
    recs, _ = stow_build
    for sku in ("Coke 1.25L", "Sprite Can", "Coke Zero Can", "Coke Zero 1.25L"):
        d = recs[sku]["drivers"]
        assert d["pour_wk"] > 0.5, f"{sku} sees no Marilyna's volume: {d}"


def test_mari_volume_does_not_leak_into_a_mari_par():
    """Marilyna's has no Purchase module of its own. Nothing may be created."""
    import os, json as _json
    for fn in os.listdir(f"{DATA}"):
        assert fn != "par_recommendations_marilynas.json", (
            "a Marilyna's par feed was created; mari has no pars")


def test_mari_pizza_lines_are_needs_recipe_not_alias_failures():
    """A pizza is a finished good: it consumes flour/cheese/pepperoni through a
    RECIPE. Classing it as an alias failure would send someone hunting for a par
    SKU that should never exist, and would drown the real alias queue."""
    import json as _json
    rep = _json.load(open(f"{DATA}/par_unattributed_marilynas.json"))
    needs = rep.get("needs_recipe") or []
    assert len(needs) > 50, "the pizza recipe backlog should be reported, not hidden"
    names = " ".join(str(r.get("product", "")) for r in needs).lower()
    assert "pizza" in names or "pepperoni" in names or "margherita" in names


def test_mari_recipe_backlog_does_not_hard_fail_the_build():
    """It is a known, quantified backlog — reported loudly, but it must not
    block the weekly run, or the run is red forever and nobody reads it."""
    import json as _json
    rep = _json.load(open(f"{DATA}/par_unattributed_marilynas.json"))
    needs = rep.get("needs_recipe") or []
    gated = {str(r.get("product", "")) for r in (rep.get("unattributed") or [])}
    for r in needs:
        assert str(r.get("product", "")) not in gated, (
            "a needs_recipe line is also counted against the hard gate")


def test_hg_shared_stock_skus_are_zeroed(hg_build):
    """Regression: Zak zeroed HG's pars for [HG] SKUs (Stowaway stock) and
    discontinued Kaiju outright."""
    recs, _ = hg_build
    for sku in ("Hyoketsu Lemon [HG]", "Trutta Streamside Shiraz - Bottle [HG]",
                "Two Tonne Riesling - Bottle [HG]", "Kaiju Hazy Pale [HG]"):
        r = recs.get(sku)
        if r is None:
            continue
        assert r["rec_par"] == 0.0, f"{sku} should be zeroed, got {r['rec_par']}"
        assert (r.get("override") or {}).get("type") == "zero", sku
'''

if "test_mari_volume_reaches_the_stowaway_par" not in t:
    open(p, "w").write(t + extra)
    print("appended mari/hg tests")
else:
    print("already present")
