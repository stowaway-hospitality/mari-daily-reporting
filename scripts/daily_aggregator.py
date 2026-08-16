"""
Daily aggregator — runs each morning after Insights CSV lands.

Inputs (per venue):
  - data/insights_<prefix>_<yyyy-mm-dd>.csv   (Lightspeed Insights daily report;
                                                may be a ZIP wrapping the CSV;
                                                supports Sales-Summary and
                                                Sales-by-Product schemas)
  - data/deputy_<prefix>_<yyyy-mm-dd>.json    (Deputy API daily wages, ex-super;
                                                salaried costs synthesized by
                                                daily_deputy_pull.py)
  - data/payments_<prefix>_<yyyy-mm-dd>.csv   (OPTIONAL — Insights Sales by
                                                Payment Type; gives real Uber
                                                Eats revenue.)
  - data/manual/uber_direct.json              (OPTIONAL — Zak-entered weekly
                                                Uber Direct fee totals,
                                                amortized across the week.)

  Marilynas falls back to unprefixed insights_<date>.csv / deputy_<date>.json
  for backwards compat with the existing daily_pull workflow.

2026-07-12 — aligned with the LIVE weekly-report pipeline
(Daily Sales/skill-patches/weekly-report/scripts — not the stale packaged
skill):
  - Venue attribution (matches build_rich_rollups.py):
      * Marilynas carve-out EXCLUDES 'Delivery Kitchen' (removed 2026-05-13:
        it IS Stow Kitchen food on Uber — revenue and labour belong with
        Stow Kitchen).
      * Symmetric cross-venue food reallocation: Stow rows tagged
        'Harry Gatos Food' -> HarryGatos; HG rows tagged 'Stow Food' ->
        Stowaway. The aggregator reads the SIBLING venue's insights CSV for
        the same date (when present) and pulls its reallocated rows in.
  - Dept split (matches classify_rg_to_dept in build_weekly_report.py):
      Kitchen = explicit RG set (+ Desserts + Delivery Kitchen per the
      canonical dept-takings table); FOH/bev is the CATCH-ALL — anything
      not Kitchen (incl. Unmapped/Modifiers) is FOH.
  - Wages are grossed up by 12%% super (venues.SUPER_RATE) so every wage
    figure is inc-super, same as wages_weekly.csv TotalWagesIncSuper.
    Deputy JSON now carries an 'Admin' dept (90/10 split, from the pull).
    Marilynas total wages INCLUDE Driver (matches Mari Venue Total in the
    weekly canon); Driver dollars also surface in the delivery lane.
  - Marilynas Net Wage %%: when real/estimated Uber fees are known, net
    takings = rev_ex - uber fees, and net_wage_pct = wages / net takings.
    Weekly canon: Net is the operationally meaningful number.
  - History CSV is NO LONGER trimmed to 90 days — full history is kept
    (backfilled from the product masters via scripts/backfill_history.py).

Kitchen / FOH split classification comes from scripts/product_dept_map.json —
generated from the LIVE reporting_group_mapping.csv + the rules above.
DO NOT hand-edit keyword rules here; regenerate the map so daily and weekly
reporting stay consistent. If the CSV carries a Category / Reporting Group
column it wins over the product-name lookup.

Footer totals rows (empty product name) are dropped before any summing —
the scheduled Insights CSV ends with one and it doubles revenue otherwise.

Output:
  - data/<prefix>_daily_<yyyy-mm-dd>.json   (per-day rollup with alerts)
  - data/<prefix>_daily_history.csv         (full history, backfilled)
  - data/product_mix/<prefix>_<yyyy-mm-dd>.json
        The FULL daily product mix — every till line, untruncated, with units,
        both GST bases and the venue attribution already applied. The daily
        rollup's `top_products` stays the top 20 because it is a dashboard
        panel; the stock ledger reads the mix file. See write_product_mix()
        and INVENTORY_ARCHITECTURE.md.

CLI:
  python daily_aggregator.py                        # yesterday, Mari
  python daily_aggregator.py 2026-07-11             # specific date, Mari
  python daily_aggregator.py --venue stowaway 2026-07-11
  python daily_aggregator.py --venue harry 2026-07-11
  python daily_aggregator.py --venue stowaway --mix-only 2026-07-11
        Write ONLY the product-mix file and stop — no daily record, no history
        CSV. This is how the backfill rebuilds history without touching the P&L.
"""

from __future__ import annotations
import csv, io, json, os, sys, zipfile
from pathlib import Path
from datetime import date, timedelta, datetime

sys.path.insert(0, str(Path(__file__).parent.parent))   # repo root -> core/, modules/
sys.path.insert(0, str(Path(__file__).parent))          # scripts/ -> export_guards

from core import venues as V
from wage_model import super_lookup
# The two cost sources and the blend all live in cogs_blend, so they can be
# imported without running this script. See that module's docstring.
from cogs_blend import (COSTED_BOOK, _load_book_costs,   # noqa: F401
                        _load_our_costs, blend_reported_cogs, book_cost)
# Product identity across renames + the emailed export's mangled non-ASCII.
# Used for the stock-ledger mix only; the P&L path keeps the raw name.
from product_identity import canonical_name

REPO_ROOT = Path(os.environ.get("REPO_ROOT", "."))
DATA_DIR = REPO_ROOT / "data"
BASELINES_DIR = REPO_ROOT / "baselines"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEPT_MAP_FILE = Path(__file__).parent / "product_dept_map.json"

# Uber Eats marketplace commission. 30% verified against the Uber Payouts page
# (weekly-report skill, known-margins.md). NOTE: marketing + tablet fees are
# NOT included here — the weekly report nets those out separately from
# merchants.ubereats.com. This lane is the commission estimate only.
UBER_COMMISSION_RATE = 0.30

# Super gross-up: Deputy Cost is ex-super; weekly canon reports inc-super.
SUPER_MULT = 1.0 + V.SUPER_RATE


def read_insights_csv_text(path: Path) -> str:
    """Return CSV text from an Insights payload that may be a raw CSV or a ZIP."""
    raw = path.read_bytes()
    if raw[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            csv_members = [m for m in zf.namelist() if m.lower().endswith(".csv")]
            if not csv_members:
                raise ValueError(f"ZIP payload has no .csv members: {zf.namelist()}")
            largest = max(csv_members, key=lambda m: zf.getinfo(m).file_size)
            print(f"  Unwrapped ZIP -> using member {largest!r} ({zf.getinfo(largest).file_size} bytes)")
            return zf.read(largest).decode("utf-8-sig", errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


def parse_num(x) -> float:
    """Parse a Kounta-Insights currency/number cell."""
    if x is None:
        return 0.0
    s = str(x).strip()
    if not s:
        return 0.0
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    s = s.replace("$", "").replace(",", "").replace("%", "").strip()
    if not s:
        return 0.0
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return -v if negative else v


def col(r: dict, *candidates: str) -> str:
    """First non-empty value across candidate column names."""
    for c in candidates:
        v = r.get(c)
        if v not in (None, ""):
            return v
    return ""


# --------------------------------------------------------------
# Product -> department classification (LIVE weekly-report canon)
#   'f'   food / Kitchen
#   'b'   bev / FOH (CATCH-ALL — anything not otherwise classified)
#   'm'   Marilynas ride-on (Stow POS rows that are Mari P&L)
#   'hgf' Harry Gatos Food rung on the Stow POS -> HarryGatos Kitchen
#   'stf' Stow Food rung on the HG POS        -> Stowaway Kitchen
# --------------------------------------------------------------
_DEPT_MAP = None

def _load_dept_map():
    global _DEPT_MAP
    if _DEPT_MAP is None:
        if DEPT_MAP_FILE.exists():
            with DEPT_MAP_FILE.open() as f:
                _DEPT_MAP = json.load(f)
        else:
            print(f"WARNING: {DEPT_MAP_FILE} missing — no food/bev split possible")
            _DEPT_MAP = {"*": {}, "stow": {}, "hg": {}}
    return _DEPT_MAP

MARILYNAS_RGS = {
    "marilyna's pizza", "marilynas pizza",
    "marilyna's soft drinks", "marilynas soft drinks",
    "add-ons - pizza", "dine-in pizza",
    "delivery alcohol",
    # 'delivery kitchen' REMOVED 2026-05-13 — it's Stow Kitchen food on Uber.
    # 'delivery cocktails' REMOVED 2026-07-16 (Zak) — it's STOW revenue. Alcohol
    # on delivery is Mari's, cocktails on delivery are the bar's. They sat in the
    # same line here purely because both had "delivery" in the name.
}
# NOTE — this set is P&L ATTRIBUTION: whose till-line is this? It is deliberately
# WIDER than the weekly-report skill's "Marilynas-strict" set
# (references/reporting-groups.md), which excludes Dine-in Pizza as
# "substitutable, not incremental". Both are right: strict answers "what would we
# lose if Mari closed?", this answers "whose revenue is it?". Don't reconcile them.
# A Mari cross-check gap below this (inc-GST) is modifier/add-on noise — a single
# add-on reporting differently between the till and her export — NOT a reporting
# GROUP being dropped/gained. Real filter drift historically moved $235-$612 (whole
# groups). Below the floor we don't raise MARI FILTER DRIFT, so the health panel
# isn't amber over $4.50 (e.g. 'Add Chorizo' on 2026-07-31). (2026-08-01)
MARI_DRIFT_MIN = 20.0

FOOD_RGS = {'big plates','small plates','kitchen specials','salads','desserts','kids meals','kids',
            'add-ons - kitchen','delivery kitchen','sides','mains','snacks','yum cha','staff dinners'}
HG_FOOD_RG = 'harry gatos food'
STOW_FOOD_RG = 'stow food'


def _norm_rg(rg: str) -> str:
    k = (rg or '').strip().lower()
    if k.endswith(' [harrys]'):
        k = k[:-len(' [harrys]')]
    return k


def _rg_dept(rg: str, venue_key: str) -> str | None:
    """RG-level classification. Returns None when the RG is unknown/blank."""
    k = _norm_rg(rg)
    if not k:
        return None
    if venue_key == "stowaway":
        if k in MARILYNAS_RGS:
            return 'm'
        if k == HG_FOOD_RG:
            return 'hgf'
    if venue_key == "harry" and k == STOW_FOOD_RG:
        return 'stf'
    if k in FOOD_RGS or k in (HG_FOOD_RG, STOW_FOOD_RG):
        return 'f'
    return 'b'   # FOH catch-all — matches classify_rg_to_dept in the weekly report


# Products the generated map has never heard of (2026-07-17).
#
# product_dept_map.json is built from the weekly report's
# reporting_group_mapping.csv — a HISTORICAL aggregate. A product missing from
# it falls through to the 'b' FOH catch-all, and for a Marilyna's product that
# is silently a DOUBLE COUNT: her report contains it, so she banks it, and Stow
# doesn't recognise it as 'm', so Stow never strips it. Both venues bill it.
#
# '$60 BANQUET' — Mari's (Zak, 2026-07-17). $54.55 ex every time it sells;
# caught reconciling against Lightspeed's own site footer, which is the only
# number in this pipeline that isn't derived from our own code. Found on 2 of
# the 11 days we hold — ~$3,620/yr. Its sibling '$45 FEAST' IS mapped to 'm',
# which is exactly why nobody noticed the gap.
#
# This is the mirror of the Mari coverage leak: that one had the classifier
# saying "Mari's" when her report didn't have it; this has her report saying
# "Mari's" when the classifier doesn't. Same root — Stow strips by CLASSIFIER
# while Mari counts by REPORT, and any daylight between the two definitions
# leaks money one way or doubles it the other.
#
# Proper fix is upstream in reporting_group_mapping.csv, which lives in the
# weekly-report skill and is read-only from here. This overlay survives a map
# regeneration; delete an entry once the source knows about it.
PRODUCT_OVERRIDES = {
    "$60 BANQUET": "m",
    # Family-size El Patron and Jimmy Jury, added to the register after the last
    # product-export rebuild of product_dept_map.json. Every OTHER size of both
    # pizzas maps to 'm' (Regular, Large, Gluten-free, Solo Combo, Wings Deal,
    # [Dine-in]); only these two were missing, so they fell through
    # classify_product() to the 'b' FOH catch-all and Marilyna's revenue was
    # billed to Stowaway — $89.70 inc across 2026-08-06 and 2026-08-08.
    # The HG-branded pizzas are named explicitly ("El Patron Pizza [HG]") and
    # stay 'f', so there is no ambiguity about who owns an unsuffixed one.
    # These live HERE and not in the JSON because build_dept_map.py REGENERATES
    # that file wholesale from a Lightspeed product export — an edit there is
    # erased by the next rebuild, while PRODUCT_OVERRIDES is consulted first and
    # survives it. Remove them once a fresh export carries them. (2026-08-09)
    "Family El Patron": "m",
    "Family Jimmy Jury": "m",
}


def classify_product(row: dict, product_name: str, venue_key: str) -> str:
    """-> 'f' | 'b' | 'm' | 'hgf' | 'stf'.

    Prefers an explicit Reporting Group / Category column when the CSV has
    one; otherwise resolves product name through the canonical map, falling
    back to 'b' (FOH catch-all, same as the weekly classifier).
    """
    rg = col(row, "Reporting Group Name", "Reporting Group", "Category")
    if rg:
        d = _rg_dept(rg, venue_key)
        if d is not None:
            return d
    m = _load_dept_map()
    vkey = {"stowaway": "stow", "harry": "hg"}.get(venue_key)
    if vkey is None:
        return 'f'   # Mari: everything is Kitchen; split not used
    pn = (product_name or '').strip()
    if pn in PRODUCT_OVERRIDES:
        return PRODUCT_OVERRIDES[pn]
    return m.get(vkey, {}).get(pn) or m.get("*", {}).get(pn) or 'b'


# --------------------------------------------------------------
# CLI parsing
# --------------------------------------------------------------
venue_key = "marilynas"
target = None
mix_only = False
insights_override = None
args = sys.argv[1:]
i = 0
while i < len(args):
    a = args[i]
    if a == "--venue":
        venue_key = args[i + 1]
        i += 2
        continue
    if a == "--insights-file":
        # Read the day's product export from an explicit path instead of
        # resolving data/insights_<prefix>_<date>.csv. Used by the product-mix
        # history backfill, whose per-day files live in data/insights_history/
        # so that adding two years of them cannot silently restate
        # products_weekly.csv (which prefers daily files over its Looker
        # backfill wherever a daily file exists).
        insights_override = Path(args[i + 1])
        i += 2
        continue
    if a == "--mix-only":
        # Write data/product_mix/<prefix>_<date>.json and stop. Used by the
        # backfill so historical mixes can be rebuilt WITHOUT regenerating
        # (and risking) the daily P&L records or the history CSV.
        mix_only = True
        i += 1
        continue
    try:
        target = date.fromisoformat(a)
    except ValueError:
        pass
    i += 1

if target is None:
    target = date.today() - timedelta(days=1)

cfg = V.get(venue_key)
prefix = cfg["file_prefix"]
lanes = set(cfg["lane_config"])
split_venue = venue_key in ("stowaway", "harry")   # Mari is Kitchen-only, no split
print(f"Aggregating {venue_key} ({cfg['display_name']}) for: {target.isoformat()}")


def resolve(*candidates: Path) -> Path | None:
    for c in candidates:
        if c.exists():
            return c
    return None


# Export shape/duplication guards live in their own module so they can be
# tested: this file does its work at import time and cannot be imported.
from export_guards import (SCHEMA_B_MAP, StaleExport,   # noqa: E402
                           assert_export_is_for, assert_not_a_copy,
                           reconcile_against_till)


def load_product_rows(path: Path):
    """Parse an Insights product CSV -> (rows, fieldnames), footer dropped.

    An export with no data rows is a CLOSED DAY, not a failure: Lightspeed
    emails a header-only report when a venue did not trade. It is returned
    empty and the caller records the day as closed rather than blank, so that
    "we know they were shut" and "we never got the numbers" stop looking the
    same on a screen.
    """
    csv_text = read_insights_csv_text(path)
    reader = csv.DictReader(io.StringIO(csv_text))
    all_rows = list(reader)
    fieldnames = reader.fieldnames or []
    # Lightspeed sends two product-report shapes. Normalise the second one into
    # the first's field names, or the day is read partially and silently: see
    # SCHEMA_B_MAP in export_guards.py and Stowaway 10/13 Aug 2026.
    if "Product Name" not in fieldnames and "Product" in fieldnames:
        print(f"  {path.name}: alternate export shape (Position/Product Number) "
              f"— normalising to the standard field names.")
        all_rows = [{SCHEMA_B_MAP.get(k, k): v for k, v in r.items()} for r in all_rows]
        fieldnames = [SCHEMA_B_MAP.get(f, f) for f in fieldnames]
    if any(c in fieldnames for c in ("Product Name", "Product")):
        footer_rows = [r for r in all_rows
                       if not (r.get("Product Name") or r.get("Product") or "").strip()]
        if footer_rows:
            footer_rev = sum(parse_num(col(r, "Revenue_inc_gst", "$ Sales", "Sales",
                                           "Sale Amount", "Total Sales"))
                             for r in footer_rows)
            print(f"  Dropped {len(footer_rows)} footer/subtotal row(s) with no "
                  f"product name (${footer_rev:,.2f} inc-GST)")
            all_rows = [r for r in all_rows
                        if (r.get("Product Name") or r.get("Product") or "").strip()]
    if not all_rows:
        # CLOSED DAY, not a failure. Said out loud so the log distinguishes it
        # from an export that never arrived — they looked identical before.
        print(f"  {path.name}: header only, no rows — treating {path.name[-14:-4]} "
              f"as a CLOSED day for this venue.")
    return all_rows, fieldnames


def row_rev(r):
    return parse_num(col(r, "Revenue_inc_gst", "$ Sales", "Sales", "Sale Amount", "Total Sales"))


def row_cogs(r):
    c = parse_num(col(r, "COGS", "Cost", "Cost of Goods Sold"))
    # Guard against Lightspeed recipe-cost typos (e.g. Serpents Kiss Schooner
    # at $145,158.75/unit, Sep 2025): absurd costs are treated as missing.
    if c > max(5 * row_rev(r), 500):
        return 0.0
    return c


def row_rev_ex(r, basis: str) -> float:
    """Per-row ex-GST revenue on the basis the DAY settled on, so the mix sums
    to the day's ex-GST revenue by construction rather than by luck."""
    if basis == "explicit_net_column":
        return parse_num(col(r, "Revenue_net", "NetRevenue", "Net Sales"))
    if basis == "inc_minus_tax":
        return row_rev(r) - parse_num(col(r, "Total Tax", "GST", "Tax"))
    return row_rev(r) / 1.1


MIX_DIR = DATA_DIR / "product_mix"
MIX_SCHEMA = "product_mix/1"
MIX_TOLERANCE = 0.01          # cents; the mix IS the day, it must tie exactly


def write_product_mix(entries, *, venue, prefix, day, source_file, ex_basis,
                      source_kind, sibling_available, expected_inc, expected_ex):
    """Persist the FULL daily product mix — every line off the till, untruncated.

    WHY THIS FILE EXISTS (INVENTORY_ARCHITECTURE.md, prerequisite to the stock
    ledger): the daily record's `top_products` is deliberately the top 20 by
    revenue, because that is a dashboard panel. Deducting stock from a
    truncated mix under-deducts SILENTLY AND FOREVER — on-hand drifts down
    slower than reality and every variance is wrong in the flattering
    direction, which is exactly the class of error this repo treats as
    dangerous. So the untruncated mix gets its own file, and the daily record
    keeps its 20.

    Rows are written VERBATIM, one per till line, in the order the day was
    ranked. Modifiers are their own lines in the Insights export and stay
    their own lines here (deduct them; don't double-count the base dish).
    Consumers that want a per-product total must sum by name themselves.

    Attribution is INHERITED, never re-derived: `entries` comes off the same
    venue-attributed `rows` the P&L is built from, so Mari's slice and the
    cross-venue food reallocation are already resolved.

    Reconciliation is written into the file rather than asserted, so a day that
    does not tie is visible to CI and to any consumer instead of aborting the
    P&L run. `reconciled: false` means DO NOT deduct stock from this day.
    """
    total_inc = round(sum(e["rev_inc"] for e in entries), 2)
    total_ex = round(sum(e["rev_ex"] for e in entries), 2)
    gap_inc = round(total_inc - round(expected_inc, 2), 2)
    gap_ex = round(total_ex - round(expected_ex, 2), 2)
    reconciled = abs(gap_inc) <= MIX_TOLERANCE and abs(gap_ex) <= MIX_TOLERANCE

    doc = {
        "schema": MIX_SCHEMA,
        "venue": venue,
        "prefix": prefix,
        "date": day.isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_file": source_file,
        "source_kind": source_kind,
        # A history-pull day is re-fetched from the Lightspeed report endpoint
        # long after the fact. The endpoint joins to the CURRENT product master,
        # so a SKU renamed in place carries its NEW name on OLD sales (observed:
        # "Bread & Butter Pud" -> "Apple Crumble", "Jala Marg Duo (2) - PartyJar
        # [6 serves]" -> "Jala Marg PartyJar [6 serves]"). Deleted products keep
        # their own names. Product name is the join key to the recipe book, so a
        # deduction on a renamed SKU deducts TODAY's recipe for an OLD sale.
        "name_basis": "current_master" if source_kind == "history_pull" else "as_at_sale",
        "sibling_till_available": sibling_available,
        "ex_gst_basis": ex_basis,
        "truncated": False,
        "row_count": len(entries),
        "totals": {
            "qty": round(sum(e["qty"] for e in entries), 4),
            "rev_inc": total_inc,
            "rev_ex": total_ex,
        },
        # Gross of the EatClub give-away: these are per-product till facts, and
        # the give-away is a day-level settlement adjustment, not a line item.
        "reconciliation": {
            "expected_rev_inc": round(expected_inc, 2),
            "expected_rev_ex": round(expected_ex, 2),
            "gap_inc": gap_inc,
            "gap_ex": gap_ex,
            "basis": "gross of EatClub give-away",
        },
        "reconciled": reconciled,
        "products": entries,
    }

    MIX_DIR.mkdir(parents=True, exist_ok=True)
    out = MIX_DIR / f"{prefix}_{day.isoformat()}.json"
    with out.open("w") as f:
        json.dump(doc, f, indent=2)

    if reconciled:
        print(f"  Product mix: {len(entries)} lines -> {out.name} "
              f"(${total_ex:,.2f} ex, ties to the day)")
    else:
        print(f"  *** PRODUCT MIX DOES NOT TIE: {out.name} sums to ${total_inc:,.2f} inc / "
              f"${total_ex:,.2f} ex, the day says ${expected_inc:,.2f} / ${expected_ex:,.2f} "
              f"(gap ${gap_inc:+,.2f} / ${gap_ex:+,.2f}).")
        print(f"      Written with reconciled=false. DO NOT deduct stock from this day "
              f"until the gap is explained.")
    return out


# --------------------------------------------------------------
# Load Insights CSV
# --------------------------------------------------------------
# ---- MARILYNA'S COMES OFF THE STOW TILL (2026-07-17) ----
#
# Marilyna's has no till. Her export was only ever a FILTER over the Stow POS —
# a saved Lightspeed schedule with a Reporting Group list on it. Sourcing her
# P&L from that filter made her numbers hostage to a setting nobody versions:
#
#   * the filter drops a group  -> Stow strips those rows, her report never gets
#     them, the revenue reaches NO venue. $612.70 on 14 Jul, $375.84 on 11 Jul,
#     $235.71 on 15 Jul, unnoticed for days.
#   * the filter GAINS a group  -> her report bills it and Stow doesn't strip it.
#     Both venues keep it. '$60 BANQUET', $54.55 a time.
#   * the filter CHANGES        -> history splits. Delivery Cocktails were hers
#     until 16 Jul and Stow's after, so one map cannot be right for both eras.
#     That is the ~$43/day on 10-11 Jul, and it is unfixable while her revenue
#     depends on what the filter happened to say that morning.
#
# So: take her rows off the STOW till and classify them like everything else.
# The till is the whole site — nothing can go missing from it, and Stow strips
# exactly what Mari receives, so the two cannot disagree. One map, one rule,
# every day, past and future. Verified against Lightspeed's own reporting groups:
# reproduces their Mari total EXACTLY (0.00) on every day whose export carries a
# tax column — 11, 13, 14, 15, 16, 17 Jul.
#
# Her own export is still pulled — as a CROSS-CHECK, not a source. If it ever
# disagrees with the till, that's the filter drifting and we want to hear about
# it. It just can't move a number any more.
if insights_override is not None:
    insights_file = insights_override if insights_override.exists() else None
    if insights_file is None:
        print(f"  --insights-file {insights_override} does not exist.")
elif venue_key == "marilynas":
    insights_file = resolve(DATA_DIR / f"insights_stow_{target.isoformat()}.csv")
    if insights_file is None:
        print(f"  Mari needs the STOW export (she has no till of her own) — not found.")
else:
    insights_file = resolve(DATA_DIR / f"insights_{prefix}_{target.isoformat()}.csv")

if insights_file is None:
    print(f"Insights CSV not found for {venue_key} {target.isoformat()}")
    print("Will emit alert-only record with 'data_missing' flag")
    lightspeed_data = None
    if mix_only:
        print("  No product mix written — there is no Insights CSV for this day.")
        sys.exit(0)
else:
    # A re-sent report is the one failure that looks like data. Checked before
    # anything is computed, so a duplicated day cannot reach the history file.
    assert_not_a_copy(insights_file, prefix)
    # ...and refuse one whose own rows name a different day (once the
    # Lightspeed schedule includes the date column; silent until then).
    assert_export_is_for(insights_file, target.isoformat())
    # RECONCILE AGAINST THE TILL. The hourly export is produced separately by
    # Lightspeed and names its own date, so agreement between the two is real
    # evidence rather than "a file arrived". Nothing did this before, which is
    # how Stowaway 11 Aug 2026 published $3,807 of a $9,438 day.
    _hourly = DATA_DIR / f"{prefix}_hourly_{target.isoformat()}.csv"
    _ok, _msg = reconcile_against_till(insights_file, _hourly)
    print(f"  till reconciliation: {_msg}")
    if not _ok:
        print(f"::error::{prefix} {target.isoformat()} does NOT reconcile to the "
              f"till — {_msg}. Refusing to write a day the register disagrees with.")
        sys.exit(1)
    all_rows, fieldnames = load_product_rows(insights_file)
    print(f"  Parsed {len(all_rows)} rows; columns: {fieldnames}")

    # Mari is the 'm' slice of the Stow till. Classify against 'stowaway' — the
    # rows ARE Stow-till rows, and classify_product('marilynas') short-circuits
    # to 'f' (her split is Kitchen-only) which would tag every row on the site.
    if venue_key == "marilynas":
        _n = lambda r: (r.get("Product Name") or r.get("Product") or "").strip()
        all_rows = [r for r in all_rows if classify_product(r, _n(r), "stowaway") == 'm']
        print(f"  Marilyna's = {len(all_rows)} 'm' rows off the Stow till "
              f"(${sum(row_rev(r) for r in all_rows):,.2f} inc)")

    # ---- classify every row once ----
    row_depts = [
        classify_product(r, (r.get("Product Name") or r.get("Product") or "").strip(), venue_key)
        for r in all_rows
    ]

    # ---- exclusions: rows that are ANOTHER venue's P&L ----
    #   stowaway: 'm' (Marilynas ride-on) + 'hgf' (HarryGatos food)
    #   harry:    'stf' (Stowaway food)
    if split_venue:
        excl_tags = {"stowaway": {"m", "hgf"}, "harry": {"stf"}}[venue_key]
        rows = [r for r, d in zip(all_rows, row_depts) if d not in excl_tags]
        excluded = len(all_rows) - len(rows)
        if excluded:
            excl_rev = sum(row_rev(r) for r, d in zip(all_rows, row_depts) if d in excl_tags)
            print(f"  Excluded {excluded} cross-venue rows ({sorted(excl_tags)}) from {venue_key} totals (${excl_rev:,.2f} inc)")
        elif venue_key == "stowaway":
            # ---- narrowed-report tripwire (2026-07-16) ----
            # Stow's export is the FULL SITE report on purpose. Marilyna's ('m')
            # and Harry Gatos food ('hgf') ring through the Stow till, and two
            # other venues read those rows OUT of this file:
            #   * Mari  — the coverage guard below compares her report against
            #             the 'm' rows here; no 'm' rows = the guard goes blind
            #             and can never fire again.
            #   * HG    — the reallocation above LIFTS 'hgf' rows out of this
            #             file (~$585/day, ~$213k/yr, concentrated on Mondays:
            #             07-06 $3,233, 07-13 $2,544). Not here = reaches no venue.
            #
            # Stow's own totals never included these rows — they're stripped
            # right here — so a "clean up Stow's report to only Stow RGs" change
            # looks harmless from inside Lightspeed and costs HG six figures a
            # year in silence. Nearly shipped 2026-07-16.
            #
            # Mari rings through the Stow till EVERY trading day (min 2 rows on
            # the quietest Monday in 10 days sampled), so zero cross-venue rows
            # means the report got narrowed, not that nobody ordered pizza.
            print(f"  *** STOW EXPORT LOOKS NARROWED: 0 cross-venue rows in {insights_file.name}.")
            print(f"      This file is meant to be the FULL SITE report — Mari and Harry Gatos")
            print(f"      read their revenue out of it. Stow's own totals are unaffected either")
            print(f"      way, so this will NOT show up as a Stow discrepancy.")
            print(f"      Check the Lightspeed email report filter includes ALL reporting groups.")
    else:
        rows = all_rows

    # ---- cross-venue INBOUND rows (symmetric food reallocation) ----
    # Stow gains HG-file 'stf' rows; HG gains Stow-file 'hgf' rows.
    cross_rows = []
    if split_venue:
        sib_prefix, sib_key, want = (
            ("hg", "harry", "stf") if venue_key == "stowaway" else ("stow", "stowaway", "hgf")
        )
        sib_file = resolve(DATA_DIR / f"insights_{sib_prefix}_{target.isoformat()}.csv")
        if sib_file is not None:
            sib_rows, _ = load_product_rows(sib_file)
            for r in sib_rows:
                d = classify_product(r, (r.get("Product Name") or r.get("Product") or "").strip(), sib_key)
                if d == want:
                    cross_rows.append(r)
            if cross_rows:
                cross_rev = sum(row_rev(r) for r in cross_rows)
                print(f"  Pulled {len(cross_rows)} reallocated rows from {sib_prefix} CSV (${cross_rev:,.2f} inc) -> {venue_key} Kitchen")
        else:
            # ---- sibling-race tripwire (2026-07-17) ----
            # Each venue's pull is fired by Pipedream the moment ITS OWN Insights
            # email lands, so the venues aggregate in arrival order — not in
            # dependency order. This venue's Kitchen revenue is partly rung on the
            # sibling's till and lives in the sibling's CSV. If that CSV has not
            # arrived yet, we silently record a venue that is missing revenue.
            #
            # Observed 2026-07-16: hg-csv-arrived 19:02, stow-csv-arrived 19:30.
            # Harry Gatos aggregated 28 minutes before Stow's CSV existed, so it
            # pulled 0 rows and recorded $802.67 instead of $814.88. Only $12.21
            # that day — but Harry Gatos' food is on the Stow till in VOLUME on
            # Mondays: 07-06 $3,233.59, 07-13 $2,543.91 (mean ~$533/day overall,
            # ~$195k/yr). A Monday race costs three grand and looks like a quiet
            # trading day.
            #
            # The 12:10pm re-aggregation cron re-runs every venue and repairs this
            # incidentally (it exists for Deputy approvals, not for this), so the
            # damage is normally a wrong number between ~6am and midday. This
            # shouts so that a race is visible rather than inferred — and so that
            # a day where the sibling CSV NEVER arrives cannot pass silently.
            print(f"  *** SIBLING CSV MISSING: insights_{sib_prefix}_{target.isoformat()}.csv not found.")
            print(f"      {venue_key} Kitchen revenue rung on the {sib_prefix} till CANNOT be reallocated,")
            print(f"      so this day is UNDERSTATED for {venue_key}. Usually a race — {sib_prefix}'s")
            print(f"      Insights email had not landed when {venue_key}'s pull fired.")
            print(f"      The 12:10pm re-aggregation should repair it; if the CSV never arrives, it won't.")

    rows = rows + cross_rows

    # ---- Mari cross-check (2026-07-17) ----
    # Her revenue no longer comes from her export — it's the 'm' slice of the
    # Stow till (see the file resolution above). So her export can't move a
    # number any more; it's now a witness. If the two disagree, the Lightspeed
    # filter has drifted from MARILYNAS_RGS and somebody should know.
    #
    # This replaces three guards that only existed because her export WAS the
    # source: RECOVERED (filter dropped a group -> revenue reached no venue),
    # DOUBLE COUNTED (filter gained one -> both venues billed it), and DEDUP
    # UNSOUND (name-matching between two sources that no longer both exist).
    # None of those failures are reachable now: the till is the whole site, and
    # Stow strips exactly what Mari receives.
    if venue_key == "marilynas":
        _own = resolve(DATA_DIR / f"insights_mari_{target.isoformat()}.csv",
                       DATA_DIR / f"insights_{target.isoformat()}.csv")
        if _own is not None:
            _orows, _ = load_product_rows(_own)
            _theirs = sum(row_rev(r) for r in _orows)
            _ours = sum(row_rev(r) for r in rows)
            _gap = _theirs - _ours
            if abs(_gap) > MARI_DRIFT_MIN:
                print(f"  *** MARI FILTER DRIFT: her Lightspeed export says ${_theirs:,.2f} inc,")
                print(f"      the Stow till's 'm' rows say ${_ours:,.2f} inc — a ${_gap:+,.2f} gap.")
                print(f"      Her numbers come from the TILL, so this changes nothing — but the")
                print(f"      'Mari Daily Sales Auto' Reporting Group filter no longer matches")
                print(f"      MARILYNAS_RGS. Reconcile the two before they drift further.")
                _mine = {(r.get("Product Name") or r.get("Product") or "").strip() for r in rows}
                _hers = {(r.get("Product Name") or r.get("Product") or "").strip() for r in _orows}
                if _hers - _mine:
                    print(f"        in her export, not 'm' on the till: {sorted(_hers - _mine)[:5]}")
                if _mine - _hers:
                    print(f"        'm' on the till, not in her export: {sorted(_mine - _hers)[:5]}")
            elif abs(_gap) > 0.02:
                # Sub-materiality: an add-on/modifier reports slightly differently
                # between the till and her export (changes no revenue — her total is
                # the till). Not a filter-group change, so NOT flagged as drift.
                print(f"  Mari cross-check gap ${_gap:+,.2f} inc — below the ${MARI_DRIFT_MIN:.0f} "
                      f"materiality floor (modifier-level, not a filter-group change); ignored.")

    revenue_inc = sum(row_rev(r) for r in rows)
    total_tax = sum(parse_num(col(r, "Total Tax", "GST", "Tax")) for r in rows)
    revenue_net_explicit = sum(parse_num(col(r, "Revenue_net", "NetRevenue", "Net Sales")) for r in rows)
    # Which ex-GST source the day settled on. The product mix has to use the
    # SAME one per row or it cannot tie to the day it came from: a file where
    # some rows carry an explicit net column and others fall back to /1.1 sums
    # to a different number than either rule alone.
    if revenue_net_explicit > 0:
        revenue_net = revenue_net_explicit
        ex_basis = "explicit_net_column"
    elif total_tax > 0:
        revenue_net = revenue_inc - total_tax
        ex_basis = "inc_minus_tax"
    else:
        revenue_net = revenue_inc / 1.1
        ex_basis = "inc_div_1.1"

    # ---- EatClub give-away (off-POS discount + commission) ----
    # EatClub tables ring the FULL bill on the POS at full price, so revenue_inc /
    # revenue_net above are OVERSTATED by whatever EatClub kept (the offer discount
    # + its 11% commission) and never settled to us. Subtract the day's give-away
    # HERE, before cogs/gp are formed, so revenue, GP$ and GP% all correct
    # together. COGS is unchanged -- the kitchen cooked the full dish; only the
    # money we actually receive drops. Optional dated fact written by the daily
    # EatClub pull; absent -> no adjustment (graceful). See scripts/eatclub/giveaway.py.
    revenue_inc_gross = revenue_inc
    revenue_ex_gross = revenue_net
    eatclub_giveaway_ex = 0.0
    eatclub_covers = 0
    _ec_file = resolve(DATA_DIR / f"eatclub_{prefix}_{target.isoformat()}.json")
    if _ec_file is not None:
        try:
            with _ec_file.open() as _f:
                _ec = json.load(_f)
            _give_inc = parse_num(_ec.get("giveaway_inc"))
            eatclub_covers = int(_ec.get("covers") or 0)
            if _give_inc:
                eatclub_giveaway_ex = _give_inc / 1.1
                revenue_inc -= _give_inc
                revenue_net -= eatclub_giveaway_ex
                print(f"  EatClub: -${_give_inc:.2f} inc give-away ({eatclub_covers} covers)"
                      f" -> revenue net -${eatclub_giveaway_ex:.2f} ex")
        except (json.JSONDecodeError, AttributeError, ValueError, TypeError) as e:
            print(f"  WARNING: could not parse {_ec_file}: {e}")

    # Lightspeed's own COGS (Produce recipe x Average Cost). Kept as the
    # comparison and per-product fallback; the reported headline is the recipe
    # blend, finalised just before lightspeed_data once products are costed.
    cogs_ls = sum(row_cogs(r) for r in rows)
    cogs = cogs_ls
    gp = revenue_net - cogs

    category_breakdown = {}
    if any((r.get("Category") or "").strip() for r in rows):
        for r in rows:
            cat = (r.get("Category") or "Uncategorised").strip()
            category_breakdown.setdefault(cat, {"rev": 0.0, "cogs": 0.0, "qty": 0.0})
            category_breakdown[cat]["rev"] += row_rev(r)
            category_breakdown[cat]["cogs"] += row_cogs(r)
            category_breakdown[cat]["qty"] += parse_num(col(r, "Qty", "Product Quantity", "Quantity"))

    # ---- Product breakdown, with OUR cost where we have a recipe ----
    #
    # 'cost' has always been read verbatim from the Insights CSV, i.e. whatever
    # Lightspeed computed as (Produce recipe x Average Cost Price). Measured
    # 2026-07-16 on Stowaway: 11 products report $0.00 cost -- $1,530 of
    # $33,460 revenue (4.6%) booked at 100% GP because LS has no recipe. They
    # are all food; the food menu changed supplier and the recipes never
    # followed. Jalapeno Marg reports 96.6% GP.
    #
    # So: use our own cost where we have a recipe, keep LS's where we don't,
    # and ALWAYS emit both so they can be compared rather than trusted.
    # See COGS_ARCHITECTURE.md.
    # The 648-recipe costed book fills the gap the 35 builder recipes never
    # covered (coverage was 0.0% of revenue on every published day). Builder
    # recipes are layered ON TOP because they are properly effective-dated, so a
    # dated source always beats the book's current snapshot.
    our_costs = _load_book_costs(venue_key)
    our_costs.update(_load_our_costs(venue_key, target))

    # The tail of `rows` is the sibling till's reallocated food (see the
    # cross-venue block above). Those lines were rung on the OTHER venue's till
    # but are this venue's Kitchen, exactly as dept_sums treats them.
    _cross_start = len(rows) - len(cross_rows)

    product_breakdown = []
    product_mix = []
    for _i, r in enumerate(rows):
        name = (r.get("Product Name") or r.get("Product") or "").strip()
        if not name:
            continue
        qty = parse_num(col(r, "Product Quantity", "Qty", "Quantity"))
        ls_cost = row_cogs(r)
        entry = {
            "name": name,
            "qty": qty,
            "rev": row_rev(r),
            "cost": ls_cost,
            "cost_source": "lightspeed",
        }
        ours = book_cost(our_costs, name)
        if ours is not None:
            entry["cost"] = round(ours * qty, 4)      # per-serve x units sold
            entry["cost_source"] = "recipe"
            entry["cost_lightspeed"] = ls_cost        # keep LS as a second opinion
        product_breakdown.append(entry)

        # Same row, the shape the stock ledger needs: units, both GST bases,
        # and where it was rung. `rev` above is inc-GST by history; the mix
        # names its bases so nothing downstream has to guess.
        _cross = _i >= _cross_start
        mix_entry = dict(entry)
        mix_entry.pop("rev", None)
        # The ledger joins a till line to its recipe BY NAME, so the mix gets
        # the canonical name: the emailed export's mangled non-ASCII repaired,
        # and a reused SKU's old sales put back under the dish that was
        # actually served. data/product_renames.yaml is the adjudicated
        # register; canonical_name leaves anything it does not recognise alone.
        # Only the mix is canonicalised — the P&L path keeps the raw name, so
        # no published number moves.
        _canon = canonical_name(
            name, target,
            source_kind="history_pull" if insights_override is not None else "committed_export")
        if _canon != name:
            mix_entry["name"] = _canon
            mix_entry["name_as_reported"] = name
        mix_entry["rev_inc"] = round(row_rev(r), 4)
        mix_entry["rev_ex"] = round(row_rev_ex(r, ex_basis), 4)
        mix_entry["dept"] = "f" if _cross else classify_product(r, name, venue_key)
        mix_entry["till"] = (sib_prefix if _cross else prefix)
        product_mix.append(mix_entry)

    product_breakdown.sort(key=lambda p: p["rev"], reverse=True)
    product_mix.sort(key=lambda p: p["rev_inc"], reverse=True)

    # ---- is this actually a PRODUCT export? (2026-08-15) ----
    # Harry Gatos also emails a REPORTING-GROUP level report under the same
    # insights_<prefix>_<date>.csv filename — same '$ Sales' column, no
    # 'Product Name'. Revenue still sums correctly off it, so the day's P&L
    # looks fine and nothing complains; there is simply no product detail in
    # the file. Found on insights_hg_2026-07-12.csv, which has been sitting in
    # data/ since July. build_products_weekly.py already refuses these by
    # header (RG_LEVEL_KEY) for the same reason.
    #
    # Write NOTHING rather than an empty mix: a zero-line mix is
    # indistinguishable from "nothing sold that day", and a stock ledger
    # reading it would deduct nothing and call the variance clean.
    if not any(c in fieldnames for c in ("Product Name", "Product")):
        print(f"  *** NO PRODUCT MIX for {prefix} {target.isoformat()}: "
              f"{insights_file.name} is not a product export.")
        print(f"      Columns: {fieldnames}")
        print(f"      This is the reporting-group report saved under the product "
              f"report's filename. Revenue (${revenue_inc_gross:,.2f} inc) is still")
        print(f"      right — there is just no product detail, so this day cannot "
              f"feed a stock deduction. Re-export the Sales-by-Product report.")
    else:
        write_product_mix(product_mix, venue=venue_key, prefix=prefix, day=target,
                          source_file=insights_file.name, ex_basis=ex_basis,
                          source_kind=("history_pull" if insights_override is not None
                                       else "committed_export"),
                          sibling_available=(not split_venue) or bool(cross_rows),
                          expected_inc=revenue_inc_gross,
                          expected_ex=revenue_ex_gross)

    if mix_only:
        sys.exit(0)

    # ---- Kitchen / FOH split (Stow + HG only) ----
    # 'f' + inbound cross rows = Kitchen slice; 'b' = FOH slice (catch-all).
    # 'm'/'hgf'/'stf' outbound rows are tracked for reconciliation only.
    dept_sums = {k: {"rev": 0.0, "cogs": 0.0} for k in ("f", "b", "m", "hgf", "stf")}
    if split_venue:
        for r, d in zip(all_rows, row_depts):
            dept_sums[d]["rev"] += row_rev(r)
            dept_sums[d]["cogs"] += row_cogs(r)
        for r in cross_rows:      # inbound reallocated rows are Kitchen/food
            dept_sums["f"]["rev"] += row_rev(r)
            dept_sums["f"]["cogs"] += row_cogs(r)
        # outbound tags don't belong in this venue's slices
        excl_tags = {"stowaway": {"m", "hgf"}, "harry": {"stf"}}[venue_key]
        for t in excl_tags:
            pass   # kept in dept_sums[t] for the record; not in f/b

    uber_eats_rev = 0
    for r in rows:
        pay_type = (r.get("PaymentType") or r.get("Payment Type") or "").lower()
        if "uber" in pay_type:
            uber_eats_rev += row_rev(r)

    # Reported "estimated" COGS = our recipe cost where we have a recipe, LS
    # elsewhere (COGS_ARCHITECTURE.md). Xero purchases stay the separate ACTUAL
    # COGS feed on the dashboard. recipe_coverage_pct = share of revenue on a
    # real recipe, so a low-coverage estimate is not read as precise.
    cogs, cogs_source, recipe_coverage_pct = blend_reported_cogs(
        product_breakdown, cogs_ls, revenue_net)
    gp = revenue_net - cogs

    lightspeed_data = {
        "revenue_inc": revenue_inc,
        "revenue_ex": revenue_net,
        "cogs": cogs,
        "gp": gp,
        "gp_pct": gp / revenue_net * 100 if revenue_net else 0,
        "cogs_pct": cogs / revenue_net * 100 if revenue_net else 0,
        "cogs_lightspeed": cogs_ls,
        "cogs_source": cogs_source,
        "recipe_coverage_pct": recipe_coverage_pct,
        "uber_eats_rev": uber_eats_rev,
        "eatclub_giveaway_ex": eatclub_giveaway_ex,
        "eatclub_covers": eatclub_covers,
        "revenue_inc_gross": revenue_inc_gross,
        "revenue_ex_gross": revenue_ex_gross,
        "category_breakdown": category_breakdown,
        # Top 20 by revenue, ON PURPOSE — this is the dashboard's panel and it
        # ships to every browser that opens a daily record. The UNTRUNCATED mix
        # (all ~300 lines) is a separate fact file, data/product_mix/, written
        # above; the stock ledger reads that one. Never widen this to feed a
        # deduction — see write_product_mix().
        "product_breakdown": product_breakdown[:20],
        "dept_sums": dept_sums if split_venue else None,
    }

# --------------------------------------------------------------
# Load payments CSV (Insights "Sales by Payment Type") — optional.
# --------------------------------------------------------------
payments_file = resolve(DATA_DIR / f"payments_{prefix}_{target.isoformat()}.csv")
payments_breakdown = None
if payments_file is not None:
    pay_text = read_insights_csv_text(payments_file)
    pay_reader = csv.DictReader(io.StringIO(pay_text))
    pay_rows = list(pay_reader)
    type_col = None
    for c in (pay_reader.fieldnames or []):
        if "payment" in c.lower() or "tender" in c.lower():
            type_col = c
            break
    payments_breakdown = {}
    uber_from_payments = 0.0
    for r in pay_rows:
        ptype = (r.get(type_col) or "Unknown").strip() if type_col else "Unknown"
        amt = parse_num(col(r, "Revenue_inc_gst", "$ Sales", "Sales", "Sale Amount", "Total Sales", "Amount", "Total"))
        payments_breakdown[ptype] = payments_breakdown.get(ptype, 0.0) + amt
        if "uber" in ptype.lower():
            uber_from_payments += amt
    if lightspeed_data is not None:
        lightspeed_data["uber_eats_rev"] = uber_from_payments
        print(f"  Payments CSV: Uber Eats ${uber_from_payments:.2f} across {len(pay_rows)} rows")

# --------------------------------------------------------------
# Load manual Uber Direct weekly entry (Mari only) — optional.
# --------------------------------------------------------------
uber_direct_dollars = 0.0
uber_direct_file = DATA_DIR / "manual" / "uber_direct.json"
if "delivery" in lanes and uber_direct_file.exists():
    try:
        with uber_direct_file.open() as f:
            ud = json.load(f)
        week_ending = target + timedelta(days=(6 - target.weekday()))  # Sunday of target's week
        weekly_total = parse_num(ud.get("weeks", {}).get(week_ending.isoformat()))
        if weekly_total:
            uber_direct_dollars = weekly_total / 7.0
            print(f"  Uber Direct: ${weekly_total:.2f} for week ending {week_ending} -> ${uber_direct_dollars:.2f}/day")
    except (json.JSONDecodeError, AttributeError) as e:
        print(f"  WARNING: could not parse {uber_direct_file}: {e}")

# --------------------------------------------------------------
# Load Deputy JSON. Costs are ex-super — gross up by SUPER_MULT so every
# reported figure is inc-super (weekly canon: TotalWagesIncSuper).
# --------------------------------------------------------------
deputy_file = resolve(
    DATA_DIR / f"deputy_{prefix}_{target.isoformat()}.json",
    DATA_DIR / f"deputy_{target.isoformat()}.json" if venue_key == "marilynas" else Path("/nonexistent"),
)

if deputy_file is None:
    deputy_data = None
else:
    with deputy_file.open() as f:
        d = json.load(f)

    # Super, PER PERSON — not a flat 12% (2026-07-18).
    #
    # This runs before Xero has posted the week, so it can't use actuals. But it
    # can use each person's OWN trailing rate, and that matters: Mari's drivers
    # are under 18 and legally get NO super, so 12% invented cost on exactly the
    # venue least able to carry it. Grossing flat here also meant Zak's 9am
    # number and the same week after the 6:30am rebuild disagreed by ~0.8% for
    # no reason he could see.
    #
    # Rules live in wage_model.super_lookup — shared with rebuild_wages and
    # roster_pull. Three copies of the gross-up is how it drifted to flat.
    _xp = DATA_DIR / "xero_pay_weekly.json"
    _xs = DATA_DIR / "xero_super_weekly.json"
    _em = DATA_DIR / "employee_map.json"
    if _xp.exists() and _xs.exists() and _em.exists():
        _super_for = super_lookup(json.loads(_xp.read_text()), json.loads(_xs.read_text()),
                                  json.loads(_em.read_text()), V.SUPER_RATE)
    else:
        print(f"  super: no Xero data — flat {V.SUPER_RATE * 100:.0f}% "
              f"(overstates; rebuild_wages corrects it at 6:30am)")
        _super_for = lambda _e, _w: SUPER_MULT
    _wk = (target - timedelta(days=target.weekday()) + timedelta(days=6)).isoformat()

    # Per-person calibration, learned by rebuild_wages from CLOSED weeks.
    #
    # Backtested 2026-07-18 over 13 weeks: the uncalibrated estimate was UNDER
    # what payroll actually paid in 357 of 398 employee-weeks — -4% overall,
    # -7.6% on hourly staff, only 2 weeks in 13 within +/-2%. Deputy's rates are
    # stale in a different way for each person (award rises, loading, penalties,
    # overtime, allowances), and modelling each cause is a losing game.
    #
    # So this figure carries each person's own measured error forward. It is a
    # correction learned from payslips, not a fudge factor.
    _cal_f = DATA_DIR / "wage_calibration.json"
    _cal = json.loads(_cal_f.read_text()) if _cal_f.exists() else {}
    if _cal:
        print(f"  wages: calibrated from {len(_cal)} people's closed weeks")
    else:
        print("  wages: NO calibration file — this number runs ~4% light. "
              "Run the full rebuild_wages --write to publish one.")

    def _rate(t):
        c = _cal.get(str(t.get("employee_id")))
        return _super_for(t.get("employee_id"), _wk) * (c["factor"] if c else 1.0)

    # UNAPPROVED TIMESHEETS — the single biggest error in this number.
    #
    # Deputy costs a shift when it is APPROVED. Yesterday's shifts usually
    # aren't. Measured 2026-07-18: 17 Jul had 13.50h of 93h (14.5%) at Cost = 0
    # — real hours, worked, that will absolutely be paid, booked at nothing.
    # The calibration factor cannot touch this: 0 x 1.05 is still 0.
    #
    # So cost them at the person's own learned $/h (published by rebuild_wages,
    # which sees enough Deputy history to know it; this script sees one day).
    # Marked _imputed so it's visible rather than silently blended in.
    _imp_n = _imp_h = 0

    def _cost_of(t):
        global _imp_n, _imp_h
        c = t.get("cost") or 0
        h = t.get("hours") or 0
        if c == 0 and h > 0 and not t.get("salaried_synth"):
            r = (_cal.get(str(t.get("employee_id"))) or {}).get("rate_per_hour")
            if r:
                _imp_n += 1
                _imp_h += h
                return h * r
        return c

    def dept_cost(name):
        return sum(_cost_of(t) * _rate(t) for t in d if t.get("dept") == name)
    kitchen_cost = dept_cost("Kitchen")
    foh_cost = dept_cost("FOH")
    driver_cost = dept_cost("Driver")
    admin_cost = dept_cost("Admin")
    # Leave is group overhead — NOT in the venue total (weekly canon); the
    # dashboard adds it to the synthesized Group wage figure.
    leave_cost = dept_cost("Leave")
    if _imp_n:
        print(f"  wages: {_imp_n} unapproved shift(s) ({_imp_h:.2f}h) costed at "
              f"the person's own rate — Deputy has no cost until approval")
    _unratable = [t for t in d if (t.get("cost") or 0) == 0 and (t.get("hours") or 0) > 0
                  and not t.get("salaried_synth")
                  and not (_cal.get(str(t.get("employee_id"))) or {}).get("rate_per_hour")]
    if _unratable:
        # No rate, no imputation. These hours book $0 and the number is light by
        # however much they're worth. Say so — a silent $0 is how a wage line
        # goes quietly wrong.
        print(f"  !! wages: {len(_unratable)} shift(s), "
              f"{sum(t.get('hours') or 0 for t in _unratable):.2f}h — real hours, no cost, "
              f"and no known rate to impute from. BOOKED AT $0:")
        for t in _unratable[:5]:
            print(f"       employee {t.get('employee_id')} ({t.get('employee_name')}) "
                  f"{t.get('hours')}h {t.get('dept')}")
    # Total = Kitchen + FOH + Admin + Driver. Driver stays inside the venue
    # total (Mari Venue Total = Kitchen + Driver in the weekly canon) AND
    # also surfaces in the delivery lane.
    total_wages = kitchen_cost + foh_cost + driver_cost + admin_cost
    deputy_data = {
        "kitchen_wages": kitchen_cost,
        "foh_wages": foh_cost,
        "driver_wages": driver_cost,
        "admin_wages": admin_cost,
        "leave_wages": leave_cost,
        "total_wages": total_wages,
        "kitchen_hours": sum(t.get("hours", 0) for t in d if t.get("dept") == "Kitchen"),
        "foh_hours":     sum(t.get("hours", 0) for t in d if t.get("dept") == "FOH"),
        "driver_hours":  sum(t.get("hours", 0) for t in d if t.get("dept") == "Driver"),
        "admin_hours":   sum(t.get("hours", 0) for t in d if t.get("dept") == "Admin"),
    }

# --------------------------------------------------------------
# Compute lanes
# --------------------------------------------------------------
if lightspeed_data and lightspeed_data.get("uber_eats_rev"):
    uber_commission = lightspeed_data["uber_eats_rev"] / 1.1 * UBER_COMMISSION_RATE
else:
    uber_commission = 0

driver_dollars = deputy_data["driver_wages"] if deputy_data else 0

if lightspeed_data:
    rev_ex = lightspeed_data["revenue_ex"]
    cogs_dollars = lightspeed_data["cogs"]
    cogs_pct = cogs_dollars / rev_ex * 100 if rev_ex else 0
    if deputy_data:
        wages_dollars = deputy_data["total_wages"]
        wages_pct = wages_dollars / rev_ex * 100 if rev_ex else 0
    else:
        wages_dollars = wages_pct = None
    delivery_dollars = driver_dollars + uber_commission + uber_direct_dollars
    delivery_pct = delivery_dollars / rev_ex * 100 if rev_ex else 0
else:
    rev_ex = cogs_dollars = cogs_pct = None
    wages_dollars = wages_pct = None
    delivery_dollars = delivery_pct = None

# ---- Marilynas Net Wage % (weekly canon: net of Uber fees) ----
# Real fee data (service + marketing + amendments) comes from the Uber
# merchant portal weekly; daily we only have the 30% commission estimate +
# amortized Uber Direct. Flagged as estimate in the record.
net_takings_ex = net_wage_pct = None
if venue_key == "marilynas" and rev_ex:
    uber_fees_est = uber_commission + uber_direct_dollars
    if uber_fees_est:
        net_takings_ex = rev_ex - uber_fees_est
        if wages_dollars is not None and net_takings_ex:
            net_wage_pct = wages_dollars / net_takings_ex * 100

# ---- Split lane figures ----
split = None
if lightspeed_data and split_venue and lightspeed_data.get("dept_sums"):
    ds = lightspeed_data["dept_sums"]
    food_ex = ds["f"]["rev"] / 1.1
    bev_ex = ds["b"]["rev"] / 1.1
    food_cogs = ds["f"]["cogs"]
    bev_cogs = ds["b"]["cogs"]
    split = {
        "food_ex_gst": round(food_ex, 2),
        "bev_ex_gst": round(bev_ex, 2),
        "food_cogs": round(food_cogs, 2),
        "bev_cogs": round(bev_cogs, 2),
        "food_cogs_pct": round(food_cogs / food_ex * 100, 1) if food_ex else None,
        "bev_cogs_pct": round(bev_cogs / bev_ex * 100, 1) if bev_ex else None,
        "food_gp_pct": round((food_ex - food_cogs) / food_ex * 100, 1) if food_ex else None,
        "bev_gp_pct": round((bev_ex - bev_cogs) / bev_ex * 100, 1) if bev_ex else None,
        "mari_rideon_ex_gst": round(ds["m"]["rev"] / 1.1, 2),
        "hg_food_out_ex_gst": round(ds["hgf"]["rev"] / 1.1, 2),
        "stow_food_out_ex_gst": round(ds["stf"]["rev"] / 1.1, 2),
    }
# Marilyna's is a pizza shop: 100% food, and the only non-kitchen labour is the
# Driver OU (which Deputy tags separately, so it never lands in kitchen_wages).
# There's no split to CLASSIFY here — but the columns aren't unknowable, they're
# trivially true: revenue IS food revenue, COGS IS food COGS, bev is zero.
# Leaving them blank made Mari's own venue tab say "awaiting split data" for
# numbers we've had since Oct 2024, while the Big Chef group view derived the
# same thing itself. Emit them properly instead (Zak, 2026-07-15).
elif venue_key == "marilynas" and lightspeed_data:
    food_ex = lightspeed_data["revenue_ex"]
    food_cogs_m = lightspeed_data["cogs"]
    split = {
        "food_ex_gst": round(food_ex, 2),
        "bev_ex_gst": 0.0,
        "food_cogs": round(food_cogs_m, 2),
        "bev_cogs": 0.0,
        "food_cogs_pct": round(food_cogs_m / food_ex * 100, 1) if food_ex else None,
        "bev_cogs_pct": None,
        "food_gp_pct": round((food_ex - food_cogs_m) / food_ex * 100, 1) if food_ex else None,
        "bev_gp_pct": None,
    }

wages_kitchen_pct = wages_foh_pct = None
if deputy_data and split:
    if split["food_ex_gst"]:
        wages_kitchen_pct = round(deputy_data["kitchen_wages"] / split["food_ex_gst"] * 100, 1)
    if split["bev_ex_gst"]:
        wages_foh_pct = round(deputy_data["foh_wages"] / split["bev_ex_gst"] * 100, 1)

# Baseline / targets
baseline_path = BASELINES_DIR / cfg["baseline_file"]
if baseline_path.exists():
    with baseline_path.open() as f:
        baseline = json.load(f)
    targets = baseline["targets_and_alerts"]
else:
    print(f"WARNING: baseline {baseline_path} missing — using empty targets")
    targets = {}


def status(v, c):
    if v is None or c is None: return "unknown"
    if v >= c.get("red",   float("inf")): return "red"
    if v >= c.get("amber", float("inf")): return "amber"
    if v <= c.get("target", float("inf")): return "green"
    return "yellow"


cogs_status = status(cogs_pct, targets.get("cogs"))
wages_status = status(wages_pct, targets.get("wages"))
delivery_status = status(delivery_pct, targets.get("delivery")) if "delivery" in lanes else "n/a"
gp_status = status(lightspeed_data["gp_pct"] if lightspeed_data else None, targets.get("gp"))
cogs_food_status = status(split["food_cogs_pct"] if split else None, targets.get("cogs_food"))
cogs_bev_status = status(split["bev_cogs_pct"] if split else None, targets.get("cogs_bev"))
wages_kitchen_status = status(wages_kitchen_pct, targets.get("wages_kitchen"))
wages_foh_status = status(wages_foh_pct, targets.get("wages_foh"))

# --------------------------------------------------------------
# Record
# --------------------------------------------------------------
record = {
    "date": target.isoformat(),
    "generated_at": datetime.utcnow().isoformat() + "Z",
    "venue": venue_key,
    "venue_display": cfg["display_name"],
    "lane_config": cfg["lane_config"],
    "data_status": {
        "lightspeed": "ok" if lightspeed_data else "missing",
        "deputy":     "ok" if deputy_data else "missing",
        "payments":   "ok" if payments_breakdown is not None else "missing",
    },
    "sales": {
        "revenue_inc_gst": round(lightspeed_data["revenue_inc"], 2) if lightspeed_data else None,
        "revenue_ex_gst":  round(rev_ex, 2) if rev_ex else None,
        "cogs_dollars":    round(cogs_dollars, 2) if cogs_dollars is not None else None,
        "cogs_pct":        round(cogs_pct, 1) if cogs_pct is not None else None,
        "gp_dollars":      round(lightspeed_data["gp"], 2) if lightspeed_data else None,
        "gp_pct":          round(lightspeed_data["gp_pct"], 1) if lightspeed_data else None,
        "cogs_source":             lightspeed_data.get("cogs_source") if lightspeed_data else None,
        "cogs_lightspeed_dollars": round(lightspeed_data["cogs_lightspeed"], 2) if lightspeed_data else None,
        "recipe_coverage_pct":     round(lightspeed_data["recipe_coverage_pct"], 1) if lightspeed_data else None,
        "uber_eats_revenue": round(lightspeed_data.get("uber_eats_rev", 0), 2) if lightspeed_data else 0,
        "net_takings_ex_gst": round(net_takings_ex, 2) if net_takings_ex is not None else None,
        "eatclub_giveaway_ex_gst": round(lightspeed_data.get("eatclub_giveaway_ex", 0), 2) if lightspeed_data else 0,
        "eatclub_covers": lightspeed_data.get("eatclub_covers", 0) if lightspeed_data else 0,
        "revenue_ex_gst_before_eatclub": (round(lightspeed_data["revenue_ex_gross"], 2)
                                          if lightspeed_data and lightspeed_data.get("eatclub_giveaway_ex") else None),
        **(split or {}),
    },
    "wages": {
        "kitchen_dollars": round(deputy_data["kitchen_wages"], 2) if deputy_data else None,
        "foh_dollars":     round(deputy_data.get("foh_wages", 0), 2) if deputy_data else None,
        "driver_dollars":  round(deputy_data["driver_wages"], 2) if deputy_data else None,
        "admin_dollars":   round(deputy_data.get("admin_wages", 0), 2) if deputy_data else None,
        "leave_dollars":   round(deputy_data.get("leave_wages", 0), 2) if deputy_data else None,
        "total_dollars":   round(wages_dollars, 2) if wages_dollars is not None else None,
        "wages_pct":       round(wages_pct, 1) if wages_pct is not None else None,
        "net_wage_pct":    round(net_wage_pct, 1) if net_wage_pct is not None else None,
        "kitchen_hours":   round(deputy_data.get("kitchen_hours", 0), 1) if deputy_data else None,
        "foh_hours":       round(deputy_data.get("foh_hours", 0), 1) if deputy_data else None,
        "wages_kitchen_pct": wages_kitchen_pct,
        "wages_foh_pct":     wages_foh_pct,
        "includes_super": True,
        "salaried_synthesized": True,
    },
    "delivery": {
        "uber_eats_commission_dollars": round(uber_commission, 2),
        "own_driver_dollars":           round(driver_dollars, 2),
        "uber_direct_dollars":          round(uber_direct_dollars, 2),
        "total_dollars":                round(delivery_dollars, 2) if delivery_dollars is not None else None,
        "delivery_pct":                 round(delivery_pct, 1) if delivery_pct is not None else None,
        "fees_are_estimate":            True,
    } if "delivery" in lanes else None,
    "payments_breakdown": {k: round(v, 2) for k, v in payments_breakdown.items()} if payments_breakdown else None,
    "alerts": {
        "cogs":     cogs_status,
        "wages":    wages_status,
        "delivery": delivery_status,
        "gp":       gp_status,
        "cogs_food":     cogs_food_status,
        "cogs_bev":      cogs_bev_status,
        "wages_kitchen": wages_kitchen_status,
        "wages_foh":     wages_foh_status,
    },
    "targets": targets,
    "top_products": lightspeed_data.get("product_breakdown", []) if lightspeed_data else [],
}

# Write venue-prefixed record
out_file = DATA_DIR / f"{prefix}_daily_{target.isoformat()}.json"
with out_file.open("w") as f:
    json.dump(record, f, indent=2)
print(f"Saved {out_file}")

# --------------------------------------------------------------
# Append to history CSV (FULL history — no trailing-window trim; the
# backfill from the product masters lives in these files)
# --------------------------------------------------------------
history_file = DATA_DIR / f"{prefix}_daily_history.csv"
history_rows = []
if history_file.exists():
    with history_file.open() as f:
        history_rows = list(csv.DictReader(f))
# Keep the row we're about to replace: it carries fields this script doesn't own
# (rebuild_wages writes the assumed pass from the roster, which we never fetch)
# and history is rewritten from nr.keys(), so anything not carried is deleted.
prev_row = next((r for r in history_rows if r["date"] == target.isoformat()), None)
history_rows = [r for r in history_rows if r["date"] != target.isoformat()]

nr = {
    "date": target.isoformat(),
    "revenue_ex_gst":   record["sales"]["revenue_ex_gst"],
    "cogs_dollars":     record["sales"]["cogs_dollars"],
    "cogs_pct":         record["sales"]["cogs_pct"],
    "recipe_coverage_pct":     record["sales"]["recipe_coverage_pct"],
    "cogs_lightspeed_dollars": record["sales"]["cogs_lightspeed_dollars"],
    "wages_dollars":    record["wages"]["total_dollars"],
    "wages_pct":        record["wages"]["wages_pct"],
    "delivery_dollars": record["delivery"]["total_dollars"] if record["delivery"] else "",
    "delivery_pct":     record["delivery"]["delivery_pct"] if record["delivery"] else "",
    "gp_dollars":       record["sales"]["gp_dollars"],
    "gp_pct":           record["sales"]["gp_pct"],
    "eatclub_giveaway_ex_gst": record["sales"]["eatclub_giveaway_ex_gst"],
    "eatclub_covers":          record["sales"]["eatclub_covers"],
    "cogs_alert":       cogs_status,
    "wages_alert":      wages_status,
    "delivery_alert":   delivery_status,
    "gp_alert":         gp_status,
    "food_ex_gst":            split["food_ex_gst"] if split else "",
    "bev_ex_gst":             split["bev_ex_gst"] if split else "",
    "food_cogs":              split["food_cogs"] if split else "",
    "bev_cogs":               split["bev_cogs"] if split else "",
    "food_cogs_pct":          (split["food_cogs_pct"] if split and split["food_cogs_pct"] is not None else ""),
    "bev_cogs_pct":           (split["bev_cogs_pct"] if split and split["bev_cogs_pct"] is not None else ""),
    "food_gp_pct":            (split["food_gp_pct"] if split and split["food_gp_pct"] is not None else ""),
    "bev_gp_pct":             (split["bev_gp_pct"] if split and split["bev_gp_pct"] is not None else ""),
    # Emitted for EVERY venue, not just the split ones. These are computed from
    # Deputy's own OU tagging for all three venues — the old `split_venue` gate
    # threw Mari's away at write time even though the pull had already worked it
    # out. Driver gets its own column: it's real labour, it is NOT kitchen, and
    # it must not be inferred from delivery_dollars (which also carries Uber
    # commission and Uber Direct fees).
    "wages_kitchen_dollars":  record["wages"]["kitchen_dollars"] if deputy_data else "",
    "wages_foh_dollars":      record["wages"]["foh_dollars"] if deputy_data else "",
    "wages_driver_dollars":   record["wages"]["driver_dollars"] if deputy_data else "",
    # Admin is inside wages_dollars but is NOT cost the venue can roster against,
    # so venue views strip it (2026-07-17). It has to be written here as well as
    # in rebuild_wages: the aggregator rewrites these rows every morning, and a
    # column it doesn't emit gets blanked — the venue split would silently fall
    # back to total-minus-parts and quietly re-absorb admin.
    "wages_admin_dollars":    record["wages"]["admin_dollars"] if deputy_data else "",
    # The assumed first pass belongs to rebuild_wages — it needs the ROSTER, which
    # this script never fetches. But history is rewritten here every morning from
    # nr.keys(), so a column this dict doesn't name is DELETED from the CSV. Carry
    # the existing value through untouched: rebuild_wages runs after the pull
    # (7:15am, and again at 12:10pm) and refills it. Without these two lines the
    # 6am pull silently drops the column and the card falls back to the raw,
    # half-clocked number with nothing saying so — the exact 14.7% problem the
    # assumed pass exists to solve.
    "wages_assumed_dollars":  (prev_row or {}).get("wages_assumed_dollars", ""),
    "wages_assumed_shifts":   (prev_row or {}).get("wages_assumed_shifts", ""),
    "wages_kitchen_pct":      wages_kitchen_pct if wages_kitchen_pct is not None else "",
    "wages_foh_pct":          wages_foh_pct if wages_foh_pct is not None else "",
    "cogs_food_alert":        cogs_food_status,
    "cogs_bev_alert":         cogs_bev_status,
    "wages_kitchen_alert":    wages_kitchen_status,
    "wages_foh_alert":        wages_foh_status,
    "uber_eats_revenue":      record["sales"]["uber_eats_revenue"],
    "uber_direct_dollars":    round(uber_direct_dollars, 2) if "delivery" in lanes else "",
    "leave_dollars":          (record["wages"]["leave_dollars"] if deputy_data and venue_key == "stowaway" else ""),
}
history_rows.append(nr)
history_rows.sort(key=lambda r: r["date"])
fieldnames = list(nr.keys())
with history_file.open("w", newline="") as f:
    if history_rows:
        # lineterminator="\n": csv defaults to CRLF, and the committed histories
        # are LF. Without this a re-run rewrites all 609 rows as line-ending
        # churn, burying the one row that actually changed.
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        for r in history_rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
print(f"History: {len(history_rows)} rows -> {history_file}")
