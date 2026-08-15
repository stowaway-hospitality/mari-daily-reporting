#!/usr/bin/env python3
"""
Par model v3 — lead time + holiday calendar.

The par model used to assume a flat one-week coverage window and then rely on a
human applying a "×1.3 because of the long weekend" fudge at order time. That is
exactly backwards: the ordering cycle is a KNOWN, computable thing, so the par
should already be right in a holiday week.

The cycle
---------
Pars are reviewed and the ILG order is placed **Sunday**; ILG's cutoff is 11:00
Tuesday, so a Sunday order makes the **Wednesday** run. Therefore the stock that
lands on Wednesday has to cover trade until the NEXT delivery lands.

  * normal week            order Sun -> deliver Wed
  * holiday MONDAY         the Wednesday run slips to FRIDAY
  * holiday on that day    (i.e. a holiday FRIDAY) -> no delivery at all that
                           week; the goods slip to the next available delivery
                           day, which is the following cycle's run.

Exposure is measured in weighted **day-units**, not flat weeks, because Fri/Sat/
Sun trade about double a weekday at Stowaway and a public-holiday Monday trades
like a weekend day. A normal Wed->Wed cycle is

    Wed 1 + Thu 1 + Fri 2 + Sat 2 + Sun 2 + Mon 1 + Tue 1 = 10 day-units

and 10 is the denominator every other cycle is expressed against.

Christmas 2026 is the case this module exists for:
    Sun 20 Dec order -> Wed 23 Dec delivery (last normal run)
    Sun 27 Dec order -> Mon 28 Dec is a holiday -> Wed 30 Dec slips to Fri 1 Jan
                     -> Fri 1 Jan is New Year's Day -> NO delivery
    Sun 03 Jan order -> Wed 6 Jan
so the 23 Dec delivery carries **14 days / 21 day-units ≈ 2.1× a normal cycle**
over peak summer trade.

All dates come from `data/par_calendar.json` (copied out of the reorder skill's
rules.json — nothing outside this repo is read at runtime).
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY, SATURDAY, SUNDAY = range(7)

NORMAL_CYCLE_DAY_UNITS = 10.0
_DEFAULT_WEIGHTS = {
    MONDAY: 1.0, TUESDAY: 1.0, WEDNESDAY: 1.0, THURSDAY: 1.0,
    FRIDAY: 2.0, SATURDAY: 2.0, SUNDAY: 2.0,
}
_PUBLIC_HOLIDAY_WEIGHT = 2.0
_WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday",
                  "Friday", "Saturday", "Sunday"]

# A cancelled delivery can only chain so far before something is wrong with the
# calendar; refuse to loop forever.
_MAX_CHAIN_WEEKS = 8


def load_calendar(data_dir: str = "data") -> dict:
    with open(os.path.join(data_dir, "par_calendar.json")) as fh:
        return json.load(fh)


def holiday_map(cal: dict) -> dict:
    """{'YYYY-MM-DD': 'Holiday name'} across every year in the calendar."""
    out = {}
    for year, entries in (cal.get("publicHolidays") or {}).items():
        if year.startswith("_"):
            continue
        for e in entries:
            out[e["date"]] = e.get("name", "public holiday")
    return out


def day_weights(cal: dict) -> dict:
    """Weekday -> weight, from the calendar file (falls back to the defaults)."""
    dw = (cal.get("day_weights") or {})
    out = dict(_DEFAULT_WEIGHTS)
    for i, nm in enumerate(_WEEKDAY_NAMES):
        if nm in dw:
            out[i] = float(dw[nm])
    return out


def _ph_weight(cal: dict) -> float:
    return float((cal.get("day_weights") or {}).get("public_holiday",
                                                    _PUBLIC_HOLIDAY_WEIGHT))


def _normal_units(cal: dict) -> float:
    return float((cal.get("day_weights") or {}).get("normal_cycle_day_units",
                                                    NORMAL_CYCLE_DAY_UNITS))


def _as_date(d) -> date:
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d))


def next_order_sunday(today=None) -> date:
    """The Sunday whose order we are pricing. Sunday itself counts as itself."""
    today = _as_date(today or date.today())
    return today + timedelta(days=(SUNDAY - today.weekday()) % 7)


def resolve_delivery(order_sunday, cal: dict):
    """Delivery date for an order placed on `order_sunday`.

    Returns (delivery_date_or_None, note). None means the week's run is
    cancelled outright by a holiday landing on the (already-shifted) delivery
    day — the goods slip into the following cycle.
    """
    order_sunday = _as_date(order_sunday)
    hol = holiday_map(cal)
    monday = order_sunday + timedelta(days=1)
    if monday.isoformat() in hol:
        delivery = monday + timedelta(days=4)          # Wed -> Fri
        note = f"holiday Monday ({hol[monday.isoformat()]}) — Wed run slips to Fri"
    else:
        delivery = monday + timedelta(days=2)          # normal Wednesday
        note = "normal Wednesday run"
    if delivery.isoformat() in hol:
        return None, (f"{note}; but {delivery.isoformat()} is "
                      f"{hol[delivery.isoformat()]} — NO delivery this cycle")
    return delivery, note


def delivery_schedule(order_sunday, cal: dict, cycles: int = 6):
    """[(order_sunday, delivery_date|None, note)] for `cycles` consecutive weeks."""
    order_sunday = _as_date(order_sunday)
    out = []
    for k in range(cycles):
        os_k = order_sunday + timedelta(days=7 * k)
        d, note = resolve_delivery(os_k, cal)
        out.append((os_k, d, note))
    return out


def next_delivery_after(order_sunday, cal: dict):
    """The first delivery that lands AFTER this order's own delivery."""
    order_sunday = _as_date(order_sunday)
    this, _ = resolve_delivery(order_sunday, cal)
    for k in range(1, _MAX_CHAIN_WEEKS + 1):
        d, _n = resolve_delivery(order_sunday + timedelta(days=7 * k), cal)
        if d is not None and (this is None or d > this):
            return d
    raise ValueError(f"no delivery found within {_MAX_CHAIN_WEEKS} weeks of {order_sunday}")


def day_unit(d, cal: dict) -> float:
    """Weighted trade weight of a single calendar day."""
    d = _as_date(d)
    hol = holiday_map(cal)
    w = day_weights(cal)[d.weekday()]
    if d.isoformat() in hol:
        w = max(w, _ph_weight(cal))
    return w


def exposure(order_sunday, cal: dict) -> dict:
    """The full exposure picture for an order placed on `order_sunday`.

    Exposure runs from the day this order's stock LANDS up to (not including)
    the day the next delivery lands — that is the window the par has to survive.
    """
    order_sunday = _as_date(order_sunday)
    delivery, note = resolve_delivery(order_sunday, cal)
    if delivery is None:
        # This week's order cannot be delivered; nothing to cover — the previous
        # cycle's par carried it. Report a zero-length window explicitly.
        return {
            "order_sunday": order_sunday.isoformat(),
            "delivery": None,
            "next_delivery": next_delivery_after(order_sunday, cal).isoformat(),
            "note": note,
            "days": 0,
            "day_units": 0.0,
            "normal_day_units": _normal_units(cal),
            "exposure_ratio": 0.0,
            "holidays_in_window": [],
        }
    nxt = next_delivery_after(order_sunday, cal)
    hol = holiday_map(cal)
    units, days, hits = 0.0, 0, []
    d = delivery
    while d < nxt:
        units += day_unit(d, cal)
        if d.isoformat() in hol:
            hits.append({"date": d.isoformat(), "name": hol[d.isoformat()]})
        days += 1
        d += timedelta(days=1)
    normal = _normal_units(cal)
    return {
        "order_sunday": order_sunday.isoformat(),
        "delivery": delivery.isoformat(),
        "next_delivery": nxt.isoformat(),
        "note": note,
        "days": days,
        "day_units": round(units, 2),
        "normal_day_units": normal,
        "exposure_ratio": round(units / normal, 4) if normal else 1.0,
        "holidays_in_window": hits,
    }


def christmas_2026_exposure(cal: dict) -> dict:
    """The 20 Dec 2026 order — the one that has to carry 23 Dec -> 6 Jan."""
    return exposure(date(2026, 12, 20), cal)


if __name__ == "__main__":  # pragma: no cover — a look-at-it helper
    cal = load_calendar(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
    for os_k, d, note in delivery_schedule(date(2026, 12, 6), cal, cycles=6):
        print(f"{os_k}  ->  {(d.isoformat() if d else 'NO DELIVERY'):<12}  {note}")
    print()
    for k, v in christmas_2026_exposure(cal).items():
        print(f"  {k}: {v}")
