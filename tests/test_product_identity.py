"""Product identity — the join key the stock ledger deducts on.

Two ways a till line silently fails to find its recipe, both measured here:

  1. The EMAILED export mangles non-ASCII to a literal '|'. A recipe keyed on
     "No Jalapeños" never matches "No Jalape|os" — it does not raise, it
     deducts nothing, and the variance comes out clean.
  2. A SKU RENAMED IN PLACE carries its new name on old sales in the history
     pull. Bread & Butter Pudding was a short-lived special whose SKU was later
     reused for Apple Crumble; without the register, every pudding ever sold
     deducts apple, oats and flour.

The register is hand-adjudicated. These tests hold the real adjudications.
"""

from __future__ import annotations

import csv
import glob
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from product_identity import canonical_name          # noqa: E402

MIX_FILES = sorted((ROOT / "data" / "product_mix").glob("*.json"))
DAILY_CSVS = sorted((ROOT / "data" / "products_daily").glob("*.csv"))

# The reused SKU. Committed exports — names as at the sale — put Bread & Butter
# Pud on exactly 2026-07-25, 07-26 and 08-01, and Apple Crumble from 07-30.
REUSED_CUTOVER = date(2026, 7, 30)


def test_mangled_spelling_is_repaired():
    assert canonical_name("No Jalape|os", date(2026, 8, 8)) == "No Jalapeños"
    assert (canonical_name("Dom P|rignon Champagne - Bottle", date(2026, 7, 19))
            == "Dom Pérignon Champagne - Bottle")


def test_unknown_names_pass_through_untouched():
    """A wrong join is worse than an unresolved one — never guess."""
    for name in ("Fresh is Best Lager - Pint", "Totally Made Up Item",
                 "Weird | Pipe | Name That Matches Nothing"):
        assert canonical_name(name, date(2026, 8, 14)) == name


def test_reused_sku_is_split_by_date_in_the_history_pull():
    before = canonical_name("Apple Crumble", date(2025, 3, 1), source_kind="history_pull")
    after = canonical_name("Apple Crumble", date(2026, 8, 5), source_kind="history_pull")
    assert before == "Bread & Butter Pud", (
        "the history pull reports old pudding sales as Apple Crumble; before the "
        "cutover they must be relabelled or they deduct the wrong dessert")
    assert after == "Apple Crumble"


def test_committed_exports_are_never_relabelled():
    """A committed export carries the name as at the sale. The fact beats the
    reconstruction — relabelling it would be inventing a rename."""
    assert canonical_name("Apple Crumble", date(2025, 3, 1),
                          source_kind="committed_export") == "Apple Crumble"


def test_no_mangled_names_survive_into_the_rollup():
    if not DAILY_CSVS:
        pytest.skip("data/products_daily/ not built yet")
    bad = []
    for path in DAILY_CSVS:
        with path.open() as f:
            for r in csv.DictReader(f):
                n = r["product_name"]
                if "|" in n:
                    bad.append(f"{path.name}:{r['date']}:{n}")
    assert not bad, (
        f"{len(bad)} row(s) still carry the export's mangled non-ASCII, e.g. "
        f"{bad[:3]}. Those never join to a recipe. Re-run "
        f"split_history_export.py to refresh data/product_spellings.csv.")


def test_the_two_desserts_never_share_a_day_under_one_name():
    """Bread & Butter Pud and Apple Crumble are different dishes. If the rollup
    ever shows the pudding's days under the crumble's name, the register has
    stopped being applied."""
    if not DAILY_CSVS:
        pytest.skip("data/products_daily/ not built yet")
    seen: dict[str, set[str]] = {}
    for path in DAILY_CSVS:
        with path.open() as f:
            for r in csv.DictReader(f):
                if r["product_name"] in ("Bread & Butter Pud", "Apple Crumble"):
                    seen.setdefault(r["product_name"], set()).add(r["date"])

    pud = seen.get("Bread & Butter Pud", set())
    crumble = seen.get("Apple Crumble", set())
    assert pud, "Bread & Butter Pud has vanished — the reused SKU collapsed into one name"
    assert min(crumble) >= REUSED_CUTOVER.isoformat(), (
        f"Apple Crumble appears on {min(crumble)}, before the {REUSED_CUTOVER} cutover. "
        f"Those are pudding sales wearing the crumble's name.")


def test_renamed_lines_keep_an_audit_trail():
    """Renaming without recording what the till actually said is unfalsifiable."""
    checked = 0
    for path in MIX_FILES:
        for p in json.loads(path.read_text())["products"]:
            if "name_as_reported" in p:
                assert p["name_as_reported"] != p["name"]
                checked += 1
    assert checked, ("no mix line carries name_as_reported — either nothing was "
                     "canonicalised, or the audit trail was dropped")
