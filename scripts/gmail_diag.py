#!/usr/bin/env python3
import email, imaplib, os
from datetime import datetime, timedelta, timezone
G=os.environ["GMAIL_ADDRESS"].strip(); P=os.environ["GMAIL_APP_PASSWORD"].replace(" ","").strip()
M=imaplib.IMAP4_SSL("imap.gmail.com",993); M.login(G,P); M.select("INBOX")
since=(datetime.now(timezone.utc)-timedelta(days=5)).strftime("%d-%b-%Y")
t,d=M.search(None,"SINCE",since); ids=d[0].split() if d and d[0] else []
print(f"{len(ids)} messages since {since}:")
for n in ids:
    t,md=M.fetch(n,"(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
    if t!="OK" or not md or not md[0]: continue
    msg=email.message_from_bytes(md[0][1])
    print(f"  [{msg.get('Date','')[:16]}] {msg.get('Subject','')}  <-- {msg.get('From','')[:30]}")
M.logout()
