"""No bottle may cost less than the price ILG publish for it.

data/ilg_pricebook.csv is the only price in this system that neither we nor
Lightspeed derived — a supplier stating in writing what a bottle costs. Every
other number has an author with an incentive or an error mode: Back Office costs
are typed by a person, Lightspeed's recipe costs are Lightspeed's own arithmetic,
and an invoice line has to survive a pack-size reading first.

So it is a floor. Buying moves a bottle by ~15% either way (measured: over the
products where a Back Office cost and a book line agree on name and size, the
ratio runs 0.85–1.12, median 1.02). A cost 40%+ under book is not a keen buy, it
is a misread pack or a derived figure pretending to be an observation.

THE CASE THAT PROVES IT
-----------------------
Havana Club sat in this book at $29.09 a bottle for months. ILG publish $49.20.
It survived because the only reference it was ever checked against was the very
number under suspicion — its own seed. 0.59x. This test is that check, made
standing.

The join is the ILG code from data/product_map.csv, never the name: "Havana 3yr
[700ml]" and "Havana Club 700ml 3yo." are the same bottle and no normaliser will
ever say so.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from audit_book import PRICEBOOK_FLOOR  # noqa: E402

PRICEBOOK = ROOT / "data" / "ilg_pricebook.csv"
PRODUCT_MAP = ROOT / "data" / "product_map.csv"
COSTS = ROOT / "data" / "costs.csv"


def _joined():
    """(ratio, product, ours, book, code) for every ILG bridge the book covers."""
    if not (PRICEBOOK.exists() and PRODUCT_MAP.exists() and COSTS.exists()):
        return None
    book = {}
    for r in csv.DictReader(PRICEBOOK.open(encoding="utf-8-sig")):
        try:
            book[r["code"].replace("-", "")] = (float(r["size_ml"]),
                                                float(r["book_price_unit"]))
        except (TypeError, ValueError):
            continue
    latest = {}
    for r in csv.DictReader(COSTS.open(encoding="utf-8-sig")):
        if (r.get("unit") or "") != "ml":
            continue
        k, d = r["ingredient"], r["observed_on"]
        if k not in latest or d >= latest[k][0]:
            try:
                latest[k] = (d, float(r["cost_per_unit"]))
            except (TypeError, ValueError):
                pass
    out = []
    for r in csv.DictReader(PRODUCT_MAP.open(encoding="utf-8-sig")):
        if (r.get("supplier") or "") != "ILG":
            continue
        hit = book.get((r.get("supplier_code") or "").replace("-", "").rstrip("P"))
        got = latest.get(f"lightspeed:{(r.get('product_id') or '').strip()}")
        if not hit or not got:
            continue
        size_ml, bk = hit
        ours = got[1] * size_ml
        if bk > 0 and ours > 0:
            out.append((ours / bk, r.get("product_name") or "", ours, bk,
                        r.get("supplier_code")))
    return out


def test_no_bridged_bottle_is_costed_under_the_published_price():
    rows = _joined()
    if rows is None:
        return                      # price book not extracted here — see audit INFO
    bad = [f"{nm or code}: ours ${o:.2f} vs book ${b:.2f} ({r:.2f}x, {code})"
           for r, nm, o, b, code in sorted(rows) if r < PRICEBOOK_FLOOR]
    assert not bad, (
        "costed below ILG's own published price — a pack misread or a derived "
        "figure posing as an observation:\n  " + "\n  ".join(bad))


def test_the_join_actually_covers_something():
    """A floor that matches nothing is not a floor.

    Guards the failure mode where a column rename or a code-format change makes
    the join silently empty and this file starts passing for the wrong reason —
    the same shape as the audit rule that read a gitignored file and reported
    'no findings'.
    """
    rows = _joined()
    if rows is None:
        return
    assert len(rows) >= 25, f"only {len(rows)} ILG bridges joined to the price book"


def test_the_havana_seed_would_have_failed_this():
    """The number this test exists for, asserted directly.

    $29.09 against a book price of $49.20 is 0.59x. If the floor ever gets
    loosened past that, this says so.
    """
    assert 29.09 / 49.20 < PRICEBOOK_FLOOR
