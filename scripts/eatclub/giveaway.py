"""EatClub give-away — the money EatClub keeps that the POS never sees.

EatClub tables ring the FULL bill on the POS at full price. EatClub then keeps
the offer discount + an 11% commission (10% ex-GST + GST) and settles the NET.
So Lightspeed/Insights revenue is OVERSTATED by (bill - net) per redeemed table,
which overstates reported margin.

This turns a day's EatClub redemptions into the single scalar the daily
aggregator needs to correct that: `giveaway_inc = sum(bill_full - net_revenue)`
over REDEEMED tables. Written as `data/eatclub_{prefix}_{date}.json`; the aggregator
subtracts it from revenue (see daily_aggregator.py, "EatClub give-away").

A redemption is `PAID` on the dine-in stores (Stowaway, Harry Gatos) but
`COMPLETED` on Marilyna's, because the takeaway flow settles differently in the
EatClub portal. Both mean the diner paid and EatClub kept its cut, so both count.
Filtering on `PAID` alone silently dropped every Marilyna's takeaway redemption
and left Mari's reported margin overstated (found 2026-08-09).

Money is float here to match daily_aggregator.py (that file has no Decimal).
"""

from __future__ import annotations

import csv
import json
import os

VENUE_PREFIX = {"stowaway": "stow", "harry": "hg", "marilynas": "mari"}

# A redemption the diner actually paid for. Dine-in settles as PAID; Marilyna's
# takeaway settles as COMPLETED.
REDEEMED_STATUSES = frozenset({"PAID", "COMPLETED"})

# Rows that correctly contribute nothing: the offer was never taken up, or the
# money has not settled yet. Blank is what the portal renders for an unclaimed
# offer, so a blank cell is a real value here, not missing data.
UNSETTLED_STATUSES = frozenset({"UNREDEEMED", "PENDING", ""})

KNOWN_STATUSES = REDEEMED_STATUSES | UNSETTLED_STATUSES


class UnknownEatClubStatus(ValueError):
    """A status EatClub has never sent us before.

    This exists because of the COMPLETED bug (fixed 2026-08-09): the filter was
    `status != "PAID" -> skip`, so when Marilyna's takeaway flow started settling
    as COMPLETED, every Mari redemption was silently treated as unredeemed and
    $327.83 of give-away went unrecorded for the whole program. An unrecognised
    status is a fact about the portal we do not yet understand, and guessing
    which side of the line it falls on is how that money went missing quietly.
    So: stop, and make a human classify it into REDEEMED_STATUSES or
    UNSETTLED_STATUSES. Loud and rare beats silent and wrong.
    """


def _f(x):
    s = str(x if x is not None else "").replace("$", "").replace(",", "").strip()
    return float(s) if s else 0.0


def day_giveaway(rows, date, venue):
    """Reduce one day's EatClub rows to the give-away fact.

    rows: dicts with bill_full, net_revenue, party_size, offer_pct, status.
    Only redeemed rows with a bill count — PAID (dine-in) or COMPLETED
    (Marilyna's takeaway). UNREDEEMED offers cost nothing. Returns the dict
    written to data/eatclub_{prefix}_{date}.json.

    Raises UnknownEatClubStatus if the portal sends a status we have never
    classified, rather than defaulting it to "not redeemed".
    """
    covers = 0
    menu_inc = net_inc = discount_inc = 0.0
    paid = 0
    offers = unredeemed = 0
    for r in rows:
        offers += 1
        status = (r.get("status") or "").strip().upper()
        if status not in KNOWN_STATUSES:
            raise UnknownEatClubStatus(
                f"{venue} {date}: unrecognised EatClub status {status!r} "
                f"(bill {r.get('bill_full')!r}). Classify it in giveaway.py as "
                f"REDEEMED_STATUSES or UNSETTLED_STATUSES before this day can be "
                f"costed - refusing to guess and under-report the give-away."
            )
        if status not in REDEEMED_STATUSES:
            unredeemed += 1
            continue
        bill = _f(r.get("bill_full"))
        if bill <= 0:
            continue
        paid += 1
        covers += int(_f(r.get("party_size")) or 0)
        net = _f(r.get("net_revenue"))
        off = _f(r.get("offer_pct"))
        off = off / 100 if off > 1 else off
        menu_inc += bill
        net_inc += net
        discount_inc += bill * off
    giveaway_inc = menu_inc - net_inc
    return {
        "date": date,
        "venue": venue,
        "tables": paid,
        "covers": covers,
        # Offer take-up. Unredeemed costs nothing, but the RATE is a signal: a
        # spike means offers sat live on a night nobody claimed them (e.g. a
        # Monday offer left on at HG, which should never happen).
        "offers": offers,
        "unredeemed": unredeemed,
        "menu_inc": round(menu_inc, 2),
        "net_inc": round(net_inc, 2),
        "giveaway_inc": round(giveaway_inc, 2),        # the aggregator reads this
        "discount_inc": round(discount_inc, 2),         # offer discount portion
        "commission_inc": round(giveaway_inc - discount_inc, 2),  # ~11% commission
    }


class GiveawayReconcileError(AssertionError):
    """The facts written do not add up to the source CSV.

    The COMPLETED bug was invisible precisely because nothing ever checked the
    total. Every redeemed row in the transactions master must land in exactly
    one dated fact, so the sum of what we wrote has to equal the sum of what we
    read. If it does not, rows are being dropped somewhere and the margin
    correction is understated — the same failure, wearing a different hat.
    """


def reconcile(day_facts, rows):
    """Check the written facts account for every redeemed row in the source.

    day_facts: the fact dicts produced for this CSV. rows: the raw CSV rows.
    Compares both table counts and give-away dollars. Rounding is per-day, so
    the dollar tolerance scales with the number of days. Raises
    GiveawayReconcileError on a mismatch; returns the totals otherwise.
    """
    redeemed = [r for r in rows
                if (r.get("status") or "").strip().upper() in REDEEMED_STATUSES
                and _f(r.get("bill_full")) > 0]
    src_tables = len(redeemed)
    src_giveaway = sum(_f(r.get("bill_full")) - _f(r.get("net_revenue")) for r in redeemed)

    got_tables = sum(f["tables"] for f in day_facts)
    got_giveaway = sum(f["giveaway_inc"] for f in day_facts)

    tol = 0.01 * max(1, len(day_facts))
    if got_tables != src_tables or abs(got_giveaway - src_giveaway) > tol:
        raise GiveawayReconcileError(
            f"give-away facts do not reconcile to source: "
            f"wrote {got_tables} tables / ${got_giveaway:.2f}, "
            f"source has {src_tables} redeemed rows / ${src_giveaway:.2f} "
            f"(diff ${got_giveaway - src_giveaway:+.2f}). "
            f"Rows are being dropped - check for an unhandled status or a "
            f"redeemed row with no bill."
        )
    return {"tables": src_tables, "giveaway_inc": round(src_giveaway, 2)}


class VenueContaminationError(AssertionError):
    """A per-venue transactions file holds rows from more than one venue.

    The EatClub partner portal serves three stores behind one login and ALWAYS
    opens on Stowaway Bar; the store name appears only in the sidebar and page
    title. On 22-23 Jul 2026 a pull read rows without switching store first and
    wrote Stowaway tables into the Harry Gatos master. Until now the only defence
    was a warning in the runbook telling the operator to check the page title.

    This is the structural version of that check: a venue file may contain
    exactly one venue, so contamination fails the run at the first write instead
    of quietly poisoning a venue's history.
    """


def assert_single_venue(transactions_csv, rows):
    """Every row in a per-venue file must name the same venue."""
    venues = sorted({(r.get("venue") or "").strip() for r in rows if r.get("venue")})
    if len(venues) > 1:
        counts = {}
        for r in rows:
            v = (r.get("venue") or "").strip()
            counts[v] = counts.get(v, 0) + 1
        raise VenueContaminationError(
            f"{os.path.basename(transactions_csv)} contains {len(venues)} venues "
            f"({counts}) - it must hold exactly one. This is the wrong-store "
            f"signature: the EatClub portal opens on Stowaway Bar, so a pull that "
            f"skipped the store switch writes another venue's tables here. Confirm "
            f"the store in the page title, then repair the file before re-running."
        )
    return venues[0] if venues else None


def write_from_transactions(transactions_csv, data_dir):
    """Group a per-venue EatClub transactions CSV by date and write one
    data/eatclub_{prefix}_{date}.json per trading day. Returns the paths written.

    Reconciles the facts against the source before returning, so a silent drop
    fails the run instead of quietly understating the margin correction.
    """
    with open(transactions_csv, newline="") as f:
        rows = list(csv.DictReader(f))

    assert_single_venue(transactions_csv, rows)

    by_key = {}
    for r in rows:
        venue = (r.get("venue") or "").strip()
        prefix = _prefix_for(venue)
        by_key.setdefault((prefix, venue, r["date"]), []).append(r)

    written = []
    facts = []
    for (prefix, venue, date), day_rows in sorted(by_key.items()):
        fact = day_giveaway(day_rows, date, venue)
        facts.append(fact)
        if fact["giveaway_inc"] <= 0:
            continue
        out = os.path.join(data_dir, f"eatclub_{prefix}_{date}.json")
        with open(out, "w") as fh:
            json.dump(fact, fh, indent=2)
        written.append(out)

    reconcile(facts, rows)
    return written


def _prefix_for(venue):
    v = venue.lower()
    if "harry" in v:
        return "hg"
    if "marilyn" in v:
        return "mari"
    return "stow"


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        raise SystemExit("usage: giveaway.py <transactions.csv> <data_dir>")
    for p in write_from_transactions(sys.argv[1], sys.argv[2]):
        print("wrote", p)
