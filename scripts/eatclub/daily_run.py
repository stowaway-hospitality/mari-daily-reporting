"""The morning EatClub read, as one reusable runner.

Why this exists
---------------
The daily analysis used to be a hand-written throwaway script per night — 48 of
them accumulated in the Daily Sales EatClub folder. Each one re-typed that
night's window total, the EatClub bills and the eight pre-launch baseline values
as Python literals, and re-implemented the cannibalisation verdict from memory.
Every run was a fresh chance to transcribe a number wrong into a file that then
became the record.

Everything those scripts did by hand already exists here, tested:
`metrics.assess_dinein` (including the rescue-tier reverse-causality rule),
`metrics.assess_takeaway`, and `baseline.dow_baseline`. This runner is the wiring
— it reads the immutable inputs, calls those functions, and prints the read. No
figures are baked in.

Usage
-----
    python3 daily_run.py --date 2026-08-08

Inputs (all immutable facts, none of them typed by hand):
  --hourly    HG hourly window series, date,h17..h22   (refreshed from Lightspeed
              salesummarybyhour; see refresh_hg_hourly.py)
  --ec-dir    the EatClub folder holding the three transactions CSVs
  --history   data/mari_daily_history.csv, for Marilyna's delivery baseline
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import baseline as bl          # noqa: E402
import config as cfg           # noqa: E402
import giveaway as gv          # noqa: E402
import metrics as mx           # noqa: E402

WEEKDAYS = bl.WEEKDAYS

DEFAULT_EC_DIR = "/Users/zak/Documents/STOW/Daily Sales/EatClub"
TX_FILES = {
    "harry": "eatclub_transactions_master.csv",
    "stowaway": "stow_eatclub_transactions.csv",
    "marilynas": "mari_eatclub_transactions.csv",
}

# The published offer ladder. A night above these tiers means the team lifted the
# discount to rescue a dying service — which inverts the causality of a weak
# window (see metrics.assess_dinein / the RESCUE verdict).
STANDARD_TIERS = {25, 20}


def _d(s):
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def load_tx(ec_dir, venue_key):
    """Read one venue's transactions, with the same guards the writer uses."""
    path = os.path.join(ec_dir, TX_FILES[venue_key])
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    gv.assert_single_venue(path, rows)      # wrong-store contamination
    for r in rows:                          # unknown-status drift
        s = (r.get("status") or "").strip().upper()
        if s not in gv.KNOWN_STATUSES:
            raise gv.UnknownEatClubStatus(f"{path}: unrecognised status {s!r}")
    return rows


def redeemed_on(rows, day):
    d = day.isoformat()
    return [r for r in rows
            if r["date"] == d
            and (r.get("status") or "").strip().upper() in gv.REDEEMED_STATUSES
            and gv._f(r.get("bill_full")) > 0]


def load_hourly(path):
    """date,h17..h22 -> list of dicts with a computed 'window' and 'early'.

    Days the venue did not trade (window == 0) are dropped. A closed day is not
    a weak day, and averaging zeros into a baseline silently deflates it.
    """
    out = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            h = {k: float(r.get(k) or 0) for k in ("h17", "h18", "h19", "h20", "h21", "h22")}
            window = h["h17"] + h["h18"] + h["h19"] + h["h20"]
            if window <= 0:
                continue
            out.append({"date": r["date"], "window": window,
                        "early": h["h17"] + h["h18"], "h21": h["h21"], **h})
    return out


def hg_read(day, hourly_rows, tx_rows):
    """Harry Gatos dine-in cannibalisation read for one night."""
    conf = cfg.VENUE_EATCLUB["harry"]
    start, end = conf["baseline_window"]

    night = next((r for r in hourly_rows if r["date"] == day.isoformat()), None)
    if night is None:
        return {"skip": f"no POS hourly row for {day} — venue closed, or the "
                        f"hourly series is stale (refresh it before trusting this)"}

    pre = [r for r in hourly_rows if _d(r["date"]) <= _d(end)]
    base_window = bl.dow_baseline(pre, "window", start, end)
    base_early = bl.dow_baseline(pre, "early", start, end)
    dow = WEEKDAYS[day.weekday()]
    if dow not in base_window:
        return {"skip": f"no pre-launch {dow} baseline in {start}..{end}"}

    red = redeemed_on(tx_rows, day)
    bills = sum(gv._f(r["bill_full"]) for r in red)
    tiers = {int(gv._f(r.get("offer_pct"))) for r in red}

    # The two reverse-causality inputs, both DERIVED, never asserted by hand.
    tier_standard = not (tiers - STANDARD_TIERS)
    early_weak = (dow in base_early) and (night["early"] < float(base_early[dow]))

    read = mx.assess_dinein(
        window_incgst=night["window"],
        eatclub_bills_incgst=bills,
        baseline_incgst=base_window[dow],
        offer_tier_standard=tier_standard,
        early_window_weak=early_weak,
    )
    return {
        "read": read, "dow": dow, "tables": len(red),
        "covers": sum(int(gv._f(r.get("party_size")) or 0) for r in red),
        "tiers": sorted(tiers), "tier_standard": tier_standard,
        "early": night["early"], "early_baseline": base_early.get(dow),
        "early_weak": early_weak, "h21": night["h21"],
        "baseline_n": sum(1 for r in pre
                          if _d(r["date"]).weekday() == day.weekday()
                          and _d(start) <= _d(r["date"]) <= _d(end)),
    }


def mari_read(day, tx_rows, history_csv):
    """Marilyna's takeaway substitution read. Ex-GST on both sides.

    Compares DEMAND, not revenue: EatClub menu value (what the customer ordered)
    against delivery dollars, because the question assess_takeaway answers is
    whether off-premise volume grew or merely moved from Uber to EatClub.
    """
    conf = cfg.VENUE_EATCLUB["marilynas"]
    if not conf.get("launch_date"):
        return {"skip": "no launch_date set for Marilyna's"}
    start, end = conf["baseline_window"]

    base = bl.mari_delivery_baseline(history_csv, start, end)
    dow = WEEKDAYS[day.weekday()]
    if dow not in base:
        return {"skip": f"no pre-launch {dow} delivery baseline in {start}..{end}"}

    with open(history_csv, newline="") as f:
        hist = {r["date"]: r for r in csv.DictReader(f)}
    row = hist.get(day.isoformat())
    if row is None:
        return {"skip": f"no mari_daily_history row for {day}"}
    delivery_ex = gv._f(row.get("delivery_dollars"))

    red = redeemed_on(tx_rows, day)
    ec_menu_ex = sum(gv._f(r["bill_full"]) for r in red) / 1.1

    read = mx.assess_takeaway(eatclub_incgst=ec_menu_ex,
                              delivery_incgst=delivery_ex,
                              delivery_baseline=base[dow])
    return {"read": read, "dow": dow, "tables": len(red),
            "covers": sum(int(gv._f(r.get("party_size")) or 0) for r in red)}


def stowaway_read(day, tx_rows):
    """Stowaway is deliberately NOT assessed here.

    Its dinner window needs the Custom Insights 'Stow Hourly RG Auto' feed, which
    does not exist yet. salesummarybyhour on site 150764 returns Stowaway AND
    Marilyna's combined, so using it would silently mix a takeaway brand into a
    dine-in window baseline. Capture continues; the verdict waits for the feed.
    """
    red = redeemed_on(tx_rows, day)
    return {"skip": "needs the 'Stow Hourly RG Auto' RG feed (not built) — the "
                    "shared Stow till cannot separate Marilyna's by hour",
            "tables": len(red),
            "covers": sum(int(gv._f(r.get("party_size")) or 0) for r in red)}


def main(argv=None):
    p = argparse.ArgumentParser(description="EatClub daily read")
    p.add_argument("--date", required=True, help="night to assess, YYYY-MM-DD")
    p.add_argument("--ec-dir", default=DEFAULT_EC_DIR)
    p.add_argument("--hourly", default=None, help="HG hourly csv (date,h17..h22)")
    p.add_argument("--history", default=None, help="mari_daily_history.csv")
    a = p.parse_args(argv)

    day = _d(a.date)
    ec_dir = a.ec_dir
    hourly_path = a.hourly or os.path.join(ec_dir, "hg_hourly.csv")
    history = a.history or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "mari_daily_history.csv")

    print(f"EatClub read — {day:%a %d %b %Y}\n" + "=" * 46)

    # ---- Harry Gatos -------------------------------------------------------
    tx_hg = load_tx(ec_dir, "harry")
    if os.path.exists(hourly_path):
        hg = hg_read(day, load_hourly(hourly_path), tx_hg)
    else:
        hg = {"skip": f"hourly series missing at {hourly_path}"}
    print("\nHARRY GATOS (dine-in)")
    if "skip" in hg:
        print(f"  SKIPPED: {hg['skip']}")
    else:
        r = hg["read"]
        print(f"  {hg['tables']} tables / {hg['covers']} covers, tiers {hg['tiers']}"
              f" ({'standard' if hg['tier_standard'] else 'ESCALATED — rescue'})")
        print(f"  window ${r.window_incgst} - EatClub ${r.eatclub_bills_incgst}"
              f" = full-price ${r.full_price_window}")
        print(f"  vs {hg['dow']} baseline ${r.baseline_incgst} (n={hg['baseline_n']})"
              f" -> ${r.delta} / {r.delta_pct}%")
        print(f"  early 17-18h ${hg['early']:.2f} vs baseline "
              f"${hg['early_baseline']} -> {'WEAK before arrivals' if hg['early_weak'] else 'normal'}")
        print(f"  VERDICT: {r.verdict}")

    # ---- Marilyna's --------------------------------------------------------
    tx_mari = load_tx(ec_dir, "marilynas")
    mari = mari_read(day, tx_mari, history)
    print("\nMARILYNA'S (takeaway)")
    if "skip" in mari:
        print(f"  SKIPPED: {mari['skip']}")
    else:
        r = mari["read"]
        print(f"  {mari['tables']} orders / {mari['covers']} covers")
        print(f"  EatClub ${r.eatclub_incgst} + delivery ${r.delivery_incgst}"
              f" = ${r.total_offpremise} off-premise (ex-GST)")
        print(f"  vs {mari['dow']} delivery baseline ${r.delivery_baseline}"
              f" -> ${r.delta} / {r.delta_pct}%")
        print(f"  VERDICT: {r.verdict}")

    # ---- Stowaway ----------------------------------------------------------
    stow = stowaway_read(day, load_tx(ec_dir, "stowaway"))
    print("\nSTOWAWAY BAR (dine-in)")
    print(f"  {stow['tables']} tables / {stow['covers']} covers captured")
    print(f"  SKIPPED: {stow['skip']}")

    return {"harry": hg, "marilynas": mari, "stowaway": stow}


if __name__ == "__main__":
    main()
