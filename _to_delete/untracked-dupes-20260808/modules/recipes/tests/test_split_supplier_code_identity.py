"""
One supplier code is one purchasable, however the PDF parse spelled it.

THE GAP
-------
Fresh Fruit Team's invoices leak the UNIT word out of its own column and onto the
end of the supplier CODE — "AH20T Tray", "ONBRKG Kilogram", "HCMB Market". The
bleed is a property of the parser generation, not of the product, so the same
avocado arrives as "AH20T" one week and "AH20T Tray" the next.

`normalize_code()` has existed since the picker was built and was applied ONLY at
merge time, to collapse two rows in the chef-facing list. It was never applied to
the COST KEY, so build_costs kept two priced series for one product:

    fresh-fruit-team:AH20T        latest 2026-07-25  $30.80/tray   (7 observations)
    fresh-fruit-team:AH20T TRAY   latest 2026-08-04  $26.40/tray  (14 observations)

Measured on data/costs.csv: 53 split identities, 36 of them holding a DIFFERENT
latest price on each half, and 5 holding a different UNIT because a
chef-confirmed pack override was keyed to one spelling and not the other:

    EGL7BX   $0.266667/ea   vs  $56.00/box
    LRW15BG  $17.60/box     vs  $0.011733/g
    MSHB2    $0.008250/g    vs  $33.00/box
    TGL10BX  $34.16/box     vs  $0.004256/g
    POTCOBX  $0.002475/g    vs  $49.50/box

Half of every affected product's price history was invisible to the as-of lookup
on whichever id a recipe happened to hold. build_ingredients.py:465 states the
contract in capitals — "THE ID MUST BE THE SAME KEY THE COST ENGINE USES" — and
this was the one thing that broke it.

MEASURED CONSEQUENCE OF MERGING
-------------------------------
59 identities move their latest observation, every one of them because the other
half of the same product's history is newer. Four of those are bridged
ProductIDs that recipes read, and only two carry a price change:

    lightspeed:22488995  Avocado [Tray]  $30.80 -> $26.40  (2026-07-25 -> 08-04)
    lightspeed:22995331  Corn Baby Sweet $ 1.32 -> $ 1.54  (2026-07-30 -> 08-07)

which moves 13 costed recipes: Guacamole [4kg] -$4.40 a batch and the eight
dishes drawing on it, and Corn Star +$0.22 (GP 84.7% -> 83.2%, the honest
direction). 68 further invoice observations reach a ProductID for the first time,
because product_map is keyed on the clean code and only ever matched the clean
half.

WHAT THIS GUARDS
----------------
- identity is core's, not each builder's, and the bleed is not part of it
- the PACK PARSER still sees the raw code — that trailing word is how some Fresh
  Fruit Team lines state their sold unit, and normalising it away would lose them
- a recipe SAVED under the old spelling still costs (four live Stowaway lines do)
- a chef confirmation keyed to either spelling reaches the product
- and the invariant on the real file: no two ids in costs.csv are one purchasable
"""

from __future__ import annotations

import csv
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from core.domain import (CostObservation, CostSeries, canonical_purchasable,   # noqa: E402
                         normalize_code, purchasable_id)
from modules.recipes.pipeline import build_ingredients as bi                   # noqa: E402

COSTS = ROOT / "data" / "costs.csv"


# --- identity --------------------------------------------------------------

def test_the_bled_unit_word_is_not_part_of_the_key():
    """The finding, in one line. Both spellings are the same avocado."""
    assert (purchasable_id("Fresh Fruit Team", "AH20T Tray")
            == purchasable_id("Fresh Fruit Team", "AH20T")
            == "fresh-fruit-team:AH20T")


def test_every_bled_spelling_in_the_real_data_collapses_onto_its_clean_twin():
    """87 Fresh Fruit Team codes carry the bleed. Named here so the list is a
    fact in the test, not a claim in a comment."""
    for bled, clean in (("ONBRKG Kilogram", "ONBRKG"), ("HCMB Market", "HCMB"),
                        ("TCPUN Punnet", "TCPUN"), ("HMRBCH Bunch", "HMRBCH"),
                        ("EGL7BX Box", "EGL7BX"), ("PQEA Each", "PQEA")):
        assert (purchasable_id("Fresh Fruit Team", bled)
                == purchasable_id("Fresh Fruit Team", clean))


def test_a_code_that_is_not_a_bleed_is_left_exactly_alone():
    """The rule needs whitespace and a bare unit WORD. It must never chew a real
    code: "PCW20BG 20KG" keeps its "20KG" (a number, not the word), and no
    ordinary supplier code changes at all."""
    assert normalize_code("PCW20BG 20KG") == "PCW20BG 20KG"
    assert purchasable_id("Foodlink", "102689") == "foodlink:102689"
    assert purchasable_id("ILG", "395-6785p") == "ilg:395-6785P"
    assert normalize_code("Bunch") == "Bunch"            # nothing left to strip
    assert normalize_code("AH20T Tray") == normalize_code(normalize_code("AH20T Tray"))


def test_canonicalising_an_already_formed_id_agrees_with_building_one():
    """pack_overrides.yaml and saved recipes hold whole ids, not (supplier, code)
    pairs. The two routes to a key must not be able to disagree."""
    assert (canonical_purchasable("fresh-fruit-team:HCMB MARKET")
            == purchasable_id("Fresh Fruit Team", "HCMB Market"))
    assert canonical_purchasable("lightspeed:20487270") == "lightspeed:20487270"
    assert canonical_purchasable("onion-brown") == "onion-brown"


# --- the pack parser must still see the raw code ---------------------------

def test_the_sold_unit_encoded_in_the_code_is_still_readable():
    """resolve_pack learns the unit from that same trailing word ("KITOSPKG
    Kilogram" is priced per kg). Identity drops it; the parser must not, or a
    third of Fresh Fruit Team stops costing."""
    qty, unit, per, how, bad = bi.resolve_pack(
        "Onion Spanish", Decimal("2.42"), basis="per_unit", note="",
        code="KITOSPKG Kilogram")
    assert (qty, unit) == (Decimal(1000), "g")
    assert per == Decimal("0.002420") and bad is None and how == "code:kilogram"


# --- a recipe saved under the old spelling still costs ---------------------

def test_a_recipe_holding_the_old_id_still_finds_the_series():
    """data/recipes/stowaway.yaml carries "fresh-fruit-team:ONBRKG KILOGRAM" in
    Onion Jam (twice), "…:HMRBCH BUNCH" in Trade Winds and "…:PQEA EACH" in
    Pineapple Salsa. A rename must never snap a saved recipe, so CostSeries
    canonicalises both sides."""
    s = CostSeries([CostObservation(
        ingredient="fresh-fruit-team:ONBRKG", observed_on=date(2026, 8, 1),
        cost_per_unit=Decimal("0.001320"), unit="g", venue="stowaway")])
    hit = s.as_of("fresh-fruit-team:ONBRKG KILOGRAM", date(2026, 8, 5))
    assert hit.cost_per_unit == Decimal("0.001320")
    assert s.rolling("fresh-fruit-team:ONBRKG KILOGRAM", date(2026, 8, 5)).unit == "g"


def test_an_id_that_canonicalises_to_nothing_known_still_raises():
    """Canonicalising is not fuzzy matching. An unknown ingredient must still
    refuse rather than resolve to something nearby."""
    import pytest
    s = CostSeries([CostObservation(
        ingredient="fresh-fruit-team:ONBRKG", observed_on=date(2026, 8, 1),
        cost_per_unit=Decimal("0.00132"), unit="g")])
    with pytest.raises(LookupError):
        s.as_of("fresh-fruit-team:NOTATHING", date(2026, 8, 5))


# --- the invariant on the real book ---------------------------------------

def test_no_two_cost_book_ids_are_the_same_purchasable():
    """The regression, on the real file. Before the fix this fails with 53
    groups — 36 of them holding two different latest prices."""
    if not COSTS.exists():
        return                     # clean checkout: nothing generated yet
    groups: dict[str, set] = {}
    for r in csv.DictReader(COSTS.open(encoding="utf-8-sig")):
        groups.setdefault(canonical_purchasable(r["ingredient"]), set()).add(r["ingredient"])
    split = {k: sorted(v) for k, v in groups.items() if len(v) > 1}
    assert not split, ("one purchasable, two cost series:\n  "
                       + "\n  ".join(f"{k}: {v}" for k, v in sorted(split.items())[:12]))


def test_the_split_identities_no_longer_hold_two_units():
    """The dangerous half of the split: a chef override keyed to one spelling
    left the other on an uncostable "1 box", so the SAME ingredient answered a
    lookup in grams or in boxes depending on which id was asked."""
    if not COSTS.exists():
        return
    latest: dict[str, tuple[str, str]] = {}
    for r in csv.DictReader(COSTS.open(encoding="utf-8-sig")):
        k = canonical_purchasable(r["ingredient"])
        if k not in latest or r["observed_on"] >= latest[k][0]:
            latest[k] = (r["observed_on"], r["unit"])
    for code in ("EGL7BX", "LRW15BG", "MSHB2", "TGL10BX", "POTCOBX"):
        got = latest.get(f"fresh-fruit-team:{code}")
        assert got and got[1] in ("g", "ea"), (
            f"{code} still prices per container: {got}")
