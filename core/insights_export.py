"""
The one place that knows what a Lightspeed Sales-by-Product export looks like.

Lightspeed changed the export's column names mid-2026. Both shapes are in the
tree and both are real data:

    OLD  Position, Product Number, Product, Quantity, Percent of Quantity,
         Sale Amount, Percent of Sale Amount, Cost, Percent of Gross Profit
    NEW  Product Name, Product Quantity, $ Sales, Total Tax, Cost,
         % of Quantity, % of Sale Amount, Gross Profit %

The cutover was NOT clean — the week of 2026-07-06 has OLD on the 6th-10th and
the 12th, NEW on the 11th. A reader that knows only one shape reads the other as
a file of footer rows and emits an empty week without a word. That is exactly
what happened: build_products_weekly.py knew only NEW, so week-ending 2026-07-12
published $15,955 against $67,000 in the daily history — **$51,046 missing**,
and the dashboard showed a plausible-looking wrong number rather than a gap.

Other things live in data/insights_*.csv and must never be read as product rows:

  * data/insights_2026-07-11.csv is a ZIP ARCHIVE committed under a .csv name
    (c44c6cb) — the raw Lightspeed download bundle, never unpacked. It holds
    Marilyna's 2026-07-11 pull; note insights_mari_2026-07-11.csv is absent.
    csv.DictReader dies on its NUL bytes three frames deep, in
    _mean_large_pizza_cost, naming no file.
  * insights_hg_2026-07-12.csv is a REPORTING-GROUP export saved under a product
    filename. Read as products it would file group names as product names.
  * insights_<date>.csv (no venue) for 2026-07-03..05 is the Marilyna's daily
    category/payment feed — a different report that shares the prefix.

So: recognise both product shapes, name everything else, and never guess.

    rows = read_insights(path)          # canonical dicts
    InsightsSchemaError                 # unreadable or unknown -> a bug, stop
    WrongReportError                    # wrong report WITH data -> a miss, warn

Canonical row keys: name, qty, inc_gst, tax, cost.
`inc_gst` is INC-GST in both shapes — daily_aggregator.py:317 treats all of these
column names as Revenue_inc_gst, and check_hg_vs_master.py:47 divides the OLD
"Sale Amount" by 1.1. `tax` is 0.0 on OLD, which has no tax column; use ex_gst().
"""

from __future__ import annotations

import csv
from pathlib import Path

__all__ = ["InsightsSchemaError", "WrongReportError", "read_insights", "ex_gst"]


class InsightsSchemaError(Exception):
    """This file is not something we can read. Always a bug — stop."""


class WrongReportError(InsightsSchemaError):
    """A recognisable Lightspeed export, but the wrong report for this filename,
    and it HAS rows — so real product detail was not captured that day.

    Separated from InsightsSchemaError because it means someone exported the
    wrong report (a collection miss to chase), not that the code is broken.
    Callers usually warn loudly and carry on.

    An empty one of these is NOT an error: Stowaway is closed Mondays and Harry
    Gatos Tuesdays, and on a closed day Lightspeed serves a header-only file.
    Nothing was lost, so nothing is said.
    """


# label, name, qty, inc-GST, tax, cost. tax=None where the shape has no tax column.
_SHAPES = (
    ("new", "Product Name", "Product Quantity", "$ Sales", "Total Tax", "Cost"),
    ("old", "Product", "Quantity", "Sale Amount", None, "Cost"),
)

# Header -> which Lightspeed report it actually is. Listed so the error can name
# the report rather than shrug at an unknown header.
_OTHER_REPORTS = {
    "Reporting Group Name": "reporting-groups",
    "POS Category": "sales-by-category",
    "Staff Name": "sales-by-staff",
    "Date": "daily category/payment feed",
}


def read_insights(path) -> list[dict]:
    """Read one Sales-by-Product export into canonical rows.

    Footer/subtotal rows (no product name) are dropped, as every caller did
    already. Raises rather than returning [] on an unreadable file — an empty
    list from a real export and an empty list from a file we failed to parse
    must not look alike, because looking alike IS the defect this module exists
    to stop.
    """
    path = Path(path)

    head = path.open("rb").read(4096)
    if head.startswith(b"PK\x03\x04"):
        raise InsightsSchemaError(
            f"{path.name}: this is a ZIP archive, not a CSV — the raw Lightspeed "
            f"download bundle was committed without being unpacked")
    if b"\x00" in head:
        raise InsightsSchemaError(f"{path.name}: binary content (NUL bytes), not a CSV")

    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = [(h or "").strip() for h in (reader.fieldnames or [])]
        shape = next((s for s in _SHAPES if s[1] in fields and s[3] in fields), None)

        if shape is None:
            report = next((r for h, r in _OTHER_REPORTS.items() if h in fields), None)
            if report:
                # A closed day yields a header-only file of whatever report
                # Lightspeed felt like serving. No rows means nothing was lost.
                if not any(any((v or "").strip() for v in r.values()) for r in reader):
                    return []
                raise WrongReportError(
                    f"{path.name}: this is the {report} export, not sales-by-product "
                    f"— that day's product detail was never captured")
            raise InsightsSchemaError(
                f"{path.name}: unrecognised Sales-by-Product header {fields[:6]!r} — "
                f"add the shape to core/insights_export._SHAPES rather than letting "
                f"the week publish empty")

        _lbl, c_name, c_qty, c_inc, c_tax, c_cost = shape
        out = []
        for r in reader:
            name = (r.get(c_name) or "").strip()
            if not name:
                continue                                    # footer / subtotal
            out.append({
                "name": name,
                "qty": _num(r.get(c_qty)),
                "inc_gst": _num(r.get(c_inc)),
                "tax": _num(r.get(c_tax)) if c_tax else 0.0,
                "cost": _num(r.get(c_cost)),
            })
        return out


def ex_gst(row: dict) -> float:
    """Ex-GST for a canonical row. OLD carries no tax column, so fall back to
    /1.1 — the rule check_hg_vs_master.py:47 has always used on that shape."""
    inc, tax = row["inc_gst"], row["tax"]
    return (inc - tax) if tax else inc / 1.1


def _num(x) -> float:
    s = str(x or "").strip()
    if not s:
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = s.replace("(", "").replace(")", "").replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return -v if neg else v
