#!/usr/bin/env python3
"""Self-sustaining Average-Spend (SPH) feed — reads the daily "Daily Sales Auto"
emails that already arrive in the ingest Gmail and extracts the transaction
COUNT + total sales per venue, then upserts data/sph_daily.csv (the file the
dashboard's Average Spend card reads).

Why this exists: the counts are already in those emails (each ZIP has a
sales_by_staff "(line added)" total row with '# of Sales', and reporting_groups
per-group counts). No Snapshot schedule, no Custom Insights, no extra logins —
just extract what already lands.

Venue math (the Stow email is the whole Stowaway-Bar till, INCLUDING Marilyna's
reporting groups; the Mari email is the Marilyna's-only slice of that same till):
    HarryGatos   = HG email total
    Marilynas    = Mari email total   (pizza + drinks + uber, combined)
    Stowaway     = Stow email total  −  Mari email total   (bar only)

Dates: attributed exactly like the ingest poller (email Date in AEST, minus one
day = "Yesterday") so SPH rows line up with the daily history rows.

Read-only on the mailbox (BODY.PEEK). Idempotent: upserts by (Date, Venue), so
re-runs and the twice-daily emails just overwrite the same rows. Guests are not
in these product-mix emails, so guest columns stay blank for email-fed days
(per-transaction is the metric; the weekly pull still fills guests for HG).

Env: GMAIL_ADDRESS, GMAIL_APP_PASSWORD, SPH_FILE (default data/sph_daily.csv).
"""
import csv, email, imaplib, io, os, re, sys, zipfile
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone

GMAIL = os.environ["GMAIL_ADDRESS"].strip()
APP_PW = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "").strip()
SPH_FILE = os.environ.get("SPH_FILE", "data/sph_daily.csv")
SYD = timezone(timedelta(hours=10))
HEADER = ["Date", "Venue", "Sales", "Transactions", "SalesExGST", "Guests",
          "SPH_PerTxn", "SPH_PerGuest", "AvgGuestsPerTxn"]

# subject -> which venue email this is
VENUE_OF_SUBJECT = [
    (re.compile(r"\bstow\b", re.I),        "stow"),
    (re.compile(r"\b(hg|harry)\b", re.I),  "hg"),
    (re.compile(r"\bmari", re.I),          "mari"),
]

def venue_of(subject):
    for rx, v in VENUE_OF_SUBJECT:
        if rx.search(subject or ""):
            return v
    return None

def target_date(msg):
    try:
        dt = parsedate_to_datetime(msg.get("Date"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        dt = datetime.now(timezone.utc)
    return (dt.astimezone(SYD) - timedelta(days=1)).strftime("%Y-%m-%d")

def _num(x):
    if x is None:
        return 0.0
    s = re.sub(r"[^0-9.\-]", "", str(x))
    try:
        return float(s) if s not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0

def _rows(zf, member):
    with zf.open(member) as fh:
        text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
        return list(csv.DictReader(text))

def totals_from_zip(raw):
    """Return (transactions, sales_inc_gst) for a Daily Sales Auto ZIP.

    Primary source: sales_by_staff "(line added)" grand-total row (the row whose
    '% Sales Contribution' is 100%% — a distinct-transaction count, not the
    per-staff sum which double-counts). Falls back to sales_by_category /
    reporting_groups summed only if the staff total is unreadable."""
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = zf.namelist()
    def find(substr):
        for n in names:
            if substr in n.lower():
                return n
        return None
    staff = find("sales_by_staff")
    if staff:
        rows = _rows(zf, staff)
        # grand-total row = 100% contribution (robust to staff-name blanks)
        tot = None
        for r in rows:
            pct = (r.get("% Sales Contribution") or "").strip()
            if pct.replace("%", "").strip() in ("100", "100.0", "100.00"):
                tot = r
        if tot is None and rows:
            tot = rows[-1]                      # "(line added)" total is last
        if tot:
            txns = int(round(_num(tot.get("# of Sales"))))
            sales = _num(tot.get("$ Sales"))
            if txns > 0 and sales > 0:
                return txns, sales
    # fallback: category footer / summed reporting groups (less exact)
    cat = find("sales_by_category") or find("reporting_groups")
    if cat:
        rows = _rows(zf, cat)
        txns = int(round(sum(_num(r.get("# of Sales")) for r in rows)))
        sales = sum(_num(r.get("$ Sales")) for r in rows)
        if txns > 0 and sales > 0:
            return txns, sales
    return None

def totals_from_snapshot(raw):
    """Return (transactions, sales_inc_gst, guests) from the HG 'Snapshot' email
    (single-value tiles: atv.csv -> No. of Sales, total_revenue.csv -> revenue,
    atv__avg._guest_spend -> Total Guest Count). Guests is the metric that makes
    HG's spend-per-head meaningful; the daily product-mix email lacks it."""
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = zf.namelist()
    def find(sub):
        for n in names:
            if sub in n.lower():
                return n
        return None
    def cell(member, col_needle):
        m = find(member)
        if not m:
            return None
        rows = _rows(zf, m)
        if not rows:
            return None
        r = rows[0]
        for k in r:
            if col_needle in k.lower():
                return r[k]
        # fall back to a positional guess for these 1-row tiles
        return list(r.values())[-1] if r else None
    guests = int(round(_num(cell("guest_spend", "guest count"))))
    sales = _num(cell("total_revenue", "total revenue"))
    if sales <= 0:                              # fallback: last col of the daily_sales row (the Total)
        ds = find("daily_sales")
        if ds:
            for r in _rows(zf, ds):
                vals = [v for v in r.values() if v]
                if vals:
                    sales = _num(vals[-1])
    # transactions: atv.csv has [ATV $, No. of Sales] — take the count column
    # (the one whose header is NOT the average-transaction-value label).
    txns = 0
    atv = find("atv.csv")
    if atv:
        rows = _rows(zf, atv)
        if rows:
            for k, v in rows[0].items():
                kl = (k or "").lower()
                if "sales" in kl and not any(x in kl for x in ("average", "transaction", "value")):
                    txns = int(round(_num(v)))
    return txns, sales, guests

def attachment_zip(msg):
    for part in msg.walk():
        fn = (part.get_filename() or "").lower()
        if fn.endswith(".zip"):
            raw = part.get_payload(decode=True)
            if raw:
                return raw
    return None

def load_sph():
    rows = {}
    if os.path.exists(SPH_FILE):
        with open(SPH_FILE, newline="") as f:
            for r in csv.DictReader(f):
                rows[(r["Date"], r["Venue"])] = r
    return rows

def put(rows, date, venue, txns, sales, guests=0):
    if txns <= 0 or sales <= 0:
        return
    sps = sales / txns
    if not (2.0 <= sps <= 2000.0):          # sanity: a real avg spend is a few $ to a few hundred
        print(f"  REJECT {date} {venue}: implausible ${sps:.2f}/txn ({txns} txns, ${sales:.2f})")
        return
    g = int(guests) if guests and guests > 0 else 0
    per_guest = f"{sales/g:.2f}" if g else ""
    apt = f"{g/txns:.2f}" if g else ""
    rows[(date, venue)] = {
        "Date": date, "Venue": venue,
        "Sales": f"{sales:.2f}", "Transactions": str(txns),
        "SalesExGST": f"{sales/1.1:.2f}", "Guests": str(g) if g else "",
        "SPH_PerTxn": f"{sales/txns:.2f}", "SPH_PerGuest": per_guest, "AvgGuestsPerTxn": apt,
    }

def main():
    M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    M.login(GMAIL, APP_PW)
    M.select("INBOX")
    since_days = int(os.environ.get("SPH_SINCE_DAYS", "3"))
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%d-%b-%Y")
    typ, data = M.search(None, "SINCE", since)
    ids = data[0].split() if data and data[0] else []
    # collect per (date) -> {venue: (txns, sales)}
    by_day = {}
    snap_by_day = {}   # date -> (txns, sales, guests) from the HG Snapshot email
    for num in ids:
        typ, md = M.fetch(num, "(BODY.PEEK[])")
        if typ != "OK" or not md or not md[0]:
            continue
        msg = email.message_from_bytes(md[0][1])
        subj = msg.get("Subject", "")
        raw = attachment_zip(msg)
        if not raw:
            continue
        d = target_date(msg)
        low = subj.lower()
        if "snapshot" in low:                    # HG guest counts
            try:
                st, ss, sg = totals_from_snapshot(raw)
            except Exception as e:
                print(f"  snapshot parse error '{subj}': {e}"); continue
            snap_by_day[d] = (st, ss, sg)
            print(f"  {d} snapshot(HG): {st} txns, ${ss:.2f}, {sg} guests")
            continue
        if "daily sales auto" not in low:
            continue
        v = venue_of(subj)
        if not v:
            continue
        try:
            tot = totals_from_zip(raw)
        except Exception as e:
            print(f"  parse error '{subj}': {e}")
            continue
        if not tot:
            print(f"  no totals in '{subj}'")
            continue
        by_day.setdefault(d, {})[v] = tot
        print(f"  {d} {v}: {tot[0]} txns, ${tot[1]:.2f}")
    M.logout()

    sph = load_sph()
    # Dates the weekly backfill owns keep the split (Marilynas + Marilynas-Uber);
    # the email feed's COMBINED Marilynas would double-count there, so leave them.
    backfill_dates = {dt for (dt, v) in sph if v == "Marilynas-Uber"}
    changed = 0
    for d, ven in by_day.items():
        if d in backfill_dates:
            print(f"  skip {d} (weekly-backfill date)"); continue
        hg_guests = snap_by_day.get(d, (0, 0.0, 0))[2]
        if "hg" in ven:
            put(sph, d, "HarryGatos", ven["hg"][0], ven["hg"][1], hg_guests); changed += 1
        elif d in snap_by_day:                   # snapshot arrived but daily HG email hasn't
            st, ss, sg = snap_by_day[d]
            put(sph, d, "HarryGatos", st, ss, sg); changed += 1
        if "mari" in ven:
            put(sph, d, "Marilynas", *ven["mari"]); changed += 1
        # Stowaway (bar) = whole till (stow email) minus Marilyna's slice
        if "stow" in ven:
            st_txn, st_sales = ven["stow"]
            m_txn, m_sales = ven.get("mari", (0, 0.0))
            bar_txn, bar_sales = st_txn - m_txn, st_sales - m_sales
            put(sph, d, "Stowaway", bar_txn, bar_sales); changed += 1

    if not sph:
        print("no SPH rows — nothing written"); return
    with open(SPH_FILE, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for key in sorted(sph.keys()):
            row = sph[key]
            w.writerow({h: row.get(h, "") for h in HEADER})
    print(f"upserted {changed} venue-day rows into {SPH_FILE} ({len(sph)} total rows)")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", type(e).__name__, str(e)[:300]); sys.exit(1)
