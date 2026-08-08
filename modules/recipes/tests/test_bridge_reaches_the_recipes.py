"""
A bridge that feeds a ProductID no recipe uses is not a bridge.

THE GAP
-------
Lightspeed keeps a spirit twice: the stock bottle ("Grand Marnier [Bottle]") and
the pour a recipe names ("Grand Marnier"). product_map carried a row for the
first only, and load_bridge was a plain dict, so an invoice landed on an
identity nothing reads while the identity 2 recipes DO read stayed frozen on its
January seed. 26 of 179 bridges were inert:

    20487225 Grand Marnier [Bottle]   0 recipes  <- the invoice went here
    20445871 Grand Marnier            2 recipes  0.075414 frozen vs 0.097720  22.8% under
    20483410 Rooster Rojo [Bottle]    0 recipes
    20445833 Rooster Rojo            19 recipes  1.7% under, ~$535/yr across
                                                 6,068 Classic Margaritas

Being a dict is also how a WRONG bridge hid. ILG 285-0409P is Four Pillars
*Bloody Shiraz* and carried rows for Olive Leaf and Rare Dry as well; last row
wins, so it landed on Rare Dry and offered it Bloody Shiraz's price. It failed
closed only because a separate bug made the unit unmatchable — by luck, not
design, and that luck ran out the moment the unit bug was fixed.

WHAT THIS GUARDS
----------------
- one supplier code may feed several ProductIDs, and all of them get the cost
- every bridge target is either referenced by a recipe, or has a sibling that is
  and is ALSO bridged — no more silent inert pairs
- the two Four Pillars rows point at their own codes, not at Bloody Shiraz's
"""

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.recipes.pipeline.build_costs import load_bridge          # noqa: E402

MAP = ROOT / "data" / "product_map.csv"
BOOK = ROOT / "data" / "lightspeed_recipes_costed.json"
COGS = ROOT / "data" / "cogs_list.csv"


def test_one_code_may_feed_several_product_ids():
    """A dict silently kept the last row. Buffalo Trace is legitimately mapped
    to both its [Bottle] and its [House] record."""
    bridge = load_bridge()
    assert bridge, "fixture sanity: product_map did not load"
    assert all(isinstance(v, list) for v in bridge.values())
    multi = {k: v for k, v in bridge.items() if len(v) > 1}
    assert multi, "fixture sanity: expected at least one code feeding two identities"


def test_the_four_pillars_rows_point_at_their_own_codes():
    """285-0409P is Bloody Shiraz. Olive Leaf is 285-1480 and Rare Dry is
    285-0132P, and each one's invoice rate matches that product's own seed to
    four decimal places."""
    bridge = load_bridge()
    assert "lightspeed:20445814" in bridge.get("ilg:285-1480", [])    # Olive Leaf
    assert "lightspeed:20445815" in bridge.get("ilg:285-0132P", [])   # Rare Dry
    bloody = bridge.get("ilg:285-0409P", [])
    assert "lightspeed:20445814" not in bloody
    assert "lightspeed:20445815" not in bloody


def _recipe_referenced():
    refs = defaultdict(list)
    if not BOOK.exists():
        return refs
    for name, rec in json.loads(BOOK.read_text())["recipes"].items():
        for ing in rec.get("ingredients", []):
            if ing.get("kind") == "id":
                refs[ing.get("ref", "")].append(name)
    return refs


def _bo_names():
    out = {}
    for r in csv.DictReader(COGS.open(encoding="utf-8-sig")):
        if (r.get("source_invoice") or "").startswith(
                ("bo-seed", "bo-ingredient-seed", "ls-recipe-seed", "recipe-bridge-seed")):
            out.setdefault(f"lightspeed:{(r.get('supplier_code') or '').strip()}",
                           r["invoice_description"].strip())
    return out


def _base(n):
    n = re.sub(r"\[[^\]]*\]", " ", n or "")
    n = re.sub(r"\b\d+(\.\d+)?\s*(ML|L|LT|G|KG|YR|YO)\b", " ", n, flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", n.lower()).strip()


def test_no_bridge_is_inert_while_a_recipe_sibling_stays_frozen():
    """The rule that stops this class recurring. A target no recipe reads is
    fine on its own — but not when a same-named product IS read by recipes and
    is not bridged too. Known exceptions carry a reason: their seed rates
    disagree, so pairing them would move a number on a guess (see §4, pack size
    vs the ILG price book)."""
    refs = _recipe_referenced()
    if not refs:
        return                                  # book not built in this checkout
    names = _bo_names()
    by_base = defaultdict(list)
    for pid, n in names.items():
        by_base[_base(n)].append(pid)

    bridged = {p for pids in load_bridge().values() for p in pids}
    # Seed rates disagree on these, so they are reported rather than paired.
    KNOWN = {"lightspeed:20445890",    # Antica Formula   1.42x  (700ml vs a 1L book size)
             "lightspeed:20445870",    # Mr Black         0.79x
             "lightspeed:20445812",    # Wolf Lane Navy   0.71x
             "lightspeed:20445809"}    # Archie Rose      units disagree

    inert = []
    for pid in sorted(bridged):
        if refs.get(pid):
            continue
        for sib in by_base.get(_base(names.get(pid, "")), []):
            if sib == pid or not refs.get(sib) or sib in bridged or sib in KNOWN:
                continue
            inert.append(f"{pid} ({names.get(pid,'')[:28]}) is bridged but unused; "
                         f"{sib} ({names.get(sib,'')[:28]}) has {len(refs[sib])} "
                         f"recipe(s) and no bridge")
    assert not inert, "inert bridge(s):\n  " + "\n  ".join(inert)
