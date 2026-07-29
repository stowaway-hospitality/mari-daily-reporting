#!/usr/bin/env python3
"""Microsoft Graph auth for the mailbox pipelines.

Two modes, picked automatically:

* APP-ONLY (preferred) — if GRAPH_TENANT_ID / GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET
  are set, authenticate as the "Stowaway Data Pipelines" Entra app registration via
  client-credentials. No user, no device-code, no refresh token that expires and
  needs re-authing. Access each mailbox as /users/{address}. Lock the app down with
  an Exchange Application Access Policy so it can only touch the pipeline mailboxes.

* DELEGATED (fallback) — the original device-code public-client flow, kept verbatim
  so nothing breaks until every caller is moved to app-only.

    python3 modules/invoices/graph_auth.py            # smoke-test whichever mode is active
"""
import os
import msal

# --- app-only (preferred) ---
APP_TENANT = os.environ.get("GRAPH_TENANT_ID")
APP_CLIENT = os.environ.get("GRAPH_CLIENT_ID")
APP_SECRET = os.environ.get("GRAPH_CLIENT_SECRET")

# --- delegated fallback (original public client) ---
CLIENT_ID = "d3590ed6-52b3-4102-aeff-aad2292ab01c"        # Microsoft Office public client
AUTHORITY = "https://login.microsoftonline.com/common"
SCOPES = ["Mail.ReadWrite"]
CACHE = os.environ.get(
    "GRAPH_INVOICE_TOKEN_CACHE",
    os.path.expanduser("~/Documents/STOW/.graph_token_cache_invoices.json"),
)


def app_only_available() -> bool:
    return bool(APP_TENANT and APP_CLIENT and APP_SECRET)


def _app_only_token() -> str:
    app = msal.ConfidentialClientApplication(
        APP_CLIENT,
        authority=f"https://login.microsoftonline.com/{APP_TENANT}",
        client_credential=APP_SECRET,
    )
    r = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in r:
        raise RuntimeError(f"app-only auth failed: {r.get('error_description') or r}")
    return r["access_token"]


def get_token(interactive: bool = False) -> str:
    # Prefer app-only whenever the app credentials are present.
    if app_only_available():
        return _app_only_token()

    # ---- original device-code delegated flow (unchanged) ----
    cache = msal.SerializableTokenCache()
    if os.path.exists(CACHE):
        cache.deserialize(open(CACHE).read())
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=cache)

    accounts = app.get_accounts()
    if accounts:
        r = app.acquire_token_silent(SCOPES, account=accounts[0])
        if r and "access_token" in r:
            if cache.has_state_changed:
                open(CACHE, "w").write(cache.serialize())
            return r["access_token"]
    if not interactive:
        raise RuntimeError("No cached invoice-mailbox token — run: python3 modules/invoices/graph_auth.py")

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"device flow failed: {flow}")
    print("\n" + "=" * 60)
    print("AUTHORISE THE INVOICE MAILBOX — one time")
    print("=" * 60)
    print(f"\n1. Go to:  {flow['verification_uri']}")
    print(f"2. Enter:  {flow['user_code']}")
    print("\n   Sign in as accounts@stowawaybar.com (or a user with Full")
    print("   Access to it).")
    print("=" * 60 + "\n")
    r = app.acquire_token_by_device_flow(flow)
    if "access_token" not in r:
        raise RuntimeError(f"auth failed: {r.get('error_description')}")
    open(CACHE, "w").write(cache.serialize())
    print(f"\n\u2705 Authenticated. Token cached at {CACHE}")
    return r["access_token"]


if __name__ == "__main__":
    import json
    import urllib.request
    if app_only_available():
        tok = _app_only_token()
        print("Mode: APP-ONLY (client-credentials)")
        # read-only proof: app-only has no /me, so read a named mailbox folder
        for mbox in ("functions@stowawaybar.com", "accounts@stowawaybar.com"):
            url = f"https://graph.microsoft.com/v1.0/users/{mbox}/mailFolders/inbox?$select=displayName,totalItemCount,unreadItemCount"
            try:
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
                d = json.loads(urllib.request.urlopen(req).read())
                print(f"  \u2705 {mbox}: inbox '{d.get('displayName')}' — {d.get('totalItemCount')} items, {d.get('unreadItemCount')} unread")
            except Exception as e:
                print(f"  \u274c {mbox}: {e}")
    else:
        tok = get_token(interactive=True)
        req = urllib.request.Request("https://graph.microsoft.com/v1.0/me",
                                     headers={"Authorization": f"Bearer {tok}"})
        me = json.loads(urllib.request.urlopen(req).read())
        print(f"Signed in as: {me.get('displayName')} ({me.get('mail') or me.get('userPrincipalName')})")
