#!/usr/bin/env python3
"""
Par model v3 — build step.

Computes par recommendations for Stowaway (stow) + Harry Gatos (hg) off the live
feeds and writes:
    data/par_recommendations_stowaway.json
    data/par_recommendations_harry_gatos.json
    data/par_shrinkage.json              (Stowaway; HG has no stock counts yet)

v3 adds, per SKU: a measured shrinkage channel from the Lightspeed stock counts,
an (R,S) service-level par over the REAL delivery-to-delivery exposure window,
a week-of-year seasonal index, and a shadow bookings uplift that is recorded but
not applied. See modules/par/model.py for the why of each.

COVERAGE GATE: if any Classic/Signature cocktail with recent sales does not
resolve to a recipe (open-price '$NN Custom Cocktail' excepted) the build exits
NONZERO and prints the offenders — fail toward review.

UNATTRIBUTED-VOLUME GATE: the same idea, one level down. A POS line in a
stock-bearing drink group whose volume reaches NO par SKU is invisible demand:
the par collapses and nothing says so. $54,794 of Stowaway drink sales over 13
weeks were landing nowhere when this gate was written — 231.7 schooners a week
of the house lager among them. If more than $2,000 of 13-week ex-GST revenue
reaches no par SKU, the build exits NONZERO. Post-mix and made-to-order lines
are excused BY NAME in data/par_aliases.json `_intentionally_unattributed`, so
the gate stays quiet about the things that can never attribute and loud about
the things that just stopped.

SHARED STOCK ACROSS VENUES: some lines pour at Harry Gatos out of stock that is
held and ordered centrally on STOWAWAY par SKUs (the shared taps, the Coke and
Sprite cans). An alias target may name the venue — "stow:Kirin [Keg]" in the hg
map — and that HG volume is then added to Stowaway's par SKU, contributes to no
HG par SKU, and appears in HG's report under `attributed_to_other_venue` rather
than as a miss. A cross-venue target is validated against the TARGET venue's par
universe, so a typo there fails the build exactly like a same-venue one.

THE [HG] SUFFIX RULE. Zak, 2026-08-10: "same as the wines. pretty much any SKU
labelled with [HG] is a harrys SKU that draws from stowaway stock". That is a
rule, not a list, so it is mechanised rather than transcribed: any HG till line
carrying the '[HG]' suffix has the suffix stripped, the remainder matched
against STOWAWAY's par universe (tolerating the '- Bottle' / '[Bottle]' /
'[Keg]' / 'Can' / 'Tin' drift between the two catalogues), and its consumption
routed to that Stowaway par SKU. A NEW [HG] product therefore attributes
correctly the first week it sells, with no file edit. An explicit alias always
overrides the rule, and where the stripped name matches no Stowaway par SKU the
rule REFUSES to guess: the line stays unattributed for the gate above to catch,
and is listed as `hg_suffix_unresolved` so a human can write the alias.

MARILYNA'S. Zak, 2026-08-10: "and yes you need to figure out marilynas
consumption as well! that all gets lumped into the stowaway lightspeed pars".
Marilyna's has no till and no Purchase module — its sales are an attributed
slice of the Stowaway till and its stock IS Stowaway's stock. So it becomes a
third SOURCE venue for the same cross-venue machinery: a mari POS line whose
normalised name IS a Stowaway par SKU routes its consumption there (MariNameRule
— exact names only; see its docstring for why the loose container tier is off
over a catalogue that is 80% pizza), an explicit "stow:" alias in the new `mari`
map always overrides it, and no par SKU exists for anything to leak into.

It gets no par_recommendations file, because it has no pars. It gets
data/par_unattributed_marilynas.json, which classifies every mari line as
attributed / needs_recipe / unattributed / not-stock-bearing. `needs_recipe` —
215 lines, 660 units and $136k a quarter of pizza consuming Stowaway flour,
mozzarella, pepperoni, bases and boxes through recipes that do not exist — is
reported LOUDLY and does NOT fail the build: it is a known, quantified backlog
(data/_par_review/marilynas_recipe_gap.md), not a regression, and failing on it
would only teach people to ignore the build. Genuine stock-bearing alias
failures keep the $2,000 gate exactly as it is.

Run:  /opt/homebrew/bin/python3.12 scripts/build_par_model.py   (Actions: python 3.11)
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.par import calendar as par_calendar  # noqa: E402
from modules.par import model  # noqa: E402
from modules.par import shrinkage as shrinkage_mod  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUT = {"stow": "par_recommendations_stowaway.json", "hg": "par_recommendations_harry_gatos.json"}
SHRINK_OUT = "par_shrinkage.json"
# Same venue-name convention as par_recommendations_* / par_flags_*.
UNATTR_OUT = {"stow": "par_unattributed_stowaway.json",
              "hg": "par_unattributed_harry_gatos.json",
              "mari": "par_unattributed_marilynas.json"}
VENUE_LABEL = {"stow": "Stowaway", "hg": "Harry Gatos", "mari": "Marilyna's"}


def _summary(recs):
    inc = dec = same = 0
    changes = []
    for sku, r in recs.items():
        cur = r["current_par"]
        if cur is None:
            if r["rec_par"] > 0:
                inc += 1
                changes.append((r["rec_par"] - 0.0, sku, 0.0, r["rec_par"]))
            continue
        d = round(r["rec_par"] - cur, 1)
        if d > 0:
            inc += 1
        elif d < 0:
            dec += 1
        else:
            same += 1
        if abs(d) > 1e-9:
            changes.append((d, sku, cur, r["rec_par"]))
    changes.sort(key=lambda x: -abs(x[0]))
    return inc, dec, same, changes


def build_venue(venue, rows):
    generated_at = datetime.now(timezone.utc).astimezone().isoformat()
    recs, meta = model.compute_venue(venue, DATA, rows=rows)
    inc, dec, same, changes = _summary(recs)

    # Shrinkage feed (Stowaway only today — HG has no stock counts).
    if meta["shrinkage"]:
        shrinkage_mod.write_json(
            os.path.join(DATA, SHRINK_OUT), venue, meta["shrinkage"],
            meta["shrinkage_summary"], generated_at)

    payload = {
        "schema_version": 2,
        "engine": meta["engine"],
        "generated_at": generated_at,
        "venue": venue,
        "source": "products_weekly.csv + recipes + bo_exports + scrape + "
                  "par_overrides.json + stock_counts + par_calendar.json",
        "weeks": meta["weeks"],
        "week_range": meta["week_range"],
        "order_sunday": meta["order_sunday"],
        "exposure": meta["exposure"],
        "target_week_of_year": meta["target_week_of_year"],
        "service_classes": meta["service_classes"],
        "bookings": {
            "live": meta["bookings_live"],
            "status": meta["bookings_status"],
            "note": "SHADOW MODE — bookings_uplift_shadow is recorded per SKU but "
                    "is NOT added to rec_par (modules/par/bookings.py BOOKINGS_LIVE=False)",
        },
        "summary": {
            "n_skus": len(recs),
            "increase": inc,
            "decrease": dec,
            "unchanged": same,
            "coverage_gaps": [n for n, _ in meta["coverage_gaps"]],
            # counted off the FLAGS, so these agree with what a human reading
            # the SKU list sees (a loss under MATERIAL_LOSS_WK is count rounding)
            "shrinkage_applied": sum(1 for r in recs.values()
                                     if "shrinkage_applied" in r["flags"]),
            "shrinkage_capped": sum(1 for r in recs.values()
                                    if "shrinkage_capped_investigate" in r["flags"]),
            "shrinkage_without_demand_mapping": sum(
                1 for r in recs.values()
                if "shrinkage_without_demand_mapping" in r["flags"]),
            "low_mover_poisson": sum(1 for r in recs.values()
                                     if r["service"].get("path") == "poisson"),
        },
        "skus": [recs[k] for k in sorted(recs)],
    }
    with open(os.path.join(DATA, OUT[venue]), "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
        fh.write("\n")
    return recs, meta, (inc, dec, same, changes)


def write_unattributed(venue, meta, generated_at):
    """Write data/par_unattributed_<venue>.json and print the warning block.

    Returns (total_revenue, n_offenders, alias_errors) so main() can decide
    whether to fail the build.
    """
    offenders = meta["unattributed"]
    intentional = meta["unattributed_intentional"]
    alias_errors = meta["aliases"]["unknown_targets"]
    exported = meta["attributed_to_other_venue"]
    auto_hg = meta.get("auto_hg_suffix") or []
    hg_unresolved = meta.get("hg_suffix_unresolved") or []
    total = meta["unattributed_revenue"]
    payload = {
        "schema_version": 2,
        "generated_at": generated_at,
        "venue": venue,
        "window_weeks": meta["unattributed_weeks"],
        "week_range": meta["week_range"],
        "threshold_revenue_ex_gst": model.UNATTRIBUTED_FAIL_REVENUE,
        "what_this_is": (
            "POS lines in a stock-bearing drink group whose sales volume reached "
            "NO par SKU. Each one is demand the par model cannot see, so the "
            "affected par collapses silently. Fix by adding an entry to "
            "data/par_aliases.json (if the stock item exists under another name) "
            "or by creating the stock item in the Lightspeed Purchase module."),
        "aliases_in_force": meta["aliases"]["n"],
        "cross_venue_aliases": meta["aliases"]["cross_venue_targets"],
        "alias_targets_not_found": alias_errors,
        "what_attributed_to_other_venue_is": (
            "POS lines sold at this venue whose stock is held and ordered at the "
            "OTHER venue, against that venue's par SKU (data/par_aliases.json, a "
            "'<venue>:<sku>' target). They are ATTRIBUTED — the volume lands on "
            "the named par SKU there — and they contribute nothing here, so they "
            "are neither unattributed nor double-counted. Listed so a line "
            "leaving this venue's numbers is never silent."),
        "what_auto_hg_suffix_is": (
            "Zak, 2026-08-10: 'pretty much any SKU labelled with [HG] is a "
            "harrys SKU that draws from stowaway stock'. That is a RULE, so the "
            "model applies it automatically: an HG till line carrying the '[HG]' "
            "suffix has the suffix stripped and the remainder matched against "
            "STOWAWAY's par universe, and its consumption is routed to that "
            "Stowaway par SKU. No file edit is needed for a new [HG] product. An "
            "explicit alias in data/par_aliases.json always overrides the rule. "
            "`auto_hg_suffix` lists every line the rule placed and why; "
            "`hg_suffix_unresolved` lists every [HG] line whose stripped name "
            "matched no Stowaway par SKU — the rule refuses to guess, so those "
            "fall through to the ordinary matchers and, if nothing places them, "
            "stay unattributed above. Give one an explicit alias to fix it."),
        "summary": {
            "unattributed_lines": len(offenders),
            "unattributed_revenue_ex_gst": total,
            "hg_suffix_rule_active": bool(meta.get("hg_suffix_rule_active")),
            "auto_hg_suffix_resolved_lines": len(auto_hg),
            "auto_hg_suffix_revenue_ex_gst": round(
                sum(r["revenue_ex_gst_window"] for r in auto_hg), 2),
            "hg_suffix_unresolved_lines": len(hg_unresolved),
            "hg_suffix_unresolved_still_unattributed": sum(
                1 for r in hg_unresolved if r["still_unattributed"]),
            "intentionally_unattributed_lines": len(intentional),
            "intentionally_unattributed_revenue_ex_gst": round(
                sum(r["revenue_ex_gst_window"] for r in intentional), 2),
            "attributed_to_other_venue_lines": len(exported),
            "attributed_to_other_venue_revenue_ex_gst": round(
                sum(r["revenue_ex_gst_window"] for r in exported), 2),
        },
        "unattributed": offenders,
        "intentionally_unattributed": intentional,
        "attributed_to_other_venue": exported,
        "auto_hg_suffix": auto_hg,
        "hg_suffix_unresolved": hg_unresolved,
    }
    with open(os.path.join(DATA, UNATTR_OUT[venue]), "w") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print(f"\n  --- UNATTRIBUTED DRINK VOLUME ({meta['unattributed_weeks']} wk) ---")
    print(f"  aliases in force: {meta['aliases']['n']}   "
          f"intentionally unattributed (post-mix / made to order): "
          f"{len(intentional)} lines, "
          f"${sum(r['revenue_ex_gst_window'] for r in intentional):,.0f}")
    if alias_errors:
        print("  !! ALIAS TARGETS THAT ARE NOT PAR SKUs (these attribute NOTHING):")
        for pos, sku in sorted(alias_errors.items()):
            print(f"       {pos!r} -> {sku!r}")
    if exported:
        rev_x = sum(r["revenue_ex_gst_window"] for r in exported)
        print(f"  attributed to ANOTHER venue's par (shared central stock): "
              f"{len(exported)} line(s), ${rev_x:,.0f}")
        for r in exported:
            mark = "  [auto [HG]]" if r.get("via") == "hg_suffix_rule" else ""
            print(f"     {r['revenue_ex_gst_window']:>9,.0f} {r['qty_per_week']:>8.2f}  "
                  f"{r['product'][:30]:30s} -> {r['target_venue']}:{r['target_sku']}{mark}")
    if meta.get("hg_suffix_rule_active"):
        # The one-line summary: how much of Zak's "[HG] draws from stowaway
        # stock" rule the model could apply on its own, and how much it refused
        # to guess at. A silent rule is a rule nobody can trust.
        still = sum(1 for r in hg_unresolved if r["still_unattributed"])
        print(f"  [HG] suffix rule: {len(auto_hg)} line(s) auto-resolved to "
              f"{model.HG_SUFFIX_STOCK_VENUE} par SKUs "
              f"(${sum(r['revenue_ex_gst_window'] for r in auto_hg):,.0f}), "
              f"{len(hg_unresolved)} left unresolved ({still} still unattributed)")
        for r in auto_hg:
            print(f"       auto  {r['qty_per_week']:>6.2f}/wk  {r['pos_line'][:34]:34s}"
                  f" -> {r['target_venue']}:{r['target_sku']}   [{r['match']}]")
        for r in hg_unresolved:
            if not r["still_unattributed"]:
                continue
            print(f"       ??    {r['qty_per_week']:>6.2f}/wk  {r['pos_line'][:34]:34s}"
                  f" -> NO {model.HG_SUFFIX_STOCK_VENUE} par SKU for "
                  f"{r['stripped']!r} (add an explicit alias)")
    if not offenders:
        print("  Unattributed gate: PASS — every stock-bearing drink line "
              "reaches a par SKU.")
        return total, 0, alias_errors
    verdict = ("FAIL" if total > model.UNATTRIBUTED_FAIL_REVENUE else "warn")
    print(f"  !! {len(offenders)} stock-bearing POS line(s) reach NO par SKU — "
          f"${total:,.0f} ex-GST over {meta['unattributed_weeks']} weeks "
          f"[{verdict}, threshold ${model.UNATTRIBUTED_FAIL_REVENUE:,.0f}]")
    print(f"     {'$ 13wk':>9} {'qty/wk':>8}  {'reporting group':22s} POS line")
    for r in offenders[:20]:
        known = "  (known, _unmapped_investigate)" if r.get("known_unmapped") else ""
        print(f"     {r['revenue_ex_gst_window']:>9,.0f} {r['qty_per_week']:>8.1f}  "
              f"{str(r['reporting_group'])[:22]:22s} {r['product'][:40]}{known}")
    if len(offenders) > 20:
        print(f"     ... and {len(offenders) - 20} more — see data/{UNATTR_OUT[venue]}")
    return total, len(offenders), alias_errors


def write_source_venue(venue, rows, generated_at):
    """Write data/par_unattributed_marilynas.json and print the summary block.

    Marilyna's has no pars, so it gets no par_recommendations file — but it
    absolutely gets a guard. Everything it sells comes off Stowaway's shelf, so
    every mari line is either attributed to a Stowaway par SKU, a known recipe
    gap, or invisible demand; the third one is the bug and this is what makes it
    impossible to have quietly.

    Returns (gate_revenue, n_offenders, alias_errors). `needs_recipe` volume is
    NOT in gate_revenue — see model.MARI_FINISHED_GOODS_RGS for why.
    """
    rep = model.source_venue_report(venue, DATA, rows=rows)
    label = VENUE_LABEL.get(venue, venue)
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "venue": venue,
        "venue_label": label,
        "stock_venue": rep["stock_venue"],
        "window_weeks": rep["window_weeks"],
        "week_range": rep["week_range"],
        "threshold_revenue_ex_gst": model.UNATTRIBUTED_FAIL_REVENUE,
        "what_this_is": (
            f"{label} has NO till and NO Purchase module of its own — its sales "
            "are an attributed slice of the Stowaway till and its stock IS "
            "Stowaway's stock, ordered against STOWAWAY par SKUs. Zak, "
            "2026-08-10: 'that all gets lumped into the stowaway lightspeed "
            "pars'. So it holds no pars, gets no par_recommendations file, and "
            "is audited here instead: every line it rings is either attributed "
            "to a Stowaway par SKU, a known recipe gap, or invisible demand."),
        "classification_key": {
            "attributed_to_other_venue": (
                "Resolved to a Stowaway par SKU — by an explicit 'stow:' alias "
                "in data/par_aliases.json `mari`, or automatically by "
                "MariNameRule (the line's normalised name IS a Stowaway par "
                "SKU). The volume is counted ONCE, in Stowaway's build, under "
                "drivers.cross_venue_by_venue_wk['mari']."),
            "needs_recipe": (
                "A pizza, a pizza add-on or a cocktail jar. It consumes "
                "Stowaway stock through INGREDIENTS (flour, mozzarella, "
                "pepperoni, bases, boxes) and can never resolve to a single par "
                "SKU, so no alias will ever fix it — the fix is a recipe. A "
                "KNOWN, QUANTIFIED BACKLOG: reported loudly, excluded from the "
                "$2,000 gate. See data/_par_review/marilynas_recipe_gap.md."),
            "unattributed": (
                "A discrete, stock-bearing drink line that reached NO par SKU. "
                "This IS a genuine alias failure and it keeps the ordinary "
                f"${model.UNATTRIBUTED_FAIL_REVENUE:,.0f} gate semantics."),
            "other_unattributed": (
                "Reached no par SKU and is neither stock-bearing by reporting "
                "group nor a finished good — today this is 'Delivery Alcohol', "
                "real bottles and cans whose group name the stock-bearing "
                "regex does not match. Outside the gate's remit, exactly as at "
                "the other two venues, but listed so it is never silent."),
            "auto_unresolved": (
                "Lines MariNameRule was asked about and refused, with the "
                "nearest Stowaway par SKU by container base where there is one. "
                "The work queue: each is one explicit alias away. Pizzas are "
                "excluded — they are `needs_recipe`, not an alias to write."),
        },
        "aliases_in_force": rep["aliases"]["n"],
        "cross_venue_aliases": rep["aliases"]["cross_venue_targets"],
        "alias_targets_not_found": rep["aliases"]["unknown_targets"],
        "summary": {
            "attributed_lines": len(rep["attributed_to_other_venue"]),
            "attributed_qty_per_week": round(
                sum(r["qty_per_week"] for r in rep["attributed_to_other_venue"]), 2),
            "attributed_revenue_ex_gst": round(
                sum(r["revenue_ex_gst_window"]
                    for r in rep["attributed_to_other_venue"]), 2),
            "auto_resolved_lines": len(rep["auto_resolved"]),
            "auto_unresolved_lines": len(rep["auto_unresolved"]),
            "needs_recipe_lines": len(rep["needs_recipe"]),
            "needs_recipe_qty_per_week": rep["needs_recipe_qty_per_week"],
            "needs_recipe_revenue_ex_gst": rep["needs_recipe_revenue"],
            "unattributed_lines": len(rep["unattributed"]),
            "unattributed_revenue_ex_gst": rep["unattributed_revenue"],
            "other_unattributed_lines": len(rep["other_unattributed"]),
            "other_unattributed_revenue_ex_gst": round(
                sum(r["revenue_ex_gst_window"] for r in rep["other_unattributed"]), 2),
            "intentionally_unattributed_lines": len(
                rep["intentionally_unattributed"]),
        },
        "attributed_to_other_venue": rep["attributed_to_other_venue"],
        "auto_resolved": rep["auto_resolved"],
        "auto_unresolved": rep["auto_unresolved"],
        "needs_recipe": rep["needs_recipe"],
        "unattributed": rep["unattributed"],
        "other_unattributed": rep["other_unattributed"],
        "intentionally_unattributed": rep["intentionally_unattributed"],
    }
    with open(os.path.join(DATA, UNATTR_OUT[venue]), "w") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    s = payload["summary"]
    print(f"\n===== {label.upper()} (no pars of its own — "
          f"stock is {rep['stock_venue']}'s) =====")
    # THE one-line build summary Zak asked for.
    print(f"  mari -> {rep['stock_venue']}: {s['attributed_lines']} line(s) "
          f"attributed, {s['attributed_qty_per_week']:.1f} units/wk, "
          f"${s['attributed_revenue_ex_gst']:,.0f} over "
          f"{rep['window_weeks']} wk  "
          f"({s['auto_resolved_lines']} by rule, "
          f"{rep['aliases']['n']} explicit alias(es))")
    for r in rep["attributed_to_other_venue"]:
        print(f"       {r['qty_per_week']:>6.2f}/wk  {r['product'][:32]:32s}"
              f" -> {r['target_venue']}:{r['target_sku']}   [{r['via']}]")
    print(f"  !! RECIPE GAP (known backlog, does NOT fail the build): "
          f"{s['needs_recipe_lines']} finished-goods line(s), "
          f"{s['needs_recipe_qty_per_week']:,.0f} units/wk, "
          f"${s['needs_recipe_revenue_ex_gst']:,.0f} ex-GST over "
          f"{rep['window_weeks']} wk consume Stowaway stock through "
          f"ingredients the model cannot see.")
    print(f"     -> data/_par_review/marilynas_recipe_gap.md")
    if rep["other_unattributed"]:
        print(f"  not stock-bearing by group (outside the gate, as elsewhere): "
              f"{s['other_unattributed_lines']} line(s), "
              f"${s['other_unattributed_revenue_ex_gst']:,.0f}")
    if rep["auto_unresolved"]:
        print(f"  work queue — {len(rep['auto_unresolved'])} line(s) one explicit "
              f"alias away:")
        for r in rep["auto_unresolved"][:8]:
            print(f"       {r['qty_per_week']:>6.2f}/wk  "
                  f"{r['revenue_ex_gst_window']:>8,.0f}  {r['pos_line'][:38]:38s}")
        if len(rep["auto_unresolved"]) > 8:
            print(f"       ... and {len(rep['auto_unresolved']) - 8} more — "
                  f"see data/{UNATTR_OUT[venue]}")
    errs = rep["aliases"]["unknown_targets"]
    if errs:
        print("  !! ALIAS TARGETS THAT ARE NOT PAR SKUs (these attribute NOTHING):")
        for pos, sku in sorted(errs.items()):
            print(f"       {pos!r} -> {sku!r}")
    if not rep["unattributed"]:
        print("  Unattributed gate: PASS — every stock-bearing drink line "
              "reaches a par SKU.")
    else:
        verdict = ("FAIL" if rep["unattributed_revenue"]
                   > model.UNATTRIBUTED_FAIL_REVENUE else "warn")
        print(f"  !! {len(rep['unattributed'])} stock-bearing POS line(s) reach "
              f"NO par SKU — ${rep['unattributed_revenue']:,.0f} ex-GST over "
              f"{rep['window_weeks']} weeks [{verdict}, threshold "
              f"${model.UNATTRIBUTED_FAIL_REVENUE:,.0f}]")
        for r in rep["unattributed"][:20]:
            print(f"     {r['revenue_ex_gst_window']:>9,.0f} "
                  f"{r['qty_per_week']:>8.1f}  "
                  f"{str(r['reporting_group'])[:22]:22s} {r['product'][:40]}")
    return rep["unattributed_revenue"], len(rep["unattributed"]), errs


def main():
    rows = model.load_weekly(DATA)
    gate_failed = False
    unattr_failed = False
    all_meta = {}
    for venue in model.PAR_VENUES:
        recs, meta, (inc, dec, same, changes) = build_venue(venue, rows)
        all_meta[venue] = (recs, meta, (inc, dec, same, changes))
        print(f"\n===== {venue.upper()} =====")
        print(f"  weeks {meta['week_range'][0]}..{meta['week_range'][1]}  ({meta['weeks']} wk)")
        exp = meta["exposure"]
        print(f"  order Sun {meta['order_sunday']} -> deliver {exp['delivery']}"
              f" -> next {exp['next_delivery']}"
              f"  ({exp['days']}d, {exp['day_units']} day-units,"
              f" {exp['exposure_ratio']:.2f}x normal)  [{exp['note']}]")
        print(f"  service classes: {meta['service_classes']}   "
              f"Poisson low-movers: "
              f"{sum(1 for r in recs.values() if r['service'].get('path') == 'poisson')}")
        nsh = sum(1 for r in recs.values() if "shrinkage_applied" in r["flags"])
        ncap = sum(1 for r in recs.values()
                   if "shrinkage_capped_investigate" in r["flags"])
        nun = sum(1 for r in recs.values()
                  if "shrinkage_without_demand_mapping" in r["flags"])
        print(f"  shrinkage: {nsh} SKUs with a material measured loss, "
              f"{ncap} capped (investigate), {nun} losing stock the till never rang up")
        print(f"  bookings (SHADOW, not applied): {meta['bookings_status']}")
        print(f"  SKUs: {len(recs)}   increase {inc} / decrease {dec} / unchanged {same}")
        print("  Top 10 changes vs current par:")
        for d, sku, cur, rec in changes[:10]:
            print(f"    {d:+7.1f}   {cur!s:>6} -> {rec:<6}  {sku}")
        gaps = meta["coverage_gaps"]
        if gaps:
            gate_failed = True
            print(f"  !! COVERAGE GATE FAIL — {len(gaps)} live cocktail(s) with no recipe:")
            for name, q in gaps:
                print(f"       {q:8.0f}  {name}")
        else:
            print("  Coverage gate: PASS (every recent Classic/Signature cocktail resolves)")

        imp = meta.get("cross_venue_imported_by_venue") or {}
        if imp:
            print("  cross-venue stock IMPORTED into this venue's pars "
                  "(other venues' tills, this venue's shelf):")
            for src in sorted(imp):
                print(f"     from {src}: {len(imp[src])} par SKU(s), "
                      f"{sum(imp[src].values()):,.1f} units over "
                      f"{meta['weeks']} wk")

        total, n_off, alias_errors = write_unattributed(
            venue, meta, datetime.now(timezone.utc).astimezone().isoformat())
        if alias_errors:
            unattr_failed = True
        if total > model.UNATTRIBUTED_FAIL_REVENUE:
            unattr_failed = True

    # ── the venues that sell but hold nothing ──────────────────────────────
    # Marilyna's. No pars, so no recommendations file — but the same guard, and
    # the same $2,000 gate on genuine alias failures. `needs_recipe` volume is
    # deliberately outside that gate: it is a known backlog with a name, not a
    # regression, and hard-failing the build on it would only teach people to
    # ignore the build.
    for venue in model.SOURCE_ONLY_VENUES:
        total, n_off, alias_errors = write_source_venue(
            venue, rows, datetime.now(timezone.utc).astimezone().isoformat())
        if alias_errors:
            unattr_failed = True
        if total > model.UNATTRIBUTED_FAIL_REVENUE:
            unattr_failed = True

    _sanity(all_meta)

    if gate_failed:
        print("\nBUILD FAILED: coverage gate not satisfied.", file=sys.stderr)
        sys.exit(1)
    if unattr_failed:
        print(f"\nBUILD FAILED: more than "
              f"${model.UNATTRIBUTED_FAIL_REVENUE:,.0f} of stock-bearing drink "
              f"revenue reaches no par SKU (or an alias points at a par SKU that "
              f"does not exist).\n"
              f"Fix: map the offending POS lines in data/par_aliases.json, or "
              f"create the missing stock item in the Purchase module. See "
              f"{', '.join('data/' + f for f in UNATTR_OUT.values())}.",
              file=sys.stderr)
        sys.exit(1)
    print("\nPar recommendations written:", ", ".join(OUT.values()))
    print("Unattributed-volume reports written:", ", ".join(UNATTR_OUT.values()))


def _sanity(all_meta):
    print("\n===== SANITY =====")
    stow = all_meta["stow"][0]
    rooster = "Rooster Rojo Blanco Tequila [Bottle]"
    r = stow.get(rooster)
    if r:
        ov = r["override"] or {}
        reserve = float(ov.get("value") or 0) if ov.get("type") == "reserve" else 0.0
        ok = r["rec_par"] >= reserve
        print(f"  Rooster rec_par={r['rec_par']} (reserve {reserve} additive: "
              f"{'OK' if ok else 'FAIL'});"
              f" recipe_wk driver={r['drivers']['recipe_wk']}"
              f" ({'margarita reaches Rooster' if r['drivers']['recipe_wk'] > 0 else 'NO recipe consumption!'})"
              f" variance_wk={r['drivers']['variance_wk']}")
    else:
        print(f"  !! {rooster} not found in recs")
    bombay = stow.get("Bombay Dry [Bottle]")
    if bombay:
        print(f"  Bombay Dry [Bottle] rec_par={bombay['rec_par']} "
              f"(pour_wk={bombay['drivers']['pour_wk']}, recipe_wk={bombay['drivers']['recipe_wk']}, "
              f"variance_wk={bombay['drivers']['variance_wk']}, override={bombay['override']})")

    cal = par_calendar.load_calendar(DATA)
    xm = par_calendar.christmas_2026_exposure(cal)
    print(f"  CHRISTMAS 2026: order Sun 2026-12-20 -> deliver {xm['delivery']}"
          f" -> next delivery {xm['next_delivery']}: {xm['days']} days,"
          f" {xm['day_units']} day-units = {xm['exposure_ratio']:.2f}x a normal cycle."
          f"  See data/_par_review/christmas_2026.md")


if __name__ == "__main__":
    main()
