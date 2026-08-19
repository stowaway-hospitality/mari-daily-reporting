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
    2. The Tandoori ruling (Zak, 2026-08-16): "1 ml" of Tandoori Sauce is the
       whole 1,116 g batch, so the batch now carries its tandoori paste as well
       as its yoghurt. The old engine charged $7.35 -- exactly 1,000 g of Greek
       yoghurt and none of the $5.73 of paste beside it. Worth +$0.87 on a
       Regular Tandoori Chicken.
    3. Sub-recipe lines the OLD engine was publishing Lightspeed's figure for
       because it never costed them from our book at all -- `our_cost: None,
       eff_cost = ls_cost`. Large Garlic Cheese Pizza is the worst: Lightspeed
       says 43 g of garlic oil costs 6c ($1.40/kg) against our invoice-fed
       $3.80/kg, so the new engine charges $0.1634 and the old charged $0.06.

    The last is the migration WORKING -- a real cost the old path was hiding,
    and hiding in the flattering direction, which CLAUDE.md names as the
    dangerous one. It is deliberately not frozen into a `manual` line: doing so
    would embed Lightspeed's $1.40/kg garlic oil in the book permanently, which
    is the exact thing this migration exists to end.

    This is a RATCHET. If it fails, something moved that nobody has explained --
    attribute it before touching the numbers here.
    """
    d = json.loads(DIFF.read_text())
    assert d["max_abs_delta"] <= 1.00, (
        f"max |delta| ${d['max_abs_delta']} exceeds the attributed residual. "
        f"Worst: {[(r['product'], r['delta']) for r in d['diffs'][:5]]}")
    assert d["sum_abs_delta"] <= 5.00, f"sum |delta| ${d['sum_abs_delta']} — drift"
    # THE ONLY COSTS THAT MAY FALL are the Jimmy Jury family, and only by cents.
    # data/batch_yield_units.yaml relabels the scrape's "60 g of Chimichurri" in
    # J.J. Aioli to the 60 ml the hand-authored record states, and 60 ml of a
    # 650 ml batch is slightly less sauce than the 1:1 reading charged for.
    # Deliberate, evidenced, and worth about three quarters of a cent a pizza.
    #
    # A fall anywhere else, or a bigger one here, is unexplained -- and an
    # unexplained fall flatters GP, which is the direction nobody investigates.
    # COSTS MAY FALL BY A CENT OR SO, and there are exactly two reasons.
    #
    #  * the Jimmy Jury family, from the Chimichurri g/ml correction;
    #  * ANY pizza carrying Spanish onion, because T6 (2026-08-17) made the
    #    lookup venue-blind and the 1 August invoice is cheaper than the 20 July
    #    one Stowaway had been pinned to. A newer invoice being cheaper is the
    #    book working, not drift.
    #
    #  * the SIX TANDOORI PRODUCTS, and this one is worth up to 56c because the
    #    OLD side is the estimate. Renan entered "Tandoori Sauce [Batch], 400,
    #    ml, $7.35" free-hand, and $7.35 is the whole batch's cost, not a rate:
    #    400 x that is $2,940 and the six dishes costed up to $187.76 against a
    #    $19.50 menu price. The live converter now CAPS a portion at one whole
    #    batch, which stops the catastrophe but still over-costs -- 400 g out of
    #    a 1,116 g batch is about a third of it. The staged book does the real
    #    arithmetic instead, so it comes in lower. A fall toward the true number
    #    is the migration doing its job.
    #
    # A fall bigger than 1.5c outside the Tandoori family is none of those and
    # wants explaining: the onion moves a pizza by about a cent, and the
    # Chimichurri fix by about three quarters of one.
    neg = [r for r in d["diffs"]
           if r["delta"] < -0.015 and "Tandoori" not in r["product"]]
    assert not neg, (
        f"unattributed cost fall: {[(r['product'], r['delta']) for r in neg[:5]]}")

    # The Tandoori family is bounded too -- it may come DOWN off the cap, never
    # up, and never by more than the cap is worth.
    tand = [r for r in d["diffs"] if "Tandoori" in r["product"]]
    assert all(r["delta"] <= 0.015 for r in tand), (
        f"Tandoori costs rose: {[(r['product'], r['delta']) for r in tand]}")
    assert all(r["delta"] > -1.00 for r in tand), (
        f"Tandoori fell further than the cap explains: "
        f"{[(r['product'], r['delta']) for r in tand]}")


@pytest.mark.skipif(not DIFF.exists(), reason="no shadow diff recorded yet")
def test_the_migration_loses_no_products_at_all():
    """Every product the old book costs, the new one costs too.

    It was 22 refusing, then 7, then 6, now none. Each round was a unit label
    somebody had picked rather than measured -- an ml on a mixed-unit sum, a g
    on a volume, a "1" that meant a whole batch. None of them needed a density
    assumed; all of them needed the basis read.

    A product appearing here again means the new engine has stopped being able
    to cost something it could yesterday, and at cutover that product silently
    falls back to Lightspeed's figure.
    """
    d = json.loads(DIFF.read_text())
    assert not d["only_old"], (
        f"{len(d['only_old'])} products the old book costs and the new one does not: "
        f"{d['only_old'][:10]}")
    assert d["matched"] == d["products_old"], (
        f"only {d['matched']} of {d['products_old']} products cost both ways")
