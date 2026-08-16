#!/usr/bin/env python3
"""
What to put on a scale next, ordered by the money standing on the answer.

Every batch yield in this book is one of four things, and only the first is a
fact:

    measured    somebody weighed it            data/measured_yields.yaml
    assumed     an estimate, to keep a dish costable at all
    basis       reasoning, written down        prep_yields.yaml with a basis
    scraped     Produce's 'Expected yield'     prep_yields.yaml, scraped
    bracket     the [1Kg] in the name          usually a PACK label
    none        nothing at all                 the dish cannot be costed

A batch yield is a divisor. Get it wrong and every dish drawing on that batch is
wrong by the same ratio, quietly and in one direction. Brisket sat at 6x and
jalapeno tequila at 7.5x until 2026-08-16, and neither showed up as anything
except a GP that looked a bit off.

So this ranks by REVENUE AT RISK: for each batch, the lifetime revenue of every
sold product that draws on it, transitively. It is not a measure of how wrong a
yield is -- nobody knows that until it is weighed, which is the point -- it is a
measure of how much rides on finding out.

    python3 scripts/yield_worklist.py            # print it
    python3 scripts/yield_worklist.py --write     # + JSON and a printable sheet
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.recipes.pipeline.build_recipe_feeds import (  # noqa: E402
    _prep_yield_estimates, resolve_yield, venue_of,
)

DATA = ROOT / "data"
BOOK = DATA / "lightspeed_recipes_costed.json"
SALES = ROOT / "dashboard" / "sales" / "products" / "index.json"
OUT = DATA / "_worklist"

VENUE_LABEL = {"stow": "Stowaway", "hg": "Harry Gatos", "mari": "Marilyna's",
               "stowaway": "Stowaway", "harry_gatos": "Harry Gatos",
               "marilynas": "Marilyna's"}


def measured_yields() -> dict:
    f = DATA / "measured_yields.yaml"
    if not f.exists():
        return {}
    doc = yaml.safe_load(f.read_text(encoding="utf-8-sig")) or {}
    out = {}
    for e in (doc.get("measured") or []):
        prev = out.get(e["batch"])
        # two real weighings that disagree are information; the latest wins
        if prev is None or str(e.get("measured_on")) >= str(prev.get("measured_on")):
            out[e["batch"]] = e
    return out


def revenue_by_product() -> dict:
    if not SALES.exists():
        return {}
    doc = json.loads(SALES.read_text(encoding="utf-8-sig"))
    out = {}
    for p in (doc.get("products") or []):
        out[(p.get("name") or "").strip().lower()] = float(p.get("lifetime_revenue_ex_gst") or 0)
    return out


def serve_yields() -> dict:
    """Products that are SOLD and also drawn as a sub-recipe.

    Their "1 serve" yields live in data/batch_yield_units.yaml, deliberately not
    in prep_yields.yaml -- the converter and audit_book both read "has a yield"
    as "is a batch", and a batch is excluded from serve costs, so putting them
    there made three sold products report 100% GP. They were showing here as
    having no yield at all, which sent BBQ Wings and an Espresso Martini to a
    kitchen scale. One serve is one serve; there is nothing to weigh.
    """
    f = DATA / "batch_yield_units.yaml"
    if not f.exists():
        return {}
    doc = yaml.safe_load(f.read_text(encoding="utf-8-sig")) or {}
    return {e["product"]: (e["yield_qty"], e["yield_unit"])
            for e in (doc.get("serve_yields") or [])}


def classify(name: str, est: dict, meas: dict) -> tuple[str, str]:
    """(confidence, why) for this batch's yield."""
    if name in meas:
        m = meas[name]
        return "measured", f"weighed by {m.get('measured_by','?')} on {m.get('measured_on','?')}"
    if name in serve_yields():
        return "serve", "one serve of a sold product — nothing to weigh"
    e = est.get(name)
    if e:
        basis = (e.get("basis") or "").strip()
        if basis.upper().startswith("ASSUMED"):
            # An estimate made to keep a dish costable at all, not a measurement
            # and not a documented kitchen basis. Lowest confidence there is, and
            # it should be the first thing a scale retires.
            return "assumed", basis.split("—")[-1].strip()[:90]
        if basis.upper().startswith("ESTIMATED"):
            return "basis", basis.split(".")[0][:90]
        if "SCRAPED" in basis:
            return "scraped", "Produce's own 'Expected yield' field"
        if basis:
            return "basis", basis.split(".")[0][:90]
        return "scraped", "prep_yields.yaml, no basis given"
    q, _ = resolve_yield(name)
    if q is None:
        return "none", "no yield anywhere — dishes using this cannot be costed"
    return "bracket", "read off the [size] in the name, which is usually a pack label"


def build() -> dict:
    book = json.loads(BOOK.read_text(encoding="utf-8-sig"))["recipes"]
    est, meas, rev = _prep_yield_estimates(), measured_yields(), revenue_by_product()

    # which sold products reach each batch, transitively
    uses = defaultdict(set)
    for product, r in book.items():
        if r.get("is_prep"):
            continue
        seen, frontier = set(), {product}
        while frontier:
            nxt = set()
            for n in frontier:
                for ln in (book.get(n, {}).get("ingredients") or []):
                    if ln.get("kind") == "subrecipe" and ln["ref"] not in seen:
                        seen.add(ln["ref"])
                        uses[ln["ref"]].add(product)
                        nxt.add(ln["ref"])
            frontier = nxt

    rows = []
    for batch in sorted({n for n, r in book.items() if r.get("is_prep")} | set(uses)):
        if batch not in book:
            continue
        conf, why = classify(batch, est, meas)
        if conf in ("measured", "serve"):
            continue                       # settled
        q, u = resolve_yield(batch)
        products = sorted(uses.get(batch, ()))
        at_risk = sum(rev.get(p.lower(), 0.0) for p in products)
        venues = sorted({VENUE_LABEL.get(venue_of(p), venue_of(p)) for p in products}) or ["—"]
        rows.append({
            "batch": batch, "confidence": conf, "why": why,
            "current_yield": (f"{q:g} {u}" if q is not None else None),
            "dishes": len(products), "venues": venues,
            "revenue_at_risk": round(at_risk, 2),
            "top_dishes": products[:6],
        })

    order = {"none": 0, "assumed": 1, "bracket": 2, "scraped": 3, "basis": 4, "serve": 5}
    rows.sort(key=lambda r: (-r["revenue_at_risk"], order.get(r["confidence"], 9)))
    return {"generated": date.today().isoformat(),
            "measured_so_far": len(meas), "outstanding": len(rows), "batches": rows}


SHEET = """<!doctype html><meta charset="utf-8"><title>Batch yields to weigh</title>
<style>
 @page{{size:A4;margin:14mm}}
 body{{font:12px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;color:#111;margin:0}}
 h1{{font-size:19px;margin:0 0 2px}} .sub{{color:#666;margin:0 0 14px}}
 table{{border-collapse:collapse;width:100%}}
 th{{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.04em;
     color:#666;border-bottom:1.5px solid #111;padding:5px 6px}}
 td{{border-bottom:1px solid #ddd;padding:7px 6px;vertical-align:top}}
 .b{{font-weight:600}} .m{{color:#666;font-size:10.5px}}
 .w{{border:1px solid #999;border-radius:3px;height:22px;width:74px;display:inline-block}}
 .tag{{font-size:9.5px;padding:1px 5px;border-radius:3px;border:1px solid #bbb;color:#555}}
 .none{{background:#fde8e8;border-color:#e88;color:#a11}}
 .assumed{{background:#fff3d6;border-color:#e0b350;color:#8a5a00}}
 .how{{margin:16px 0 0;padding:9px 11px;background:#f6f6f4;border-left:3px solid #111;font-size:11px}}
 tr{{page-break-inside:avoid}}
</style>
<h1>Batch yields — what to weigh</h1>
<p class="sub">{when} · in order of how much revenue is riding on the answer ·
 {n} outstanding, {done} already measured</p>
<table><tr><th style="width:31%">Batch</th><th style="width:13%">We currently assume</th>
<th style="width:9%">Where that came from</th><th style="width:20%">Used by</th>
<th style="width:14%">Weighed amount</th><th style="width:13%">Who / date</th></tr>
{rows}
</table>
<div class="how"><b>How:</b> weigh the empty container, make the batch, weigh it again,
subtract. The tare is the step people skip. <b>Write grams</b> unless you actually
poured it into a measuring jug — a sauce on a scale is grams, and calling grams
"ml" is how three batches ended up with a unit nobody had measured.
A number here beats every estimate in the system permanently.</div>
"""


def rev_note(r) -> str:
    return f" &middot; ${r['revenue_at_risk']:,.0f} rev" if r["revenue_at_risk"] else ""


def sheet(d: dict) -> str:
    rows = []
    for r in d["batches"]:
        cls = f' {r["confidence"]}' if r["confidence"] in ("none", "assumed") else ""
        cur = r["current_yield"] or "nothing — can't cost it"
        rows.append(
            f'<tr><td><span class="b">{html.escape(r["batch"])}</span>'
            f'<div class="m">{html.escape(", ".join(r["venues"]))}</div></td>'
            f'<td>{html.escape(cur)}</td>'
            f'<td><span class="tag{cls}">{r["confidence"]}</span></td>'
            f'<td class="m">{r["dishes"]} dish{"es" if r["dishes"] != 1 else ""}'
            f'{rev_note(r)}</td>'
            f'<td><span class="w"></span></td><td><span class="w"></span></td></tr>')
    return SHEET.format(when=d["generated"], n=d["outstanding"],
                        done=d["measured_so_far"], rows="\n".join(rows))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()
    d = build()

    print(f"{d['outstanding']} batch yield(s) still unweighed "
          f"({d['measured_so_far']} measured so far)\n")
    print(f"  {'batch':38s} {'assumed':>14s}  {'from':9s} {'dishes':>6s} {'revenue':>11s}")
    for r in d["batches"][:a.top]:
        money = f"${r['revenue_at_risk']:,.0f}" if r["revenue_at_risk"] else "-"
        print(f"  {r['batch'][:38]:38s} {str(r['current_yield'] or 'NONE'):>14s}  "
              f"{r['confidence']:9s} {r['dishes']:>6d} {money:>11s}")
    if a.write:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "yield_verification.json").write_text(
            json.dumps(d, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        (OUT / "yield_verification.html").write_text(sheet(d), encoding="utf-8")
        print(f"\nwrote {OUT.relative_to(ROOT)}/yield_verification.{{json,html}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
