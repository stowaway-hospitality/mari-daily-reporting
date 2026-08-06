"""
ILG bills the same product under two codes: "395-6785" and "395-6785P".

Seven products carry both, and in all seven the two codes share an identical
invoice description. A bridge built from one invoice covers only the code that
invoice happened to use — Aperol was bridged on "395-6785P" and not on
"395-6785", so a $215 delivery never reached the cost book, and the next split
like it would have been silent in the same way.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.recipes.pipeline.build_costs import _extend_bridge_to_p_codes, load_bridge  # noqa: E402


def test_a_bridge_covers_both_ilg_codes_for_one_product():
    b = load_bridge()
    assert b.get("ilg:395-6785") and b.get("ilg:395-6785P")
    assert b["ilg:395-6785"] == b["ilg:395-6785P"]


def test_it_never_pairs_two_different_products(monkeypatch):
    """Only the trailing P may differ, and the descriptions must match exactly."""
    import modules.recipes.pipeline.build_costs as bc

    monkeypatch.setattr(bc, "COGS", ROOT / "data" / "cogs_list.csv")
    out = _extend_bridge_to_p_codes({"ilg:999-9999P": "lightspeed:1"})
    assert "ilg:999-9999" not in out          # no invoice, no evidence


def test_it_is_a_no_op_without_the_invoice_file(tmp_path, monkeypatch):
    import modules.recipes.pipeline.build_costs as bc

    monkeypatch.setattr(bc, "COGS", tmp_path / "nope.csv")
    before = {"ilg:395-6785P": "lightspeed:20484286"}
    assert _extend_bridge_to_p_codes(dict(before)) == before
