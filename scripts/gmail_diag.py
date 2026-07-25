#!/usr/bin/env python3
"""One-off diagnostic: list recent emails in the ingest Gmail + unzip any CSV
attachment, so we can confirm the Lightspeed 'Snapshot' schedule is arriving and
see its exact format. Read-only (BODY.PEEK). No dispatch. Delete after use."""
import email, imaplib, io, os, zipfile
from datetime import datetime, timedelta, timezone

GMAIL = os.environ["GMAIL_ADDRESS"].strip()
APP_PW = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "").strip()

M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
M.login(GMAIL, APP_PW)
M.select("INBOX")
print("Logged into:", GMAIL)
since = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%d-%b-%Y")
typ, data = M.search(None, "SINCE", since)
ids = data[0].split() if data and data[0] else []
print(f"{len(ids)} message(s) since {since}\n" + "="*70)
for num in ids:
    typ, md = M.fetch(num, "(BODY.PEEK[])")
    if typ != "OK" or not md or not md[0]:
        continue
    msg = email.message_from_bytes(md[0][1])
    frm = msg.get("From", "")
    subj = msg.get("Subject", "")
    dt = msg.get("Date", "")
    atts = []
    for part in msg.walk():
        fn = part.get_filename()
        if fn:
            atts.append(fn)
    print(f"\nFROM: {frm}\nSUBJ: {subj}\nDATE: {dt}\nATTACH: {atts}")
    # if it's the Snapshot (or any zip/csv), dump structure
    is_snap = ("snapshot" in subj.lower()) or any(a.lower().endswith((".zip",".csv")) for a in atts)
    if not is_snap:
        continue
    for part in msg.walk():
        fn = part.get_filename() or ""
        raw = part.get_payload(decode=True)
        if not raw:
            continue
        if fn.lower().endswith(".zip"):
            try:
                z = zipfile.ZipFile(io.BytesIO(raw))
                print(f"  ZIP {fn} contains: {z.namelist()}")
                for name in z.namelist():
                    body = z.read(name).decode("utf-8","replace")
                    lines = body.splitlines()
                    print(f"  --- {name} ({len(lines)} lines) ---")
                    for ln in lines[:6]:
                        print("    " + ln[:160])
            except Exception as e:
                print("  zip read error:", e)
        elif fn.lower().endswith(".csv"):
            body = raw.decode("utf-8","replace")
            lines = body.splitlines()
            print(f"  CSV {fn} ({len(lines)} lines):")
            for ln in lines[:6]:
                print("    " + ln[:160])
M.logout()
print("\ndone")
