#!/usr/bin/env python3
"""One-off: dump the Snapshot email's CSV-zip structure (guest counts) so we can
wire HG spend-per-guest. Read-only. Delete after use."""
import email, imaplib, io, os, zipfile
from datetime import datetime, timedelta, timezone
GMAIL=os.environ["GMAIL_ADDRESS"].strip(); APP_PW=os.environ["GMAIL_APP_PASSWORD"].replace(" ","").strip()
M=imaplib.IMAP4_SSL("imap.gmail.com",993); M.login(GMAIL,APP_PW); M.select("INBOX")
since=(datetime.now(timezone.utc)-timedelta(days=3)).strftime("%d-%b-%Y")
typ,data=M.search(None,"SINCE",since); ids=data[0].split() if data and data[0] else []
for num in ids:
    typ,md=M.fetch(num,"(BODY.PEEK[])")
    if typ!="OK" or not md or not md[0]: continue
    msg=email.message_from_bytes(md[0][1]); subj=msg.get("Subject","")
    if "snapshot" not in subj.lower(): continue
    print("FROM:",msg.get("From","")[:50],"\nSUBJ:",subj,"\nDATE:",msg.get("Date",""))
    for part in msg.walk():
        fn=part.get_filename() or ""; raw=part.get_payload(decode=True)
        if fn.lower().endswith(".zip") and raw:
            z=zipfile.ZipFile(io.BytesIO(raw)); print(" ZIP:",z.namelist())
            for n in z.namelist():
                lines=z.read(n).decode("utf-8","replace").splitlines()
                print(f"  --- {n} ({len(lines)} lines) ---")
                for ln in lines[:8]: print("    "+ln[:150])
M.logout(); print("done")
