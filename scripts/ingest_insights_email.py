#!/usr/bin/env python3
"""Free, always-on replacement for the Pipedream sales-email flow (Gmail/IMAP).

Microsoft 365 in this tenant blocks every self-serve API path (app registration
is admin-only; user consent disabled; Office client not preauthorised). So the
three Lightspeed "Daily Sales Auto" emails are routed to a Gmail and read over
IMAP with a Google app password - no admin, always-on, $0.

Fires the SAME repository_dispatch events the daily pull consumes
(stow-csv-arrived / hg-csv-arrived / insights-csv-arrived), so the whole
downstream pipeline is unchanged.

Dedupe is a committed message-id ledger (.ingest/processed.json), NOT the unread
flag - because this is a personal inbox a human also reads. We fetch with
BODY.PEEK so we never alter read/unread state. Re-runs are no-ops; a late email
is caught next run.

Env:
    GMAIL_ADDRESS        the Gmail the sales emails are routed to
    GMAIL_APP_PASSWORD   a Google App Password (needs 2-Step Verification)
    GH_DISPATCH_PAT      PAT with repo scope (fires repository_dispatch)
    GH_REPO              owner/repo (default stowaway-hospitality/mari-daily-reporting)
    STATE_FILE           ledger path (default .ingest/processed.json)
"""
import base64, email, imaplib, io, json, os, re, sys, urllib.request, zipfile
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone

GMAIL = os.environ.get("GMAIL_ADDRESS", "").strip()
APP_PW = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()   # Google shows it space-separated
# If set, read this M365 mailbox app-only (via Graph) instead of the Gmail/IMAP
# workaround — the whole reason for Gmail (admin-only app registration) is gone.
SALES_MAILBOX = os.environ.get("SALES_MAILBOX", "").strip()
PAT = os.environ["GH_DISPATCH_PAT"]
REPO = os.environ.get("GH_REPO", "stowaway-hospitality/mari-daily-reporting")
STATE_FILE = os.environ.get("STATE_FILE", ".ingest/processed.json")
SYD = timezone(timedelta(hours=10))   # AEST (fine for date-stamping)

SUBJECT_MAP = [
    # Hourly MUST come first: "Stow Hourly RG Auto" also contains "stow", and we
    # must NOT route the hour x reporting-group CSV into the DAILY pipeline (it
    # has a completely different shape). It fires the hourly pull instead.
    (re.compile(r"hourly", re.I),          ("stow-hourly-arrived",  "stowaway")),
    (re.compile(r"\bstow\b", re.I),        ("stow-csv-arrived",     "stowaway")),
    (re.compile(r"\b(hg|harry)\b", re.I),  ("hg-csv-arrived",       "harry")),
    (re.compile(r"\bmari", re.I),          ("insights-csv-arrived", "marilynas")),
]


def classify(subject):
    for rx, out in SUBJECT_MAP:
        if rx.search(subject or ""):
            return out
    return None


# Insights sometimes attaches the product export as a ZIP of the whole dashboard
# rather than a bare CSV. This used to base64 the ZIP unchanged, and the workflow
# wrote those bytes to data/insights_<date>.csv — a ZIP under a .csv name.
#
# data/insights_2026-07-11.csv is one, committed in c44c6cb. Nothing that reads it
# says "this is not a CSV"; csv.DictReader raises `_csv.Error: line contains NUL`
# from wherever it is first opened, which took out BOTH scripts/build_site.py and
# the recipe build, and left the Marilyna's coverage cross-check blind for the day.
#
# So unwrap here, at the only place that knows the attachment was ever a ZIP.
_PRODUCT_MEMBER = "sales_by_product"


def _csv_from_zip(raw, fn):
    """-> the product-sales CSV inside an Insights dashboard ZIP, or None."""
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        print(f"  '{fn}' claims to be a zip but will not open — skipped")
        return None
    members = [n for n in z.namelist() if n.lower().endswith(".csv")]
    want = [n for n in members if _PRODUCT_MEMBER in n.lower().replace(" ", "_")]
    if len(want) == 1:
        return z.read(want[0])
    if len(members) == 1:
        return z.read(members[0])
    # Never guess which sheet is the product export — a wrong pick would publish
    # reporting-group subtotals as products and look plausible.
    print(f"  '{fn}': cannot identify the product CSV among {members} — skipped")
    return None


def attachment_b64(msg):
    for part in msg.walk():
        fn = part.get_filename() or ""
        if not fn.lower().endswith((".zip", ".csv")):
            continue
        raw = part.get_payload(decode=True)
        if not raw:
            continue
        # Trust the BYTES, not the extension: the ZIP has arrived named .csv too.
        if raw[:4] == b"PK\x03\x04" or fn.lower().endswith(".zip"):
            raw = _csv_from_zip(raw, fn)
            if not raw:
                continue
        return base64.b64encode(raw).decode()
    return None


def target_date(msg):
    try:
        dt = parsedate_to_datetime(msg.get("Date"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        dt = datetime.now(timezone.utc)
    return (dt.astimezone(SYD) - timedelta(days=1)).strftime("%Y-%m-%d")   # report = "Yesterday"


def dispatch(event, venue, csv_b64, tdate):
    payload = {"event_type": event,
               "client_payload": {"venue": venue, "csv_base64": csv_b64,
                                  "target_date": tdate, "source": "gmail-imap-poller"}}
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/dispatches",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"token {PAT}", "Accept": "application/vnd.github+json"})
    urllib.request.urlopen(req, timeout=30)


def load_state():
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return {}


def save_state(state):
    cut = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    state = {k: v for k, v in state.items() if v >= cut}
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump(state, open(STATE_FILE, "w"), indent=0, sort_keys=True)


def _graph_tdate(received_iso):
    try:
        dt = datetime.strptime(received_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        dt = datetime.now(timezone.utc)
    return (dt.astimezone(SYD) - timedelta(days=1)).strftime("%Y-%m-%d")   # report = "Yesterday"


DAILY_EVENTS = {"stow-csv-arrived", "hg-csv-arrived", "insights-csv-arrived"}
_PREFIX = {"stowaway": "stow", "harry": "hg", "marilynas": "mari"}


def _output_complete(venue, tdate):
    """True once the day's sales actually landed — the self-heal's stop signal."""
    try:
        d = json.load(open(os.path.join("data", f"{_PREFIX.get(venue, venue)}_daily_{tdate}.json")))
        return d.get("data_status", {}).get("lightspeed") == "ok"
    except Exception:
        return False


def _recent(iso, hours):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)) < timedelta(hours=hours)
    except Exception:
        return False


def main():
    state = load_state()
    fired = scanned = 0
    imap_conn = None

    if SALES_MAILBOX:
        import graph_mailbox   # scripts/ is on sys.path when run directly
        records = [("graph", m) for m in graph_mailbox.messages(SALES_MAILBOX, since_days=8)]
        print(f"source: M365 mailbox {SALES_MAILBOX} (app-only)")
    else:
        imap_conn = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        imap_conn.login(GMAIL, APP_PW)
        imap_conn.select("INBOX")
        since = (datetime.now(timezone.utc) - timedelta(days=8)).strftime("%d-%b-%Y")
        typ, data = imap_conn.search(None, "SINCE", since)
        ids = data[0].split() if data and data[0] else []
        records = []
        for num in ids:
            # BODY.PEEK -> does NOT set \Seen, so the human's inbox is untouched
            typ, md = imap_conn.fetch(num, "(BODY.PEEK[])")
            if typ == "OK" and md and md[0]:
                records.append(("imap", email.message_from_bytes(md[0][1])))
        print("source: Gmail/IMAP")

    for kind, msg in records:
        scanned += 1
        if kind == "graph":
            subject, mid = msg.subject, msg.message_id
        else:
            subject, mid = msg.get("Subject", ""), (msg.get("Message-ID") or f"uid-{scanned}")
        cl = classify(subject)
        if not cl:
            continue
        event, venue = cl
        tdate = _graph_tdate(msg.received) if kind == "graph" else target_date(msg)
        seen = mid in state
        if seen:
            # Hourly / non-daily: ledger dedup only. Daily sales: SELF-HEAL — if the
            # day's data never landed (dropped ingest / transient failure) retry it
            # rather than trust the ledger, so a gap can't become permanent. Once
            # the pull writes the day, output is complete and we leave it alone; a
            # 2h backoff caps churn while it is still missing.
            if event not in DAILY_EVENTS or _output_complete(venue, tdate):
                continue
            if _recent(state.get(f"heal:{venue}:{tdate}", ""), 2):
                continue
        b64 = msg.attachment_b64() if kind == "graph" else attachment_b64(msg)
        if not b64:
            print(f"  skip '{subject}' - no csv/zip attachment")
            continue
        dispatch(event, venue, b64, tdate)
        now_iso = datetime.now(timezone.utc).isoformat()
        state[mid] = now_iso
        if seen:
            state[f"heal:{venue}:{tdate}"] = now_iso
        fired += 1
        print(f"  {'re-dispatched (self-heal)' if seen else 'dispatched'} {event} ({venue}) for {tdate} from '{subject}'")

    save_state(state)
    if imap_conn is not None:
        imap_conn.logout()
    print(f"done - {fired} Insights email(s) ingested, {scanned} candidate(s) scanned")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", type(e).__name__, str(e)[:300]); sys.exit(1)
