"""The sales rollups must keep the field names the joins read.

WHY: asked which thin-margin delivery SKUs still sold, I read `weeks` when the
feed says `weekly`. Every lookup missed, every total came back 0, and "0 sold"
is indistinguishable from a real answer — it even agreed with what we expected,
which is the worst way to be wrong. Same failure shape as the $0 bottles: a miss
that returns a plausible number instead of raising.

So the schema the joins depend on is asserted here. Rename a field and this fails
loudly, instead of every downstream total quietly becoming zero.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROLLUPS = sorted((ROOT / "dashboard" / "sales" / "products").glob("rollup_*.json"))


def test_rollups_exist():
    assert ROLLUPS, "no rollup_*.json — the Sales Product API is the documented " \
                    "source for product questions (CLAUDE.md)"


@pytest.mark.parametrize("path", ROLLUPS, ids=lambda p: p.name)
def test_rollup_keeps_the_shape_the_joins_read(path):
    d = json.loads(path.read_text())
    assert "products" in d, f"{path.name}: top-level 'products' is what callers iterate"
    prods = d["products"]
    assert prods, f"{path.name}: no products — an empty feed reads as 'nothing sold'"

    for p in prods[:50]:
        assert "name" in p, "'name' is the join key against the recipe book"
        assert "weekly" in p, (
            "'weekly' is the per-week series. It is NOT 'weeks' — reading that "
            "returned 0 for every product and looked like a real answer.")
    weeks = [w for p in prods for w in p.get("weekly") or []]
    assert weeks, f"{path.name}: every product has an empty series"
    for w in weeks[:200]:
        assert {"we", "qty", "sales_ex"} <= set(w), (
            f"{path.name}: a week needs we/qty/sales_ex; got {sorted(w)}")


@pytest.mark.parametrize("path", ROLLUPS, ids=lambda p: p.name)
def test_quantities_are_numbers_not_strings(path):
    """A string qty sums to a TypeError if you're lucky and concatenates if not."""
    d = json.loads(path.read_text())
    for p in d["products"][:50]:
        for w in (p.get("weekly") or [])[:20]:
            assert isinstance(w["qty"], (int, float)), f"{p['name']}: qty is {type(w['qty'])}"
            assert isinstance(w["sales_ex"], (int, float)), f"{p['name']}: sales_ex not numeric"
