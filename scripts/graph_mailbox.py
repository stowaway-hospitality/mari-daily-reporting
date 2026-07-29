#!/usr/bin/env python3
"""App-only Microsoft Graph mailbox reader.

The M365-native replacement for the Gmail/IMAP sales-email ingestion. That Gmail
workaround only existed because app registration used to be admin-only (Symsafe);
now that the 'Stowaway Data Pipelines' app exists, we can read the mailbox
directly — no personal Gmail, no app password, no single point of failure.

Exposes the same four things the IMAP path used per message: subject, a stable
message-id (for the dedup ledger), the received datetime, and the first CSV/zip
attachment as base64 — so ingest_insights_email.py / sph_from_email.py can swap
the source with almost no other change.

Env: GRAPH_TENANT_ID / GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET, or the local file
~/Documents/STOW/.graph_app_secret.json (same creds functions-autodraft uses).

    python3 scripts/graph_mailbox.py accounts@stowawaybar.com   # read-only self-test
"""
import json, os, sys, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone

APP_SECRET_FILE = os.path.expanduser("~/Documents/STOW/.graph_app_secret.json")
GRAPH = "https://graph.microsoft.com/v1.0"


def _app_creds():
    t = os.environ.get("GRAPH_TENANT_ID")
    c = os.environ.get("GRAPH_CLIENT_ID")
    s = os.environ.get("GRAPH_CLIENT_SECRET")
    if not (t and c and s) and os.path.exists(APP_SECRET_FILE):
        d = json.load(open(APP_SECRET_FILE))
        t = t or d.get("tenant_id"); c = c or d.get("client_id"); s = s or d.get("client_secret")
    return (t, c, s) if (t and c and s) else None


def available() -> bool:
    return _app_creds() is not None


def _token() -> str:
    import msal
    t, c, s = _app_creds()
    app = msal.ConfidentialClientApplication(
        c, authority=f"https://login.microsoftonline.com/{t}", client_credential=s)
    r = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in r:
        raise RuntimeError(f"app-only auth failed: {r.get('error_description') or r}")
    return r["access_token"]


def _get(tok, url):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
    return json.loads(urllib.request.urlopen(req).read())


class Msg:
    def __init__(self, tok, mbox, m):
        self._tok, self._mbox, self.id = tok, mbox, m.get("id")
        self.subject = m.get("subject", "") or ""
        self.message_id = m.get("internetMessageId") or m.get("id")
        self.received = m.get("receivedDateTime")   # ISO-8601 Z
        self.has_attachments = bool(m.get("hasAttachments"))

    def attachment_b64(self, exts=(".csv", ".zip")):
        """First CSV/zip file attachment as base64 (Graph gives contentBytes b64)."""
        if not self.has_attachments:
            return None
        u = f"{GRAPH}/users/{urllib.parse.quote(self._mbox)}/messages/{self.id}/attachments"
        for a in _get(self._tok, u).get("value", []):
            name = (a.get("name") or "").lower()
            if a.get("@odata.type", "").endswith("fileAttachment") and name.endswith(exts):
                return a.get("contentBytes")
        return None

    def body_html(self):
        u = f"{GRAPH}/users/{urllib.parse.quote(self._mbox)}/messages/{self.id}?$select=body"
        return _get(self._tok, u).get("body", {}).get("content", "")


def messages(mailbox, since_days=2):
    """Inbox messages received in the last `since_days`, oldest first."""
    tok = _token()
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (f"{GRAPH}/users/{urllib.parse.quote(mailbox)}/mailFolders/inbox/messages"
           f"?$filter=receivedDateTime ge {since}&$orderby=receivedDateTime asc&$top=50"
           f"&$select=id,subject,internetMessageId,receivedDateTime,hasAttachments").replace(" ", "%20")
    out = []
    while url:
        d = _get(tok, url)
        out += [Msg(tok, mailbox, m) for m in d.get("value", [])]
        url = d.get("@odata.nextLink")
    return out


if __name__ == "__main__":
    mbox = sys.argv[1] if len(sys.argv) > 1 else "accounts@stowawaybar.com"
    if not available():
        print("no app creds — set GRAPH_* env or the local secret file"); sys.exit(1)
    ms = messages(mbox, since_days=7)
    print(f"Read {len(ms)} message(s) from {mbox} inbox (last 7 days):")
    for m in ms[:8]:
        print(f"  [{m.received}] {m.subject[:60]!r}  attach={m.has_attachments}")
    print("OK — app-only mailbox read works.")
