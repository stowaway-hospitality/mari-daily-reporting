"""
An export we cannot read must be a loud failure, not a quiet empty week.

THE GAP
-------
scripts/build_products_weekly.py read the product name as

    row.get("Product Name") or row.get("ProductName")

Lightspeed renamed that column (and "$ Sales", and "Product Quantity") on
2026-07-13. On an OLDER file every key missed, so `name` came back "" and the
row was dropped by the `if not name: continue` guard that exists to skip footer
rows. Eleven committed files went in and nothing came out.

Week ending 2026-07-12 published $9,183 for Stowaway against $42,006 in the
daily history. $54,236 ex-GST in total was missing from the Products view, and
nothing anywhere said so — a per-key `or` chain cannot tell "this column is
absent because the schema is older" from "this row is a footer".

There is also a THIRD shape under the same filename pattern: Harry Gatos emails
a reporting-group-level export. Folding that in would invent products named
after reporting groups and double-count HG revenue, so it is recognised and
skipped rather than parsed.

And separately: data/insights_2026-07-11.csv was a ZIP archive committed under a
.csv name (c44c6cb) — the ingest base64'd the attachment without unwrapping it.
csv.DictReader raises `_csv.Error: line contains NUL` from wherever that file is
first opened, which took out scripts/build_site.py AND the recipe build.

WHAT THIS GUARDS
----------------
- both product schemas are recognised, by header rather than by key roulette
- the reporting-group export is skipped deliberately, not by accident
- an unknown header raises instead of silently yielding nothing
- no file in data/ is an archive wearing a .csv extension
- and the invariant that matters: the old-schema files still carry revenue
"""

import csv
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from build_products_weekly import (                                  # noqa: E402
    PRODUCT_SCHEMAS, RG_LEVEL_KEY, product_schema,
)

DATA = ROOT / "data"
NEW = ["Product Name", "Product Quantity", "$ Sales", "Total Tax", "Cost"]
OLD = ["Position", "Product Number", "Product", "Quantity",
       "Percent of Quantity", "Sale Amount", "Percent of Sale Amount", "Cost"]
RG = ["Reporting Group Name", "Total Quantity", "$ Sales", "Total Tax", "Cost"]


def test_the_current_export_is_recognised():
    assert product_schema(NEW, "x.csv") is PRODUCT_SCHEMAS[0]


def test_the_older_export_is_recognised_rather_than_read_as_footers():
    """The whole finding. Every row of these files was silently dropped."""
    s = product_schema(OLD, "x.csv")
    assert s is PRODUCT_SCHEMAS[1]
    assert (s["name"], s["inc"], s["qty"]) == ("Product", "Sale Amount", "Quantity")


def test_the_older_export_has_no_tax_column_so_the_caller_grosses_up():
    """There is no Total Tax on the old shape. The reader must know that and
    divide by 1.1, not read a 0 and publish inc-GST as ex-GST."""
    assert product_schema(OLD, "x.csv")["tax"] is None
    assert product_schema(NEW, "x.csv")["tax"] == "Total Tax"


def test_the_reporting_group_export_is_skipped_not_parsed():
    """HG emails this under the same filename pattern. Its first column is a
    GROUP, not a product — parsing it would invent products called 'Tap Beer'
    and double-count HG revenue against its own product rows."""
    assert product_schema(RG, "x.csv") is None
    assert RG_LEVEL_KEY == "Reporting Group Name"


def test_an_unknown_header_is_refused_loudly():
    """The next rename must stop the build, not empty a week."""
    with pytest.raises(SystemExit) as e:
        product_schema(["Item", "Units", "Revenue"], "data/insights_stow_2027-01-01.csv")
    msg = str(e.value)
    assert "UNRECOGNISED INSIGHTS EXPORT SCHEMA" in msg
    assert "insights_stow_2027-01-01.csv" in msg    # says WHICH file


def test_no_committed_csv_is_actually_an_archive():
    """data/insights_2026-07-11.csv was a ZIP under a .csv name. One bad file
    took out the site build and the recipe build, and neither said why."""
    bad = [p.relative_to(ROOT) for p in DATA.rglob("*.csv")
           if p.read_bytes()[:4] in (b"PK\x03\x04", b"PK\x05\x06")]
    assert not bad, f"archive(s) wearing a .csv extension: {bad}"


def test_no_committed_csv_carries_a_nul_byte():
    """The symptom the ZIP presented as. Any NUL means the file is not text and
    csv.DictReader will die on it wherever it is first opened."""
    bad = [p.relative_to(ROOT) for p in DATA.rglob("*.csv") if b"\x00" in p.read_bytes()]
    assert not bad, f"NUL byte in: {bad}"


def _insights_files():
    for p in sorted(DATA.glob("insights_*.csv")):
        m = re.match(r"insights_(stow|hg)_(\d{4}-\d{2}-\d{2})\.csv$", p.name)
        if m:
            yield p, m.group(2)


def test_every_committed_export_matches_a_known_schema():
    for p, _d in _insights_files():
        with p.open(encoding="utf-8-sig") as f:
            product_schema(csv.DictReader(f).fieldnames or [], str(p))   # raises if not


def test_the_old_schema_files_still_carry_revenue():
    """The regression, on the real files. Before the fix these parsed to zero
    rows each. Refuse to let that be true again."""
    empty = []
    for p, dstr in _insights_files():
        with p.open(encoding="utf-8-sig") as f:
            rd = csv.DictReader(f)
            s = product_schema(rd.fieldnames or [], str(p))
            if s is None or s is PRODUCT_SCHEMAS[0]:
                continue                              # current shape, covered elsewhere
            named = [r for r in rd if (r.get(s["name"]) or "").strip()]
        if not named:
            empty.append(p.name)
    assert not empty, f"old-schema export parsed to zero product rows: {empty}"
