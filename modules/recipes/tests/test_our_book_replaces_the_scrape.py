"""Produce is a mirror nobody updates. Where we have the recipe, ours IS it.

Zak re-specced Pizza Sauce in the builder on 2026-08-15 — 10 kg of tinned tomato
+ oregano + tomato paste ($37.19) became 6 kg of Kagome + salt + parsley
($14.31). Nobody went and edited it in Produce, because Produce is not where the
recipe lives any more. So the scrape kept handing us the dead version, and every
prep built on it — Salsa Rosa, and under that Black Beans, Pulled Mushroom,
Burrito Rice Sauce and every burrito — was costed off a recipe the kitchen had
stopped making.

Zak: "why does that old recipe still exist, i told you we need to replace all use
of pizza sauce with the new recipe."

Two properties keep that honest, and both are load-bearing:

  * OURS WINS when we have it (test_our_version_replaces_the_scraped_one).
  * OURS IS REFUSED when we cannot fully cost it. Several of our records carry no
    unit_cost_incl snapshot and rely entirely on live ids; if an id has no price,
    the batch sums to a confident $0.00. Replacing a $4.61 scraped recipe with
    $0.00 is a silent, flattering loss — exactly the direction CLAUDE.md warns
    about — so the scrape stands and the gap stays visible.
    (test_a_recipe_we_cannot_cost_is_left_alone)

And the yield must travel with the recipe. Dividing one recipe's cost by another
recipe's yield invented a sauce twice in one afternoon: $6.17/kg (new yield, old
$37.19 batch) and $1.53/kg (new $14.31 batch, old 9338 g yield). $2.37/kg is the
only figure that describes something real.
"""
from scripts.convert_lightspeed_recipes import load_our_book_lines


def _costs(**kw):
    """id -> (per_base_unit, unit), the shape load_our_costs returns."""
    return kw


def test_our_version_replaces_the_scraped_one(monkeypatch, tmp_path):
    lines, ids, yields = load_our_book_lines(_costs())
    assert "Pizza Sauce [Recipe]" in lines, "our Pizza Sauce must reach the converter"
    got = {l["name"]: float(l["qty"]) for l in lines["Pizza Sauce [Recipe]"]}
    # Salt 10 g since Renan's 2026-08-20 save (was 18 under Zak's 08-16 spec).
    assert got == {"Parsley": 10.0,
                   "Tomato - Pizza Sauce Kagome": 6000.0,
                   "Pure Cooking Sea Salt": 10.0}
    total = sum(float(l["cost"]) for l in lines["Pizza Sauce [Recipe]"])
    assert abs(total - 14.3075) < 0.01, f"batch should be $14.31, got ${total:.4f}"


def test_the_yield_travels_with_the_recipe():
    _, _, yields = load_our_book_lines(_costs())
    # 6020 g since Renan's corrected 2026-08-20 save (was 6028 under Zak's).
    assert yields["Pizza Sauce [Recipe]"] == (6020.0, "g")
    # ...and that pairing is the only one that gives the real rate.
    lines, _, _ = load_our_book_lines(_costs())
    total = sum(float(l["cost"]) for l in lines["Pizza Sauce [Recipe]"])
    rate = total / yields["Pizza Sauce [Recipe]"][0] * 1000
    assert 2.30 < rate < 2.45, f"expected ~$2.37/kg, got ${rate:.2f}/kg"
    # the two wrong pairings, named so nobody reintroduces them
    assert abs(37.19 / 6028 * 1000 - 6.17) < 0.02      # old cost / new yield
    assert abs(total / 9338 * 1000 - 1.53) < 0.02      # new cost / old yield


def test_the_kagome_line_carries_its_supplier_id():
    """A b-e: code can only resolve because our record states the id outright."""
    _, ids, _ = load_our_book_lines(_costs())
    assert ids["Tomato - Pizza Sauce Kagome"] == "b-e:14580"
    assert ids["Pure Cooking Sea Salt"] == "b-e:28010"


def test_a_recipe_we_cannot_cost_is_left_alone():
    """The safety property. Davy's Old Fashioned has lines with no price."""
    lines, _, _ = load_our_book_lines(_costs())
    for name, ls in lines.items():
        total = sum(float(l["cost"]) for l in ls)
        assert total > 0, f"{name} was returned at $0 — it should have been refused"


def test_live_prices_beat_the_keyed_in_snapshot():
    """An invoice-fed rate must win over the snapshot taken when it was entered."""
    plain, _, _ = load_our_book_lines(_costs())
    dear, _, _ = load_our_book_lines(_costs(**{"b-e:14580": (1.0, "g")}))
    before = sum(float(l["cost"]) for l in plain["Pizza Sauce [Recipe]"])
    after = sum(float(l["cost"]) for l in dear["Pizza Sauce [Recipe]"])
    assert after > before * 100, "the live rate did not reach the line"
