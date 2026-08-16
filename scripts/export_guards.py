"""Guards on a Lightspeed Insights export, before any number is believed.

Three things can arrive in the mailbox and none of them announces itself:

  1. a real product export                      -> aggregate it
  2. a header-only report (the venue was shut)  -> record CLOSED, not blank
  3. LAST WEEK'S report, re-sent                -> refuse, loudly

The third is the dangerous one, and it is not hypothetical. Harry Gatos'
exports for Monday 3 Aug and Monday 10 Aug 2026 are byte-identical: 51 rows,
"Unlimited Dumplings, 29, $684.00" on both, every figure equal to the cent. Two
trading Mondays do not produce identical files. One of those days is a copy,
and a copied day is worse than a missing one - a missing day is visible and
heals itself, a copied day looks like trade and sums into the week.

The second matters for a different reason: an empty export and a failed ingest
looked the same on screen. HG's nine closed Tuesdays and Sundays all delivered
the same header-only Reporting-Group file, and the week rendered "6 days" with
nothing saying why. Closed is a fact worth recording.

This module is deliberately separate from daily_aggregator.py, which executes
its work at import time and so cannot be imported by a test.
"""
from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

# The two shapes Lightspeed emails. A product export names the product; the
# reporting-group one does not, and carries no product detail at all.
PRODUCT_KEYS = ("Product Name", "Product")

# Lightspeed emails TWO product-report shapes and never says which is which.
#   A  Product Name, Product Quantity, $ Sales, Total Tax, Cost, ...
#   B  Position, Product Number, Product, Quantity, Percent of Quantity,
#      Sale Amount, Percent of Sale Amount, Cost, Percent of Gross Profit
# B arrived for Stowaway on 10 and 13 Aug 2026. Nothing rejected it and nothing
# read it properly either: the aggregator looks for "$ Sales"/"Product Name",
# found neither, and wrote a day that was real but far too small — 10 Aug came
# out at $933.59 ex against a file holding $2,438.04 inc. An UNDERstatement,
# which is the direction nobody notices, because a quiet Monday looks like a
# quiet Monday. Normalising B into A's field names is the whole fix.
SCHEMA_B_MAP = {
    "Product": "Product Name",
    "Quantity": "Product Quantity",
    "Sale Amount": "$ Sales",
}
RG_KEY = "Reporting Group Name"

# Columns a Lightspeed export may use to say WHICH DAY it covers. The product
# export carries none of them today — which is the whole reason a re-sent report
# could be filed under the wrong date — but the hourly export already has
# "Sale Closed Date", so the moment the scheduled report includes it, every file
# becomes self-identifying and this class of error ends.
DATE_KEYS = ("Sale Closed Date", "Sale Date", "Date", "Business Date", "Day")


class StaleExport(RuntimeError):
    """This export is byte-identical to another DATE's export."""


def read_rows(path: Path, normalise: bool = True) -> tuple[list[dict], list[str]]:
    """(rows, fieldnames). No rows is a legitimate answer: the venue was shut.

    Shape B is renamed into shape A so every caller downstream sees one export
    format. Set normalise=False to inspect what actually arrived.
    """
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    fields = reader.fieldnames or []
    if normalise and "Product Name" not in fields and "Product" in fields:
        rows = [{SCHEMA_B_MAP.get(k, k): v for k, v in r.items()} for r in rows]
        fields = [SCHEMA_B_MAP.get(f, f) for f in fields]
    return rows, fields


def is_schema_b(path: Path) -> bool:
    """True when the export arrived in the Position/Product Number shape."""
    _, fields = read_rows(path, normalise=False)
    return "Product Name" not in fields and "Product" in fields


def is_closed_day(path: Path) -> bool:
    """True when the export has a header and nothing else."""
    rows, _ = read_rows(path)
    return not rows


def is_product_level(path: Path) -> bool:
    _, fields = read_rows(path)
    return any(k in fields for k in PRODUCT_KEYS)


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def content_fingerprint(path: Path) -> str:
    """Hash of the day's TRADE, independent of row order and export shape.

    A byte hash was the original check and it very nearly failed. Lightspeed
    does not promise a stable order for rows that tie on the sort column: the
    same Harry Gatos day, re-pulled twice on 2026-08-17, came back with
    BBQ Pork Buns/Tonkotsu and four other equal-quantity pairs swapped. Same
    day, same cents, different bytes — so a re-sent report that happened to
    come back re-sorted would have sailed past assert_not_a_copy() and been
    written as a real day. The 10 Aug duplicate was caught on the luck of the
    copy being byte-exact.

    So fingerprint what cannot change without the trade changing: the set of
    (product, quantity, sales) lines, sorted. Footer/subtotal rows are dropped
    — they carry no product name and would otherwise let a total stand in for
    the detail. Normalising through read_rows() means a schema-A and a schema-B
    export of the SAME day also collide, which is correct: both are that day.

    This is deliberately the same rule as scripts/duplicate_export_guard.py,
    which has always been content-based. The two guards now agree, so CI and
    the runtime cannot disagree about what a duplicate is.
    """
    rows, _ = read_rows(path)
    parts = []
    for r in rows:
        name = (r.get("Product Name") or "").strip()
        if not name:
            continue                      # footer/subtotal row
        qty = (r.get("Product Quantity") or "").strip()
        sales = (r.get("$ Sales") or "").strip()
        parts.append(f"{name}|{qty}|{sales}")
    parts.sort()
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def assert_not_a_copy(path: Path, prefix: str, data_dir: Path | None = None) -> None:
    """Raise StaleExport if another DATE's export rings the same trade.

    Compares content, not bytes — see content_fingerprint(). Closed-day
    exports are exempt: they are identical to each other by nature and carry
    no revenue to duplicate.
    """
    if is_closed_day(path):
        return
    data_dir = data_dir or path.parent
    mine = content_fingerprint(path)
    for other in sorted(data_dir.glob(f"insights_{prefix}_*.csv")):
        if other.name == path.name:
            continue
        try:
            if is_closed_day(other):
                continue
            if content_fingerprint(other) == mine:
                raise StaleExport(
                    f"{path.name} rings the same products, quantities and cents "
                    f"as {other.name} — that is a re-sent report, not this day's "
                    f"trade. Two trading days do not tie to the cent. Writing it "
                    f"would duplicate a day's revenue into the week. Re-export "
                    f"this date from Lightspeed Insights.")
        except OSError:
            continue


def dates_in_export(path: Path) -> set[str]:
    """Every distinct date the file claims to cover. Empty set = it does not say.

    This is the difference between trusting an email's delivery time and reading
    the report itself. On 10 Aug 2026 a re-send of the 3 Aug report was filed as
    the 10th because the pipeline had nothing else to go on: target_date() in
    ingest_insights_email.py reads the EMAIL's Date header, and the product
    export contains no date at all.
    """
    rows, fields = read_rows(path)
    key = next((k for k in DATE_KEYS if k in fields), None)
    if not key:
        return set()
    out = set()
    for r in rows:
        v = (r.get(key) or "").strip()[:10]
        if len(v) == 10 and v[4] in "-/" and v[7] in "-/":
            out.add(v.replace("/", "-"))
    return out


def assert_export_is_for(path: Path, target: str) -> None:
    """Refuse an export whose own rows say they belong to a different day.

    Silent when the report does not carry a date — this cannot invent one — so
    it is safe to ship before the Lightspeed schedules are updated, and starts
    protecting the moment they are.
    """
    claims = dates_in_export(path)
    if not claims:
        return
    if claims != {target}:
        raise StaleExport(
            f"{path.name} is filed as {target} but its rows say "
            f"{', '.join(sorted(claims))}. The export was re-sent and stamped "
            f"with the email's delivery date. Refusing it.")


def hourly_total_inc(hourly_path: Path) -> float:
    """The day's takings per the HOURLY export — an independent path to the
    same number, and the only file Lightspeed sends that names its own date."""
    rows, fields = read_rows(hourly_path)
    col = next((c for c in fields if c and "Inc" in c), None)
    if not col:
        return 0.0
    tot = 0.0
    for r in rows:
        v = (r.get(col) or "").replace("$", "").replace(",", "").strip()
        try:
            tot += float(v)
        except ValueError:
            pass
    return tot


def product_total_inc(product_path: Path) -> float:
    """The day's takings per the PRODUCT export, footer/subtotal rows dropped."""
    rows, _ = read_rows(product_path)
    tot = 0.0
    for r in rows:
        if not (r.get("Product Name") or "").strip():
            continue                      # footer/subtotal row
        v = (r.get("$ Sales") or "").replace("$", "").replace(",", "").strip()
        try:
            tot += float(v)
        except ValueError:
            pass
    return tot


def reconcile_against_till(product_path: Path, hourly_path: Path,
                           tolerance_pct: float = 10.0) -> tuple[bool, str]:
    """Do the two independent exports for this day agree?

    THE point of this: until now nothing ever compared our numbers to the till's
    own. Two weeks of wrong Harry Gatos figures sat in the reporting because the
    only check was "did a file arrive". These two reports are produced
    separately by Lightspeed, so agreement is real evidence and disagreement is
    a fact worth stopping for.

    Returns (ok, message). Tolerance is a percentage: the hourly report counts
    the whole till while the product report can exclude voids and open-price
    oddities, so they are close, not identical.
    """
    if not hourly_path.exists():
        return True, "no hourly export for this day — nothing to reconcile against"
    prod, hourly = product_total_inc(product_path), hourly_total_inc(hourly_path)
    if hourly <= 0:
        return True, "hourly export carries no revenue — skipped"
    gap = prod - hourly
    pct = abs(gap) / hourly * 100
    msg = (f"product ${prod:,.2f} vs till ${hourly:,.2f} inc "
           f"({gap:+,.2f}, {pct:.1f}%)")
    return (pct <= tolerance_pct), msg
