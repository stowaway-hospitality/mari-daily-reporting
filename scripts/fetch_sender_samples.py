#!/usr/bin/env python3
"""Download recent PDF attachments from a given sender domain (accounts@ mailbox)
into data/invoice_corpus/<folder>/ so a parser can be built against real samples.

    python3 scripts/fetch_sender_samples.py <sender-domain> <folder> [max]
"""
import sys, os, urllib.request, urllib.parse, json, base64
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.invoices.graph_auth import get_token

MB = "accounts@stowawaybar.com"
dom = sys.argv[1]
folder = sys.argv[2]
maxn = int(sys.argv[3]) if len(sys.argv) > 3 else 3
out = os.path.join("data/invoice_corpus", folder)
os.makedirs(out, exist_ok=True)
tok = get_token()
base = f"https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(MB)}/messages"
# server-side $search on sender — fast, no full-inbox paging
q = urllib.parse.urlencode({"$select": "id,from,subject,hasAttachments", "$top": "25",
                            "$search": f'"from:{dom}"'})
url = base + "?" + q
got = 0
while url and got < maxn:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}",
                                               "ConsistencyLevel": "eventual"})
    d = json.load(urllib.request.urlopen(req))
    for m in d.get("value", []):
        if not m.get("hasAttachments"):
            continue
        addr = (m.get("from", {}).get("emailAddress", {}) or {}).get("address", "").lower()
        if dom not in addr:
            continue
        aurl = f"{base}/{m['id']}/attachments"
        ar = urllib.request.Request(aurl, headers={"Authorization": f"Bearer {tok}"})
        atts = json.load(urllib.request.urlopen(ar)).get("value", [])
        for a in atts:
            if (a.get("name", "").lower().endswith(".pdf")) and a.get("contentBytes"):
                fn = os.path.join(out, f"{got:02d}_{a['name'][:40]}")
                open(fn, "wb").write(base64.b64decode(a["contentBytes"]))
                print("saved", fn)
                got += 1
                break
        if got >= maxn:
            break
    url = d.get("@odata.nextLink")
print(f"done: {got} PDFs from {dom} -> {out}")
