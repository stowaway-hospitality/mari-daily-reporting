"""The supplier's own UOM outranks a scavenged description.

WHY THIS FILE EXISTS: on 2026-08-14 the mutation "the invoice UOM stops
outranking the description" — `suc = single_unit_content(note)` -> `suc = None`
in resolve_pack() — SURVIVED the whole gate. pytest, both pack detectors,
arch_guard and schema_guard all passed with the rule switched off.

Nothing was asserting the rule directly. What covered it was live data: some
ingredient somewhere read differently, and a test that pinned that ingredient's
number went red. Every one of those got legitimately rewritten or the data moved
on, and the rule was left naked. This asserts the BEHAVIOUR, so it cannot be
uncovered by the book getting cleaner.

The real defect it guards, from the docstring in build_ingredients.py:
MKB500PUNN arrived with the description "Punnet) 8 x 100g packs supplied for" —
a wrapped fragment whose "8 x 100g" is the TOTAL of a four-punnet line. Read as
one punnet it booked King Brown mushrooms at $7.56/kg against the $30.25/kg
every other delivery of the same code states.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.recipes.pipeline.build_ingredients import resolve_pack  # noqa: E402


def test_the_uom_wins_when_the_description_is_a_wrapped_fragment():
    """UOM says one 200 g punnet; the description's "8 x 100g" is the line total.

    Trusting the description gives 800 g for the price of one punnet — 4x light,
    and light is the flattering direction. Re-break the rule and this reds.
    """
    qty, unit, per, how, _review = resolve_pack(
        desc="Punnet) 8 x 100g packs supplied for",
        cost="6.05", basis="per_unit", note="200g punnet", code="MKB500PUNN")
    assert (qty, unit) == (Decimal("200"), "g"), (qty, unit, how)
    assert per == Decimal("6.05") / 200
    assert "invoice UOM" in how, how


def test_the_uom_is_refused_when_it_names_a_MULTI_not_a_unit():
    """The rule must not become the case/bottle bug in a new place. "6x700ML" is
    a CASE: it names six sellable things, and telling a case price from a bottle
    price needs a second source (seed_matched_liquor_cost), not this function.
    So the multi is refused and the description path runs instead."""
    _q, _u, _per, how, _r = resolve_pack(
        desc="VEUVE CLICQUOT NV 750ML", cost="484.58",
        basis="per_unit", note="6x700ML", code="285-0409")
    assert "invoice UOM" not in how, how


def test_a_bulk_carton_label_is_refused_too():
    """CTN-6 is a carton label, not a unit's content. It multiplies a piece
    elsewhere in resolve_pack; taken as the pack itself it would divide a carton
    price by one piece."""
    _q, _u, _per, how, _r = resolve_pack(
        desc="CAMEMBERT 125GM", cost="45.60",
        basis="per_unit", note="CTN-6", code="100487")
    assert "invoice UOM" not in how, how
