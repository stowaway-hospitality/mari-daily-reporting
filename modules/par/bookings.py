#!/usr/bin/env python3
"""
Par model v3 — bookings uplift. **SHADOW MODE ONLY.**

A par built off historical weekly demand cannot see the 60-pax Christmas party
that is already on the books for next Saturday. The bookings engine can. This
module turns booked covers into a per-SKU uplift so that, once it is trusted,
the par for the exposure window can carry a known event instead of discovering
it at 8pm.

It is deliberately NOT live. `BOOKINGS_LIVE = False`: the uplift is computed and
RECORDED on every SKU as `bookings_uplift_shadow`, and `rec_par` ignores it. Run
it in shadow for a few cycles, compare `bookings_uplift_shadow` against what the
week actually did, and only then flip the flag.

Auth reality (checked 2026-08-09)
---------------------------------
`dashboard/bookings/bookings.js` talks to `https://stowaway-bookings.onrender.com`
with `Authorization: Bearer <token>`, where the token comes from the Supabase
`app_config` table via `Auth.bookingToken()` (RLS: authenticated users only).
The admin endpoints the covers live behind —

    GET /api/admin/overview            upcoming events: {date, name, bookings, covers}
    GET /api/admin/day/<YYYY-MM-DD>    one day: {sittings: [{bookings: [{adults, kids, ...}]}]}

— return `401 {"detail":"bad admin token"}` without it, and the build box has no
way to obtain that token (it is not a repo secret and must not become one on the
strength of a par model). The public `GET /?date=YYYY-MM-DD` is the customer
booking PAGE (HTML), not a covers feed.

So: this module is written against the documented shape above, takes its token
from `STOWAWAY_BOOKINGS_TOKEN` in the environment if it is ever provided, and
**degrades to a zero uplift** otherwise — never to a guess, and never to a
build failure.

Money note: covers and units only; no currency arithmetic here.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, timedelta

BOOKINGS_LIVE = False          # <- the flag. Keep it False until shadow is proven.
API = "https://stowaway-bookings.onrender.com"
TOKEN_ENV = "STOWAWAY_BOOKINGS_TOKEN"
TIMEOUT = 12

# Covers a normal (unbooked) trading day already carries. Only covers ABOVE this
# baseline are new demand — a 20-pax booking on a Saturday that would have been
# full anyway is not +20 of anything.
BASELINE_COVERS = {0: 0, 1: 40, 2: 45, 3: 55, 4: 90, 5: 120, 6: 100}


def _token():
    return os.environ.get(TOKEN_ENV, "").strip()


def fetch_day(day, token=None, api=API):
    """GET /api/admin/day/<date> -> parsed JSON, or None if unreachable/unauth."""
    token = token if token is not None else _token()
    if not token:
        return None
    req = urllib.request.Request(
        f"{api}/api/admin/day/{day}",
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError):
        return None


def covers_from_day(doc) -> int:
    """Total booked covers in an /api/admin/day payload.

    Documented shape (bookings.js: `covers = b.adults + b.kids`):
        {"sittings": [{"bookings": [{"adults": 4, "kids": 1, ...}, ...]}, ...]}
    A flat {"bookings": [...]} is accepted too — the module must not fall over
    if the service grows a wrapper.
    """
    if not doc:
        return 0
    groups = doc.get("sittings")
    if groups is None:
        groups = [doc]
    total = 0
    for g in groups or []:
        for b in (g.get("bookings") or []):
            total += int(b.get("adults", 0) or 0) + int(b.get("kids", 0) or 0)
    return total


def booked_covers(start, end, token=None, api=API):
    """{date: covers} over [start, end). Empty dict when the API is unavailable."""
    token = token if token is not None else _token()
    if not token:
        return {}
    out, d = {}, start
    while d < end:
        doc = fetch_day(d.isoformat(), token=token, api=api)
        if doc is None:
            return {}          # all-or-nothing: a partial window is worse than none
        out[d] = covers_from_day(doc)
        d += timedelta(days=1)
    return out


def excess_covers(covers_by_day, baseline=None) -> float:
    """Covers ABOVE the day's normal baseline, summed over the window."""
    baseline = baseline or BASELINE_COVERS
    tot = 0.0
    for d, c in (covers_by_day or {}).items():
        tot += max(0.0, c - baseline.get(d.weekday(), 0))
    return tot


def per_cover_rates(consumption_by_sku, covers_per_week: float):
    """Units per cover per SKU, from history.

    `covers_per_week` is the venue's normal weekly covers; the model passes the
    baseline sum so the rate is 'units per cover on a normal week'. Anything
    finer needs a covers history feed this repo does not carry yet.
    """
    if covers_per_week <= 0:
        return {}
    out = {}
    for sku, series in consumption_by_sku.items():
        tail = series[-8:]
        if not tail:
            continue
        wk = sum(tail) / len(tail)
        if wk > 0:
            out[sku] = wk / covers_per_week
    return out


def baseline_weekly_covers(baseline=None) -> float:
    baseline = baseline or BASELINE_COVERS
    return float(sum(baseline.values()))


def shadow_uplift(consumption_by_sku, window_start, window_end,
                  token=None, api=API):
    """{sku: uplift_units} for the exposure window, plus a status string.

    Returns ({}, 'unavailable: ...') whenever the API cannot be read — which is
    the current state on the build box, and is a zero uplift by construction.
    """
    tok = token if token is not None else _token()
    if not tok:
        return {}, f"unavailable: no {TOKEN_ENV} in environment (admin endpoints return 401)"
    covers = booked_covers(window_start, window_end, token=tok, api=api)
    if not covers:
        return {}, "unavailable: bookings API unreachable or returned no days"
    extra = excess_covers(covers)
    if extra <= 0:
        return {sku: 0.0 for sku in consumption_by_sku}, "live: no covers above baseline"
    rates = per_cover_rates(consumption_by_sku, baseline_weekly_covers())
    return ({sku: round(r * extra, 3) for sku, r in rates.items()},
            f"live: {extra:.0f} covers above baseline in window")


if __name__ == "__main__":  # pragma: no cover
    start = date.today()
    up, status = shadow_uplift({}, start, start + timedelta(days=7))
    print(status)
