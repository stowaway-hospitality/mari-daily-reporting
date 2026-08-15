"""
The cost guard that never ran, and the venue filter that must not.

THE DEFECT
----------
`modules/invoices/resolve.py` documents, at length, the one thing a
supplier-code -> ProductID map has to get right:

    ILG 122-2867  "ALEHOUSE CRISP KEG"    -> 20487313 Summer Mid [Keg]    $184.94
    ILG 122-2858  "ALEHOUSE PREMIUM KEG"  -> 20487298 Draught Lager [Keg] $212.44

Both match /ALEHOUSE .* KEG/, the sensible guess is BACKWARDS, and they are
$27.50 apart. `is_suspect` is the guard: material in BOTH dollars and percent,
because Sprite drifts 22.2% on $0.42 and must pass while Alehouse errs 14.9% on
$27.50 and must fail — no single threshold separates those.

None of it ran. The only importers of `Resolver` and `is_suspect` were the
module's own tests. The live path, `build_costs.load_bridge()`, opened
product_map.csv itself and applied no cost check at all. A guard with no caller
is a comment.

WHAT CHANGED
------------
load_bridge imports `is_suspect` — imported, not restated, so the band can only
ever have one definition — and refuses to bridge a map row whose own recorded
Back Office and invoice costs are material apart both ways.

MEASURED FIRST: 0 of 190 rows are suspect. 70 carry both costs; 119 have no
`bo_cost` at all and cannot be checked. So it moves nothing today. That is the
point — it is armed before the next map row, not after it. The 119 unchecked
rows are the real remaining exposure and this file states the number so it
cannot quietly grow.

THE VENUE FILTER IS DELIBERATELY ABSENT, AND THAT WAS MEASURED
--------------------------------------------------------------
`Resolver.__init__` filters on venue; `load_bridge` does not, and 11 map rows
carry harry_gatos/marilynas and bridge unconditionally. Applying the filter
looks like the tidy fix. It is not:

    30 observations  Carrot Large [kg]          harry_gatos
    28               Cauliflower Florets [kg]   harry_gatos
     6               Broccolini [Bunch]         harry_gatos
     5               Capsicum                   marilynas
     4               Bocconcini                 harry_gatos
     4               Dry Slaw [kg]              marilynas
     2               Mayonnaise Kewpie 1kg      harry_gatos
     1 each          Pumpkin / Lettuce Mesculin / Corn Flour

82 of the 3,880 rows in costs.csv, across ten real products, all evidenced from
real invoices. Filtering them out freezes every one of them on a January seed
while its invoices sit unread — stale seeds under-cost, which is the flattering
direction, and it deletes evidence to enforce a distinction the data model does
not make. All three venues ring through ONE Lightspeed till: a ProductID is the
same box of cauliflower whichever door it came in. The venue column records
where a delivery landed, not which product it is.

The filter stays where it belongs — on `Resolver`, which resolves one invoice
line for one venue's ledger.
"""

import csv
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from core.domain import purchasable_id                                   # noqa: E402
from modules.invoices.resolve import is_suspect                          # noqa: E402
from modules.recipes.pipeline import build_costs                         # noqa: E402
from modules.recipes.pipeline.build_costs import load_bridge             # noqa: E402

PRODUCT_MAP = ROOT / "data" / "product_map.csv"
COSTS = ROOT / "data" / "costs.csv"


def _map_rows():
    return list(csv.DictReader(PRODUCT_MAP.open(encoding="utf-8-sig")))


# --- the guard is wired in -------------------------------------------------

def test_load_bridge_uses_resolve_is_suspect_not_a_second_copy_of_it():
    """One definition of the band, or the two drift apart.

    Asserted by identity: the name build_costs holds must BE the function
    resolve.py exports."""
    assert build_costs.is_suspect is is_suspect


def test_the_alehouse_pair_would_be_refused_and_sprites_drift_would_not():
    """The numbers the band exists for, both real, both measured 2026-07-17."""
    assert is_suspect(Decimal("184.94"), Decimal("212.44"))   # $27.50, 14.9%
    assert not is_suspect(Decimal("2.31"), Decimal("1.89"))   # $0.42, 22.2%


def test_a_wrong_product_row_does_not_reach_the_bridge(tmp_path):
    """A map row whose own recorded costs are $27.50 and 14.9% apart is far more
    likely to be the wrong product than reference-price drift, so it does not
    bridge. An unbridged bottle keeps its seed and is reported by audit_book; a
    WRONG bridge writes a wrong cost against a real SKU."""
    rows = _map_rows()
    good = load_bridge()
    bad = dict(rows[0])
    bad.update({"supplier": "ILG", "supplier_code": "122-2867",
                "product_id": "20487298", "product_name": "Alehouse Draught Lager [Keg]",
                "bo_cost": "184.94", "invoice_cost": "212.44"})
    # NOT under data/. A test that writes there is how `pytest` once published a
    # day's record and rewrote a history file — see cogs_blend.py's docstring.
    tmp = tmp_path / "product_map.csv"
    orig = build_costs.PRODUCT_MAP
    try:
        with tmp.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows + [bad])
        build_costs.PRODUCT_MAP = tmp
        with_bad = load_bridge()
    finally:
        build_costs.PRODUCT_MAP = orig
    key = purchasable_id("ILG", "122-2867")
    assert with_bad.get(key) == good.get(key), (
        "a cost-suspect row bridged anyway — the guard is not running")


def test_the_real_map_has_nothing_suspect_and_says_how_much_it_cannot_check():
    """0 suspect of 70 checkable, 119 with no Back Office cost recorded.

    The unchecked rows are the honest limit of this guard and the number is
    pinned so it cannot grow quietly."""
    rows = _map_rows()
    checkable = [r for r in rows if (r.get("bo_cost") or "").strip()
                 and (r.get("invoice_cost") or "").strip()]
    suspect = [r for r in checkable
               if is_suspect(Decimal(r["bo_cost"]), Decimal(r["invoice_cost"]))]
    assert not suspect, [(r["supplier_code"], r["product_name"]) for r in suspect]
    assert len(checkable) >= 70
    assert len(rows) - len(checkable) <= 125, (
        f"{len(rows) - len(checkable)} map rows carry no Back Office cost — the "
        f"guard cannot see them at all")


# --- the venue filter must NOT be there ------------------------------------

def test_harry_gatos_and_marilynas_map_rows_still_bridge():
    """The 11 rows a venue filter would have deleted."""
    bridge = load_bridge()
    missing = []
    for r in _map_rows():
        v = (r.get("venue") or "").strip()
        if v and v != "stowaway":
            key = purchasable_id(r["supplier"], r["supplier_code"])
            if f"lightspeed:{r['product_id'].strip()}" not in (bridge.get(key) or ()):
                missing.append(f"{key} -> {r['product_name']} [{v}]")
    assert not missing, "\n  ".join([""] + missing)


def test_those_rows_carry_real_evidence_that_a_filter_would_delete():
    """Not a principle — 82 dated, invoiced observations across ten products.

    If this ever drops toward zero the argument above has stopped being true
    and the decision should be revisited on the new numbers."""
    nonstow = {}
    for r in _map_rows():
        v = (r.get("venue") or "").strip()
        if v and v != "stowaway":
            nonstow[purchasable_id(r["supplier"], r["supplier_code"])] = \
                f"lightspeed:{r['product_id'].strip()}"
    n, products = 0, set()
    for r in csv.DictReader(COSTS.open(encoding="utf-8-sig")):
        for iid, pid in nonstow.items():
            if r["ingredient"] == pid and f"(via {iid})" in r["pack"]:
                n += 1
                products.add(pid)
    assert n >= 80, f"only {n} bridged observations from non-Stowaway map rows"
    assert len(products) >= 10, products
