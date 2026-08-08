#!/usr/bin/env python3
"""Build data/products_weekly.csv — per-product weekly ex-GST sales with the
reporting-group NAME, for the Menu Trends "Products" view.

Source: the committed Sales-by-Product exports (data/insights_<prefix>_<date>.csv).
Those are the full-site till dumps, so Stow's file also carries Marilyna's items
('m') and Harry Gatos food ('hgf'), and HG's file carries Stow food ('stf'). We
reattribute each product to its real venue with scripts/product_dept_map.json (the
exact same dept codes the daily aggregator uses), skip the Mari file entirely
(Mari's revenue IS the 'm' slice of the Stow till — reading both would double it),
and join the human reporting-group name from the Lightspeed product exports in
data/bo_exports/. Weeks end Sunday, matching data/rg_weekly.csv.

Output columns: week_ending,venue,reporting_group,product_name,sales_ex_gst,qty
Run: python3 scripts/build_products_weekly.py   (idempotent; rebuilds the whole file)
"""
import csv, glob, json, os, re, sys
from collections import defaultdict
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DEPT_MAP_FILE = os.path.join(ROOT, "scripts", "product_dept_map.json")
PRODUCT_OVERRIDES = {"$60 BANQUET": "m"}          # mirrors daily_aggregator
VKEY = {"stow": "stow", "hg": "hg"}               # dept-map sub-keys
# dept code -> the venue that revenue belongs to. f/b stay on the till's own venue.
DEPT_VENUE = {"m": "mari", "hgf": "hg", "stf": "stow"}
# friendly fallback group when a product carries no reporting group in Lightspeed.
UNMAPPED = {"f": "Kitchen (no reporting group)", "b": "Bar / FOH (no reporting group)",
            "m": "Marilyna's", "hgf": "Harry Gatos Food", "stf": "Stowaway Food"}


def parse_num(x):
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


# Serving-size variants of the SAME drink split it across rows. Collapse the
# trailing " - <size>" so beer pints+schooners merge, and wine regular/large/
# bottle/glass merge. Whitelisted tokens only — never strips flavours ("- Passion-
# fruit"), delivery zones ("- Freshwater / Queenscliff"), or deal names.
_SIZE_SUFFIXES = {"pint", "schooner", "regular", "large", "bottle",
                  "glass", "large glass", "regular glass"}

def normalize_product(name):
    base, sep, suf = (name or "").rpartition(" - ")
    if sep:
        s = re.sub(r"\s*\[[^\]]*\]\s*$", "", suf).strip().lower()   # drop a trailing [HG] etc.
        if s in _SIZE_SUFFIXES:
            return base.strip()
    return name


def week_ending(d):                                # Sunday of d's Mon-Sun week
    return d + timedelta(days=(6 - d.weekday()))


# THE EXPORT HAS CHANGED SHAPE ONCE AND WILL AGAIN.
#
# This used to read `row.get("Product Name") or row.get("ProductName")` and
# `row.get("$ Sales") or row.get("Sales")`. Lightspeed renamed those columns on
# 2026-07-13. Every row of an OLDER file therefore returned name="" and was
# skipped by the `if not name: continue` guard meant for footer rows — so 11
# files went in and nothing came out, with no error and no log line. Week ending
# 2026-07-12 published $9,183 for Stowaway against $42,006 in the daily history.
# $54,236 ex-GST in total was missing from the Products view.
#
# A per-key `or` chain cannot tell "this column is absent because the schema is
# older" from "this row is a footer". So name the schemas, match on the HEADER,
# and refuse anything that matches none — an unreadable export must be a loud
# failure, never a quiet empty week.
PRODUCT_SCHEMAS = (
    {"label": "2026-07 onwards", "name": "Product Name", "qty": "Product Quantity",
     "inc": "$ Sales", "tax": "Total Tax", "cost": "Cost"},
    {"label": "pre 2026-07-13", "name": "Product", "qty": "Quantity",
     "inc": "Sale Amount", "tax": None, "cost": "Cost"},
)
# HG also emails a REPORTING-GROUP level export under the same filename pattern.
# It is a different report, not an older product one: folding it in would invent
# products named after groups and double-count HG revenue. Recognise and skip.
RG_LEVEL_KEY = "Reporting Group Name"


def product_schema(header, path):
    """-> a PRODUCT_SCHEMAS entry, or None for the reporting-group export.

    Raises on anything else rather than returning a shape the caller will
    silently read nothing out of.
    """
    cols = {(c or "").strip() for c in header}
    if RG_LEVEL_KEY in cols:
        return None
    for s in PRODUCT_SCHEMAS:
        if s["name"] in cols and s["inc"] in cols:
            return s
    raise SystemExit(
        f"\nUNRECOGNISED INSIGHTS EXPORT SCHEMA\n  file: {path}\n"
        f"  header: {', '.join(header)}\n\n"
        f"  Known product schemas:\n"
        + "".join(f"    - {s['label']}: needs {s['name']!r} + {s['inc']!r}\n"
                  for s in PRODUCT_SCHEMAS)
        + f"    - reporting-group export: has {RG_LEVEL_KEY!r} (skipped by design)\n\n"
        "  Lightspeed has renamed these columns before. Add the new shape to\n"
        "  PRODUCT_SCHEMAS rather than letting the week publish empty.\n")


def load_dept_map():
    with open(DEPT_MAP_FILE) as f:
        return json.load(f)


def dept_for(name, prefix, dmap):
    n = (name or "").strip()
    if prefix == "mari":
        return "f"
    if n in PRODUCT_OVERRIDES:
        return PRODUCT_OVERRIDES[n]
    vk = VKEY.get(prefix)
    return (dmap.get(vk, {}).get(n) or dmap.get("*", {}).get(n) or "b")


def load_rg_venue_map():
    """reporting_group -> venue, by which venue dominates it in rg_weekly.csv
    (the authoritative, Lightspeed-reconciled history). Used to attribute the
    Looker product backfill. Shared groups (Tap Beer, Cocktails) resolve to Stow,
    which is correct for the backfill because it only covers the Stowaway Bar
    site — HG's own-till sales aren't in it."""
    from collections import defaultdict as _dd
    tot = _dd(lambda: _dd(float))
    path = os.path.join(DATA, "rg_weekly.csv")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        for r in csv.DictReader(f):
            tot[r["reporting_group"]][r["venue"]] += float(r.get("sales_ex_gst") or 0)
    return {rg: max(vs, key=vs.get) for rg, vs in tot.items()}


def ingest_looker_backfill(agg, skip_weeks):
    """Fold data/looker_product_backfill.csv (a 13-month Lightspeed Insights
    export: Product Name, Reporting Group, Sale Closed Week [Mon start], Total Ex
    Tax) into agg, but ONLY for week-endings the daily insights feed doesn't
    already cover — daily stays authoritative for recent weeks. Quantity isn't in
    the export, so qty stays 0 for backfilled rows."""
    path = os.path.join(DATA, "looker_product_backfill.csv")
    if not os.path.exists(path):
        return 0
    rgv = load_rg_venue_map()
    n = 0
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            wk = (r.get("Sales Data Sale Closed Week") or "").strip()
            if not wk or wk == "None":
                continue
            try:
                we = (date.fromisoformat(wk) + timedelta(days=6)).isoformat()  # Mon -> Sun week-ending
            except ValueError:
                continue
            if we in skip_weeks:
                continue
            name = (r.get("Products Product Name") or "").strip()
            rg = (r.get("Products Reporting Group Name") or "").strip() or "Unmapped"
            ex = parse_num(r.get("Sales Data Total Ex Tax"))
            if not name or not ex:
                continue
            venue = rgv.get(rg, "stow")
            k = (we, venue, rg, normalize_product(name))
            agg[k][0] += ex
            agg[k][1] += parse_num(r.get("Sales Data Product Quantity"))   # units (cost is null in Looker)
            n += 1
    return n


def load_rg_names():
    """source-till prefix -> { product_name -> reporting_group_name }."""
    out = {"stow": {}, "hg": {}}
    for prefix, fn in (("stow", "stowaway_products.csv"), ("hg", "harry_gatos_products.csv")):
        path = os.path.join(DATA, "bo_exports", fn)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                name = (row.get("ProductName") or "").strip()
                g = (row.get("ReportingGroup") or "").strip()
                g = re.sub(r"\s*\[harrys\]\s*$", "", g, flags=re.I).strip()   # drop HG suffix
                if name and g:
                    out[prefix][name] = g
    return out


def main():
    dmap = load_dept_map()
    rgnames = load_rg_names()
    agg = defaultdict(lambda: [0.0, 0.0, 0.0])   # (we, venue, rg, product) -> [ex_gst, qty, cost]
    n_rg_skipped = 0
    n_legacy = defaultdict(int)

    # Stow + HG till files only. Mari's revenue rides in on the Stow 'm' slice.
    for path in sorted(glob.glob(os.path.join(DATA, "insights_*.csv"))):
        m = re.match(r"insights_(stow|hg)_(\d{4}-\d{2}-\d{2})\.csv$", os.path.basename(path))
        if not m:
            continue
        prefix, dstr = m.group(1), m.group(2)
        we = week_ending(date.fromisoformat(dstr)).isoformat()
        with open(path, encoding="utf-8-sig") as f:
            rd = csv.DictReader(f)
            schema = product_schema(rd.fieldnames or [], path)
            if schema is None:
                n_rg_skipped += 1
                continue                             # reporting-group export, not products
            if schema is not PRODUCT_SCHEMAS[0]:
                n_legacy[schema["label"]] += 1
            for row in rd:
                name = (row.get(schema["name"]) or "").strip()
                if not name:
                    continue                         # footer / subtotal row
                inc = parse_num(row.get(schema["inc"]))
                # No tax column on the older export, so gross up out of inc-GST —
                # the same fallback the daily aggregator uses for a hand-pulled file.
                tax = parse_num(row.get(schema["tax"])) if schema["tax"] else 0.0
                ex = (inc - tax) if tax else inc / 1.1
                qty = parse_num(row.get(schema["qty"]))
                if not ex:
                    continue
                code = dept_for(name, prefix, dmap)
                venue = DEPT_VENUE.get(code, prefix)             # reattribute cross-till
                rg = rgnames.get(prefix, {}).get(name) or UNMAPPED.get(code, "Unmapped")
                k = (we, venue, rg, normalize_product(name))
                agg[k][0] += ex
                agg[k][1] += qty
                agg[k][2] += parse_num(row.get(schema["cost"]))  # for GP% (daily feed carries cost)

    # Historical backfill (Lightspeed Insights export) for every week the daily
    # feed doesn't already cover — extends product trends back ~13 months.
    for label, n in sorted(n_legacy.items()):
        print(f"schema: read {n} file(s) on the {label} export shape")
    if n_rg_skipped:
        print(f"schema: skipped {n_rg_skipped} reporting-group export(s) (not product-level)")

    insights_weeks = {we for (we, _v, _rg, _p) in agg}
    n_back = ingest_looker_backfill(agg, insights_weeks)
    if n_back:
        print(f"backfill: folded {n_back} Looker rows for pre-daily weeks")

    rows = [{"week_ending": we, "venue": v, "reporting_group": rg, "product_name": p,
             "sales_ex_gst": round(a[0], 2), "qty": round(a[1], 2), "cost": round(a[2], 2)}
            for (we, v, rg, p), a in agg.items()]
    rows.sort(key=lambda r: (r["week_ending"], r["venue"], r["reporting_group"], -r["sales_ex_gst"]))

    out = os.path.join(DATA, "products_weekly.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["week_ending", "venue", "reporting_group", "product_name", "sales_ex_gst", "qty", "cost"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    # reconciliation: per venue/week product totals (for a sanity eyeball)
    tot = defaultdict(float)
    for r in rows:
        tot[(r["week_ending"], r["venue"])] += r["sales_ex_gst"]
    print(f"products_weekly.csv: {len(rows)} rows, "
          f"{len({r['week_ending'] for r in rows})} weeks, "
          f"{len({r['product_name'] for r in rows})} products")
    for k in sorted(tot)[-9:]:
        print(f"  {k[0]} {k[1]:5} ex-GST ${tot[k]:,.0f}")


if __name__ == "__main__":
    main()
