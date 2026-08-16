# HANDOFF — re-export the six bad venue-days

**One job. Nothing else.** Ignore HANDOFF_AUDIT.md for this session; it covers
unrelated questions and will only distract.

Six venue-days carry numbers that did not come from that day's trade. Each needs
a genuine export from Lightspeed, dropped into `data/`, and the day re-run.

---

## The list

| # | Venue | Date | What is wrong | Evidence |
|---|---|---|---|---|
| 1 | Harry Gatos | **3 Aug 2026** | export is byte-identical to 10 Aug | same 50 rows, "Unlimited Dumplings, 29, $684.00" on both |
| 2 | Harry Gatos | **10 Aug 2026** | same duplicated pair | ditto |
| 3 | Marilyna's | **3 Aug 2026** | export byte-identical to 10 Aug | same file, 2 rows |
| 4 | Marilyna's | **10 Aug 2026** | same duplicated pair | ditto |
| 5 | Marilyna's | **11 Aug 2026** | export byte-identical to 13 Aug | the known 11→13 backfill incident |
| 6 | Marilyna's | **13 Aug 2026** | same duplicated pair | ditto |

Plus two loose ends:

- **Marilyna's 13 Jul 2026** — closed day carrying $13.10 from a re-sent file.
  Small, but it is revenue that was never earned.
- **Stowaway 11 Aug 2026** — SETTLE THIS FIRST, see below. It may need nothing.

Confirm the list yourself before acting:

    cd <clone>
    python3 - <<'PY'
    import hashlib, glob, collections
    from pathlib import Path
    for pfx in ("stow","hg","mari"):
        by = collections.defaultdict(list)
        for f in sorted(glob.glob(f"data/insights_{pfx}_*.csv")):
            if sum(1 for _ in open(f)) > 1:          # skip closed-day files
                by[hashlib.sha256(Path(f).read_bytes()).hexdigest()].append(f)
        for h, v in by.items():
            if len(v) > 1: print(pfx, [Path(x).name for x in v])
    PY

---

## FIRST: settle Stowaway 11 Aug (5 minutes, may remove it from the list)

The product export says **$3,807.34**; the hourly export for the same day says
**$9,438.32** inc. Zak's reaction: *"Stowaway's till isn't $9.4k on a Tuesday."*

The hourly file is genuinely dated and genuinely Stowaway — 83 rows, every row
stamped `Sale Closed Date = 2026-08-11`, `Site Name = Stowaway Bar`. So one of
the two reports is measuring something the other is not.

**Get a third number:** Lightspeed Insights → Reports → **Snapshot** →
Site Name = Stowaway Bar → Time period = 11 Aug 2026 → read **Total Revenue**.

- matches ~$9,438 → the day IS understated; add Stowaway 11 Aug to the re-export list
- matches ~$3,807 → the hourly report counts something wider; **tell Zak**, and
  the reconciliation check in `export_guards.py` needs re-basing or removing

Do not skip this. The reconciliation currently only warns, precisely because
this is unresolved.

---

## How to get a genuine export

**Harry Gatos and Stowaway are separate Lightspeed companies.** Zak's login
lands on one; switch via the company launcher at `my.kounta.com` (click
"Back Office ⌄", then pick the company).

Then: **insights.kounta.com → Reports → Product sales**

- **Sale Closed Date** → Custom → the single day (from and to the same date)
- **Site Name** → the venue
- export the product-level table (the tile menu → download → CSV)

The emailed report this pipeline expects has these columns:

    Product Name, Product Quantity, $ Sales, Total Tax, Cost,
    % of Quantity, % of Sale Amount, Gross Profit %

A second shape also arrives sometimes and is handled automatically
(`Position, Product Number, Product, Quantity, ..., Sale Amount, ...`) — either
is fine, do not reshape it by hand.

### Marilyna's is the awkward one

Mari does not appear under **Site Name** — the filter lists only *Harry Gatos*
and *Stowaway Bar*, because Mari has no till of its own and rings through
Stowaway. So Mari's export is a filtered report, almost certainly by
**Reporting Group**. Work out which filter produces `insights_mari_*.csv` before
re-exporting: open the existing file, look at the product names, and match them
to a Reporting Group in the Product sales dashboard.

    head -5 data/insights_mari_2026-08-12.csv     # a known-good Mari export

If you cannot reproduce it confidently, stop and ask Zak rather than guessing —
a wrong filter produces a plausible file, which is worse than no file.

---

## Installing a corrected export

1. Save as `data/insights_<prefix>_<YYYY-MM-DD>.csv`
   (`stow`, `hg`, `mari`).
2. Sanity-check before committing:

       python3 - <<'PY'
       import sys; sys.path.insert(0, "scripts")
       from pathlib import Path
       from export_guards import assert_not_a_copy, read_rows, product_total_inc
       p = Path("data/insights_hg_2026-08-03.csv")
       rows, fields = read_rows(p)
       print(len(rows), "rows;", fields[:4])
       print("total inc: $%.2f" % product_total_inc(p))
       assert_not_a_copy(p, "hg")          # raises if it is STILL a duplicate
       print("not a copy — good")
       PY

3. Commit the file, push, then re-run the day:

       gh api -X POST repos/stowaway-hospitality/mari-daily-reporting/actions/workflows/daily_pull.yml/dispatches \
         -f ref=main -f "inputs[target_date]=2026-08-03" -f "inputs[venue]=harry"

   Venue keys are `stowaway`, `harry`, `marilynas`.

**The guard will refuse to recompute these days while the file is still a
duplicate.** That is intended. Replace the file first; the refusal is your
confirmation that you have not yet.

---

## Verifying a fix

For each day you correct:

    python3 -c "
    import csv
    for r in csv.DictReader(open('data/hg_daily_history.csv')):
        if r['date']=='2026-08-03': print(r['date'], r['revenue_ex_gst'], r.get('cogs_pct'))"

Then check it against what Lightspeed says for that day (Snapshot, same date,
same site). They should agree within a few percent.

Known-good reference points, already verified against Lightspeed:

- HG 2 Aug 2026 = **$2,400 inc** — a real function, one Open Price line, order
  type "Unspecified". **Do not "correct" this day.**
- HG 9 Aug 2026 = **$0** — closed. Already fixed; it used to carry $2,196.82.
- HG is shut **Tuesdays** and most **Sundays**. Blank days there are usually right.

---

## Rules for this session

1. **Investigate before changing.** Confirm each duplicate yourself with the
   script above.
2. **Reproduce numbers with the repo's own code**, not ad-hoc sums. Two false
   alarms this week came from throwaway scripts that the pipeline contradicted.
3. **Ask Zak before concluding anything about what a venue did on a night.** He
   corrected two wrong conclusions in one afternoon.
4. Claim the area first:
   `python3 scripts/session.py start sales-pipeline --who "re-export 6 bad days"`
5. Do not touch anything else in the pipeline while doing this.
