#!/usr/bin/env python3
"""
Par model v2 -> v3 impact report.

Runs BOTH engines over the same committed inputs in one process (so nothing is
compared against a stale artefact) and writes data/_par_review/v3_impact.md:

  * how many SKUs move up / down / unchanged, per venue
  * the top 20 movers
  * the named problem items from the 28 Jul 2026 stock count
  * the shrinkage picture and the Christmas 2026 exposure

Run:  python3 scripts/par_v3_impact.py
"""
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.par import calendar as par_calendar  # noqa: E402
from modules.par import model  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "_par_review", "v3_impact.md")

# The SKUs the 28 Jul 2026 count put on the board, plus the two the par model
# has argued about before.
PROBLEM = [
    "Rooster Rojo Blanco Tequila [Bottle]",
    "San Pellegrino 500ml",
    "Coke Zero Can",
    "Bombay Dry [Bottle]",
    "Aperol [Bottle]",
    "Fellr",
    "Hyoketsu",
]


def _find(recs, needle):
    """Exact first, then case-insensitive substring across the par book."""
    if needle in recs:
        return needle
    low = needle.lower()
    hits = [k for k in recs if low in k.lower()]
    return sorted(hits, key=len)[0] if hits else None


def run():
    rows = model.load_weekly(DATA)
    out = {}
    for venue in ("stow", "hg"):
        v2, m2 = model.compute_venue(venue, DATA, rows=rows, engine="v2")
        v3, m3 = model.compute_venue(venue, DATA, rows=rows, engine="v3")
        out[venue] = (v2, m2, v3, m3)
    return out


def _lines(venue, v2, m2, v3, m3):
    L = []
    up = dn = same = 0
    movers = []
    for sku in sorted(set(v2) | set(v3)):
        a = v2.get(sku, {}).get("rec_par", 0.0)
        b = v3.get(sku, {}).get("rec_par", 0.0)
        d = round(b - a, 1)
        if d > 0.05:
            up += 1
        elif d < -0.05:
            dn += 1
        else:
            same += 1
        if abs(d) > 0.05:
            movers.append((d, sku, a, b))
    movers.sort(key=lambda x: -abs(x[0]))

    name = {"stow": "Stowaway", "hg": "Harry Gatos"}[venue]
    L.append(f"## {name} (`{venue}`)")
    L.append("")
    exp = m3["exposure"]
    L.append(f"Order Sun **{m3['order_sunday']}** -> delivery **{exp['delivery']}** -> "
             f"next delivery **{exp['next_delivery']}** — {exp['days']} days, "
             f"{exp['day_units']} weighted day-units = **{exp['exposure_ratio']:.2f}x** "
             f"a normal cycle ({exp['note']}).")
    L.append("")
    L.append(f"- SKUs compared: **{len(set(v2) | set(v3))}**")
    L.append(f"- v3 higher than v2: **{up}**   |   lower: **{dn}**   |   unchanged: **{same}**")
    sc = m3["service_classes"]
    L.append(f"- service classes: core(95%) {sc.get('core', 0)}, "
             f"standard(90%) {sc.get('standard', 0)}, tail(85%) {sc.get('tail', 0)}")
    L.append(f"- low movers on the Poisson path: "
             f"**{sum(1 for r in v3.values() if r['service'].get('path') == 'poisson')}**")
    nsh = sum(1 for r in v3.values() if "shrinkage_applied" in r["flags"])
    ncap = sum(1 for r in v3.values()
               if "shrinkage_capped_investigate" in r["flags"])
    L.append(f"- SKUs carrying a material measured shrinkage uplift: **{nsh}** "
             f"(**{ncap}** hit the 50%-of-demand cap and are flagged "
             f"`shrinkage_capped_investigate`)")
    L.append(f"- bookings: `{m3['bookings_status']}` — **shadow only**, not added to `rec_par`")
    L.append("")
    L.append("### Top 20 movers (v2 -> v3)")
    L.append("")
    L.append("| Δ | v2 | v3 | current | SKU | why |")
    L.append("|---:|---:|---:|---:|---|---|")
    for d, sku, a, b in movers[:20]:
        r = v3.get(sku, {})
        cur = r.get("current_par")
        why = []
        s = r.get("service", {})
        if s.get("path") == "poisson":
            why.append("Poisson low-mover")
        if r.get("shrinkage", {}).get("loss_per_week", 0) > 0:
            why.append(f"shrink +{r['shrinkage']['loss_per_week']:.2f}/wk")
        if "sanity_floored" in (r.get("flags") or []):
            why.append("sanity floor")
        idx = r.get("seasonal_index")
        if idx and abs(idx - 1.0) > 0.05:
            why.append(f"seasonal x{idx:.2f}")
        if r.get("override"):
            why.append(f"override {r['override']['type']}")
        L.append(f"| {d:+.1f} | {a} | {b} | {cur if cur is not None else '—'} | {sku} | "
                 f"{', '.join(why) or 'forecast/volatility'} |")
    L.append("")
    return L, (up, dn, same)


def main():
    res = run()
    ts = datetime.now(timezone.utc).astimezone().isoformat()
    L = ["# Par model v2 -> v3 — impact", "",
         f"_Generated {ts} by `scripts/par_v3_impact.py`. Both engines run over the "
         f"same committed inputs in one process._", ""]

    L.append("## The finding that started this")
    L.append("")
    L.append("The Lightspeed stock counts were being read as **net** variance, and the "
             "net variance lies. On **28 Jul 2026** the gross negative was "
             "**-$1,598** against a gross positive of **+$1,636** — a net of **+$37**, "
             "which reads as a clean count. It is not a clean count: $1,598 of stock "
             "left the building without a sale and an unrelated $1,636 of "
             "miscounts/mis-scans happened to cover it. v3 only ever takes the loss "
             "side, so a positive variance on one SKU can never pay for a loss on "
             "another.")
    L.append("")
    L.append("Measured on that count: Rooster Rojo -5.82 btl (-18.8%), "
             "San Pellegrino -6 (-19.4%), Coke Zero Can -31 (-67%).")
    L.append("")

    totals = {}
    for venue in ("stow", "hg"):
        v2, m2, v3, m3 = res[venue]
        lines, t = _lines(venue, v2, m2, v3, m3)
        totals[venue] = t
        L += lines

    # Problem items
    L.append("## The named problem items (Stowaway)")
    L.append("")
    L.append("| SKU | current par | v2 | v3 | Δ v2->v3 | v3 pre-override | shrink/wk | "
             "loss frac | path | note |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---|---|")
    v2, _m2, v3, _m3 = res["stow"]
    for needle in PROBLEM:
        key = _find(v3, needle)
        if key is None:
            L.append(f"| {needle} | — | — | — | — | — | — | — | — | "
                     f"not in the Stowaway par book |")
            continue
        a = v2.get(key, {}).get("rec_par", 0.0)
        r = v3[key]
        b = r["rec_par"]
        sh, sv = r["shrinkage"], r["service"]
        cur = r["current_par"]
        note = []
        if r.get("override"):
            note.append(f"hard `{r['override']['type']}` {r['override']['value']} pins it")
        if "held_no_recent_demand" in (r.get("flags") or []):
            note.append("held at live par — no rung-up demand")
        if "shrinkage_without_demand_mapping" in (r.get("flags") or []):
            note.append("**counts see it moving, the till mapping does not**")
        L.append(f"| {key} | {cur if cur is not None else '—'} | {a} | {b} | "
                 f"{b - a:+.1f} | {r['rec_par_pre_override']} | "
                 f"{sh['loss_per_week']:.2f} | "
                 f"{sh['loss_fraction']:.1%} | {sv.get('path')} | "
                 f"{'; '.join(note) or sv.get('service_class')} |")
    L.append("")
    unmapped = [k for k, r in v3.items()
                if "shrinkage_without_demand_mapping" in (r.get("flags") or [])]
    if unmapped:
        L.append(f"**{len(unmapped)} Stowaway SKUs are losing stock that the till never "
                 f"rang up at all** — the stock counts see the units leaving, "
                 f"`products_weekly.csv` shows no demand, so the par is held at the live "
                 f"value and the SKU is flagged `shrinkage_without_demand_mapping`. This "
                 f"is a POS naming / product-mapping job, not a par job: "
                 + ", ".join(f"`{u}`" for u in sorted(unmapped)[:12])
                 + ("…" if len(unmapped) > 12 else "") + ".")
        L.append("")

    # Shrinkage picture
    _v2, _m2, _v3, m3 = res["stow"]
    sh = m3["shrinkage"]
    with_loss = sorted((v for v in sh.values()
                        if v["loss_per_week"] >= model.MATERIAL_LOSS_WK),
                       key=lambda v: -v["loss_per_week"])
    fracs = sorted(v["loss_fraction"] for v in with_loss if v["loss_fraction"] > 0)
    med = fracs[len(fracs) // 2] if fracs else 0.0
    L.append("## Shrinkage (Stowaway)")
    L.append("")
    L.append(f"- stock counts used: **{len(m3['shrinkage_summary']['counts'])}**, "
             f"giving **{len(m3['shrinkage_summary']['periods'])}** measurable periods")
    L.append(f"- SKUs with a measurable loss: **{len(with_loss)}**")
    L.append(f"- median loss fraction (of modelled demand): **{med:.1%}**")
    L.append(f"- capped at 50% of demand and flagged for investigation: "
             f"**{sum(1 for v in sh.values() if v['investigate'])}** "
             f"(a further {sum(1 for v in sh.values() if v['capped'] and not v['investigate'])} "
             f"hit the cap on a sub-0.05-unit/week 'loss' — that is count "
             f"rounding on a fractional bottle, not shrinkage)")
    L.append("")
    L.append("### Top 8 by units lost per week")
    L.append("")
    L.append("| SKU | loss/wk | loss fraction | periods | capped |")
    L.append("|---|---:|---:|---:|---|")
    for v in with_loss[:8]:
        L.append(f"| {v['product']} | {v['loss_per_week']:.2f} | "
                 f"{v['loss_fraction']:.1%} | {v['n_periods']} | "
                 f"{'YES — investigate' if v['capped'] else 'no'} |")
    L.append("")

    # Christmas
    cal = par_calendar.load_calendar(DATA)
    xm = par_calendar.christmas_2026_exposure(cal)
    L.append("## Christmas 2026 — the 14-day gap")
    L.append("")
    L.append(f"Order **Sun 20 Dec 2026** -> delivery **{xm['delivery']}** -> next "
             f"realistic delivery **{xm['next_delivery']}**: **{xm['days']} days**, "
             f"**{xm['day_units']} weighted day-units** = "
             f"**{xm['exposure_ratio']:.2f}x** a normal cycle, over peak summer trade.")
    L.append("")
    L.append("Chain: Mon 28 Dec is a public holiday, so the Wed 30 Dec ILG run slips to "
             "Fri 1 Jan; Fri 1 Jan is New Year's Day, so that delivery does not happen "
             "at all and the goods land on Wed 6 Jan. Full note: "
             "`data/_par_review/christmas_2026.md`.")
    L.append("")
    L.append("Supplier Christmas shutdowns are **not yet known** — "
             "`data/par_calendar.json -> supplierShutdowns` carries PENDING entries to "
             "fill in once suppliers publish in December. Until then the 6 Jan "
             "resumption is the OPTIMISTIC case.")
    L.append("")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        fh.write("\n".join(L) + "\n")
    print(f"wrote {OUT}")
    for venue, (up, dn, same) in totals.items():
        print(f"  {venue}: up {up} / down {dn} / unchanged {same}")


if __name__ == "__main__":
    main()
