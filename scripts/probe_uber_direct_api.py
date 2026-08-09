#!/usr/bin/env python3
"""ONE-OFF PROBE — does the Uber Direct API actually carry the fee we bill against?

Uber Direct is the half of the Uber pull that could plausibly leave the browser:
it has a self-serve OAuth2 client_credentials API, unlike Uber Eats, whose
Reporting API needs an NDA and partner-manager approval.

But the docs only demonstrate a `fee` on the QUOTE. Our feed needs the fee that
was actually CHARGED per completed delivery, including the "Return completed"
case (a returned order Uber still bills for — one such delivery on 2026-07-24 at
A$23.86 is already in the CSV). Building an ingest against a quoted fee would
reproduce the exact class of bug this feed just spent a week escaping: a modelled
number that looks right and is quietly wrong in the flattering direction.

So: probe first, build second. This prints the SHAPE of the response, never a
secret and never a token. Delete it once the question is answered.

    UBER_DIRECT_CLIENT_ID / _CLIENT_SECRET / _CUSTOMER_ID   (repo secrets)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

AUTH = "https://auth.uber.com/oauth/v2/token"
API = "https://api.uber.com/v1/customers/{cid}/deliveries"
MONEYISH = ("fee", "amount", "price", "total", "charge", "cost", "currency")


def _post(url, form):
    body = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _walk(obj, prefix=""):
    """Field paths + types, with values only for money-ish and status fields."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += _walk(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(obj, list):
        out += _walk(obj[0], prefix + "[0]") if obj else [f"{prefix}[] (empty)"]
    else:
        leaf = prefix.split(".")[-1].lower()
        show = any(m in leaf for m in MONEYISH) or leaf in ("status", "kind", "state")
        out.append(f"{prefix}: {type(obj).__name__}" + (f" = {obj!r}" if show else ""))
    return out


def main() -> int:
    cid = os.environ.get("UBER_DIRECT_CUSTOMER_ID", "")
    cs = os.environ.get("UBER_DIRECT_CLIENT_SECRET", "")
    ci = os.environ.get("UBER_DIRECT_CLIENT_ID", "")
    if not (cid and cs and ci):
        print("SKIP: UBER_DIRECT_* secrets are not set. Nothing probed, nothing failed.")
        return 0

    try:
        tok = _post(AUTH, {"client_id": ci, "client_secret": cs,
                           "grant_type": "client_credentials", "scope": "eats.deliveries"})
    except Exception as e:
        print(f"AUTH FAILED: {e}")
        print("If this is invalid_scope, the credentials may be Org-scoped "
              "(scope 'direct.organizations') rather than Direct-scoped.")
        return 1
    token = tok.get("access_token", "")
    print(f"auth ok — token_type={tok.get('token_type')} expires_in={tok.get('expires_in')}s "
          f"scope={tok.get('scope')} (token itself not printed)")

    url = API.format(cid=urllib.parse.quote(cid)) + "?limit=10"
    try:
        data = _get(url, token)
    except Exception as e:
        print(f"LIST FAILED: {e}")
        return 1

    rows = data.get("data") if isinstance(data, dict) else data
    rows = rows if isinstance(rows, list) else [data]
    print(f"\nlist-deliveries returned {len(rows)} row(s). Top-level keys: "
          f"{sorted(data.keys()) if isinstance(data, dict) else type(data).__name__}")

    if not rows:
        print("No deliveries in the default window — widen the filter and re-run.")
        return 0

    print("\n--- SHAPE OF ONE DELIVERY (money + status values shown, rest typed only) ---")
    for line in _walk(rows[0]):
        print("  " + line)

    print("\n--- THE THREE QUESTIONS THIS PROBE EXISTS TO ANSWER ---")
    flat = {p.split(":")[0] for p in _walk(rows[0])}
    fee_fields = sorted(f for f in flat if any(m in f.lower() for m in MONEYISH))
    print(f"  1. money-ish fields present : {fee_fields or 'NONE — the API may not expose the billed fee'}")
    print(f"  2. cents or dollars?        : inspect the values above against the portal figure for the same order")
    print(f"  3. statuses seen            : {sorted({str(r.get('status')) for r in rows})}")
    print("\nCompare a known day against data/uber_direct_daily.csv before ANY ingest is written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
