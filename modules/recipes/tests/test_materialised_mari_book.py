"""
Phase 2a — the materialised Marilyna's book must stay equivalent to the scrape.

WHAT THIS GUARDS
----------------
The one-way door is only safe if walking through it changes no numbers. These
pin the three things that were actually wrong on the first run, each of which
was silent and each of which flattered or inflated a cost by a large factor:

  * `Cooked Beef Brisket [1Kg]` yields 6,000 g (prep_yields.yaml, cook-loss
    basis), NOT the 1,000 g its name bracket claims. Reading the bracket costs
    cooked brisket 6x and lands $8.53 on every Meatlovers and Sanchez.
  * a line our own book cannot price must be emitted `manual`, carrying the
    figure the P&L publishes today. Silently dropping it understates cost,
    which overstates GP — the direction nobody investigates.
  * every product the OLD book costs must still cost, or the migration loses
    coverage without saying so.

Equivalence deliberately carries known-wrong numbers: migration and correction
are separate steps (COST_BOOK_ARCHITECTURE_PLAN.md, T2).
"""

import json
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

STAGED = ROOT / "data" / "recipes" / "_staged" / "marilynas.yaml"
DIFF = ROOT / "data" / "_shadow" / "marilynas_diff.json"
REPORT = ROOT / "data" / "_shadow" / "materialise_marilynas.json"

pytestmark = pytest.mark.skipif(not STAGED.exists(),
                                reason="Mari is not staged yet (pre-Phase-2a)")


def _blocks():
    return yaml.safe_load(STAGED.read_text(encoding="utf-8-sig")) or []


def test_every_line_declares_a_source():
    """`source` is the whole idea (plan, section 5). A line without one is a
    quantity nobody can rank, and provenance ranking is the conflict resolver."""
    allowed = {"weighed", "invoice", "derived", "mirrored", "rule", "authored", "scrape"}
    bad = [(b["product"], l.get("desc") or l.get("subrecipe"))
           for b in _blocks() for l in (b.get("ingredients") or [])
           if l.get("source") not in allowed]
    assert not bad, f"{len(bad)} line(s) with a missing/unknown source: {bad[:5]}"


def test_brisket_yield_is_the_measured_basis_not_the_name_bracket():
    blocks = {b["product"]: b for b in _blocks()}
    brisket = blocks.get("Cooked Beef Brisket [1Kg]")
    assert brisket, "the brisket batch must be materialised — 12 Mari products draw on it"
    assert brisket["yield_qty"] == 6000 and brisket["yield_unit"] == "g", (
        "prep_yields.yaml states 6,000 g (10,000 g raw x 60% cook loss, corroborated "
        "by Lightspeed's own $25.00/kg seed implying 58.5%). The '[1Kg]' in the name "
        "is a pack label, not a yield; believing it costs brisket 6x."
    )


def test_no_subrecipe_is_used_without_a_yield():
    """cost_on refuses a sub-recipe with no yield, so an un-yielded batch silently
    removes every dish that draws on it from the book."""
    blocks = {b["product"]: b for b in _blocks()}
    used = {l["subrecipe"] for b in _blocks() for l in (b.get("ingredients") or [])
            if l.get("subrecipe")}
    missing = [s for s in used
               if s in blocks and not blocks[s].get("yield_qty")]
    assert not missing, f"sub-recipes used but declaring no yield: {missing}"


def test_manual_lines_carry_a_cost_and_are_reported():
    """A manual line is a debt: our book cannot price it and the number is
    Lightspeed's. It must be visible in the report, never just in the YAML."""
    manual = [(b["product"], l) for b in _blocks()
              for l in (b.get("ingredients") or []) if l.get("manual")]
    for product, l in manual:
        assert l.get("unit_cost_incl") is not None, (
            f"{product}: a manual line with no unit_cost_incl costs $0 — "
            f"a silent understatement of cost")
    if REPORT.exists():
        reported = json.loads(REPORT.read_text())["manual_lines"]
        assert len(reported) == len(manual), (
            "every manual line must appear in the materialisation report")


@pytest.mark.skipif(not DIFF.exists(), reason="no shadow diff recorded yet")
def test_shadow_diff_stays_within_the_attributed_residual():
    """Every dollar of the residual is attributed. Two causes, no others:

    1. Sub-cent rounding on Wings Deals, from freezing a per-unit cost at six
       decimal places (<= $0.0011 each).
    2. Sub-recipe lines the OLD engine was publishing Lightspeed's figure for
       because it never costed them from our book at all -- `our_cost: None,
       eff_cost = ls_cost`. Large Garlic Cheese Pizza is the worst: Lightspeed
       says 43 g of garlic oil costs 6c ($1.40/kg) against our invoice-fed
       $3.80/kg, so the new engine charges $0.1634 and the old charged $0.06.

    The second is the migration WORKING -- a real cost the old path was hiding,
    and hiding in the flattering direction, which CLAUDE.md names as the
    dangerous one. It is deliberately not frozen into a `manual` line: doing so
    would embed Lightspeed's $1.40/kg garlic oil in the book permanently, which
    is the exact thing this migration exists to end.

    This is a RATCHET. If it fails, something moved that nobody has explained --
    attribute it before touching the numbers here.
    """
    d = json.loads(DIFF.read_text())
    assert d["max_abs_delta"] <= 0.15, (
        f"max |delta| ${d['max_abs_delta']} exceeds the attributed residual. "
        f"Worst: {[(r['product'], r['delta']) for r in d['diffs'][:5]]}")
    assert d["sum_abs_delta"] <= 1.00, f"sum |delta| ${d['sum_abs_delta']} — drift"
    # THE ONLY COSTS THAT MAY FALL are the Jimmy Jury family, and only by cents.
    # data/batch_yield_units.yaml relabels the scrape's "60 g of Chimichurri" in
    # J.J. Aioli to the 60 ml the hand-authored record states, and 60 ml of a
    # 650 ml batch is slightly less sauce than the 1:1 reading charged for.
    # Deliberate, evidenced, and worth about three quarters of a cent a pizza.
    #
    # A fall anywhere else, or a bigger one here, is unexplained -- and an
    # unexplained fall flatters GP, which is the direction nobody investigates.
    neg = [r for r in d["diffs"] if r["delta"] < -0.01
           or (r["delta"] < -0.002 and "jimmy jury" not in r["product"].lower())]
    assert not neg, (
        f"unattributed cost fall: {[(r['product'], r['delta']) for r in neg[:5]]}")


@pytest.mark.skipif(not DIFF.exists(), reason="no shadow diff recorded yet")
def test_the_migration_loses_no_products_beyond_the_known_seven():
    """7 products still refuse: `Tandoori Sauce [Batch]` yields g and its recipe
    draws 1 ml (6 products), and `Mulled Wine PartyJar (4)` draws 3.89 ml of a
    `Mulled Wine` that declares no yield at all, so it is carried as 1 ea.

    Both are the same shape as the three batch-unit mislabels already corrected
    in data/batch_yield_units.yaml and want the same treatment: read the yield's
    own basis rather than assume a density. Pinned so an eighth cannot appear
    unnoticed.
    """
    d = json.loads(DIFF.read_text())
    assert len(d["only_old"]) <= 7, (
        f"{len(d['only_old'])} products the old book costs and the new one does not: "
        f"{d['only_old'][:10]}")
