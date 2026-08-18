"""
A confirmed alias declined to apply, and a lettuce line over-costed a burger
$2.52.

DEFECT ONE — "already in `out`" is not "already has a recipe"
-------------------------------------------------------------
`apply_product_aliases` skipped whenever `pos_name in out`. That treats "the key
exists" as "there is a recipe under it", and those are different things. Produce
carries name-only stubs: a product exists, nobody ever built it.

Zak, 2026-08-06: *"beef burger d is same recipe as american standard."* The
confirmation went into data/product_recipe_aliases.yaml, and the alias then
declined to apply because an empty "Beef Burger D" was sitting on the key. A
$24.00 burger kept costing nothing while its nine-line, $5.8403 build sat in the
book, and the entry that blocked it contained no information at all.

The rule is now about the BUILD, not the key:

  * an entry with at least one RESOLVED ingredient line is a real Produce recipe
    and is never overwritten. A confirmed alias is one person saying two names
    mean the same dish; a built recipe is the kitchen saying what goes in it,
    and the kitchen wins. It is also the only safe direction — replacing a real
    build with a copy of another dish cannot be undone from here.
  * an entry with no resolved line prices nothing, so a confirmed pairing is
    strictly better than the stub. It is replaced, and printed.

DEFECT TWO — the copy cost more than the dish it copies
--------------------------------------------------------
A line drawn as a pack fraction ("0.083" of a "Lettuce Cos Baby Twin Pack
[Each]") has two readings — a twelfth of a pack, or one pack out of a carton —
and Produce's own stated line cost tells them apart. An aliased recipe is a deep
copy and has NO scrape line of its own, so there was no raw figure to judge
with, and the fallback is WHOLE: the expensive reading.

    American Standard Burger  lettuce  0.083 x $2.75 = $0.228   (Produce: $0.23)
    Beef Burger D             lettuce  1 whole pack   = $2.75

    our_cost  $5.8403  vs  $8.3620      -> $2.5217 a burger, 43% over
    GP            76.1%          61.7%

Lettuce became the dearest thing in the burger — above the wagyu patty — on a
dish whose entire build is a copy of one that reads the same line as 23c.

The " D" suffix fallback (Bang Bang Cauli D, $12.57 on a $16 dish) already
existed for delivery twins; it looked for "Beef Burger", which does not exist.
An alias is asked FIRST because it is not a heuristic: a confirmed pairing names
the source recipe exactly.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import yaml                                                              # noqa: E402
from convert_lightspeed_recipes import apply_product_aliases             # noqa: E402

COSTED = ROOT / "data" / "lightspeed_recipes_costed.json"
ALIASES = ROOT / "data" / "product_recipe_aliases.yaml"


def _aliases():
    return yaml.safe_load(ALIASES.read_text(encoding="utf-8-sig")) or {}


def _book():
    return json.loads(COSTED.read_text(encoding="utf-8-sig"))["recipes"]


def _built(*names):
    """A recipe with one resolved line — what a real Produce build looks like."""
    return {"ingredients": [{"name": n, "kind": "id", "ref": f"lightspeed:{i}",
                             "qty": "1", "unit": "g"}
                            for i, n in enumerate(names)]}


# --- which entry wins ------------------------------------------------------

def test_a_confirmed_alias_replaces_an_entry_that_prices_nothing():
    """The stub that blocked Beef Burger D for two days."""
    out = {"American Standard Burger": _built("Patty", "Bun"),
           "Beef Burger D": {"ingredients": []}}
    n = apply_product_aliases(out)
    assert n >= 1
    assert out["Beef Burger D"]["alias_of"] == "American Standard Burger"
    assert len(out["Beef Burger D"]["ingredients"]) == 2


def test_an_unresolved_line_is_still_no_recipe():
    """Lines that resolve to nothing cost nothing. The stub can carry text and
    still price zero — `kind` is what says our book can reach it."""
    out = {"American Standard Burger": _built("Patty", "Bun"),
           "Beef Burger D": {"ingredients": [
               {"name": "something", "kind": None, "qty": "1", "unit": "g"}]}}
    apply_product_aliases(out)
    assert out["Beef Burger D"].get("alias_of") == "American Standard Burger"


def test_a_genuinely_built_produce_recipe_is_never_overwritten():
    """The rule that must not be lost while fixing the one above. A build beats
    a rename, and a wrong overwrite here is unrecoverable."""
    mine = _built("Its Own Patty", "Its Own Bun", "Its Own Cheese")
    out = {"American Standard Burger": _built("Patty", "Bun"),
           "Beef Burger D": mine}
    apply_product_aliases(out)
    assert out["Beef Burger D"] is mine
    assert "alias_of" not in out["Beef Burger D"]


def test_the_alias_still_publishes_a_name_that_was_not_there_at_all():
    """The original job of the function — "Outback Prawn Toast" is "Devon's
    Prawn Toast", and no normaliser will ever say so."""
    out = {"American Standard Burger": _built("Patty")}
    assert apply_product_aliases(out) >= 1
    assert "Beef Burger D" in out


def test_the_deep_copy_is_a_copy():
    """Editing the alias must not edit the recipe it was copied from."""
    out = {"American Standard Burger": _built("Patty")}
    apply_product_aliases(out)
    out["Beef Burger D"]["ingredients"][0]["qty"] = "999"
    assert out["American Standard Burger"]["ingredients"][0]["qty"] == "1"


# --- the published cost ----------------------------------------------------

def test_every_confirmed_alias_costs_what_its_source_costs():
    """The invariant. An alias is the SAME DISH — if the two costs differ, a
    line was read differently on the copy, which is exactly the $2.52 lettuce.

    Checked across every confirmed pair, not just the one that broke."""
    book = _book()
    bad = []
    for pos_name, book_name in _aliases().items():
        a, b = book.get(pos_name), book.get(book_name)
        if not a or not b or not a.get("alias_of"):
            continue
        if round(float(a["our_cost"] or 0), 4) != round(float(b["our_cost"] or 0), 4):
            bad.append(f"{pos_name} ${a['our_cost']} vs {book_name} ${b['our_cost']}")
    assert not bad, "\n  ".join([""] + bad)


def test_the_burger_lettuce_is_a_twelfth_of_a_pack_not_a_whole_one():
    """$0.22825, not $2.75. Produce prices its own line at $0.23, so the
    fraction is the reading that matches and the whole pack is 12x out."""
    book = _book()
    line = next(x for x in book["Beef Burger D"]["ingredients"]
                if x["name"].startswith("Lettuce Cos Baby Twin Pack"))
    assert abs(line["eff_cost"] - 0.22825) < 0.001, line
    # A BAND, NOT FOUR DECIMAL PLACES. What this test is about is the ALIAS
    # applying at all: $5.84 for the real nine-line build versus $8.36 when the
    # lettuce falls back to a whole pack — a $2.52 gap. Pinning to a tenth of a
    # cent made an invoice-fed rate moving 1.7c read as that failure, which is
    # the opposite of what the assertion is for.
    assert 5.60 <= float(book["Beef Burger D"]["our_cost"]) <= 6.10, (
        book["Beef Burger D"]["our_cost"])
    assert float(book["Beef Burger D"]["gp_pct"]) > 70


def test_no_alias_costs_more_than_the_dish_it_copies():
    """The class, not the instance. A copy costing MORE than its source can only
    be a line read two ways, and the expensive reading is the default."""
    book = _book()
    for name, r in book.items():
        src = r.get("alias_of")
        if not src or src not in book:
            continue
        a, b = float(r.get("our_cost") or 0), float(book[src].get("our_cost") or 0)
        assert a <= b + 0.0001, f"{name} ${a} > {src} ${b}"
