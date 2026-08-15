"""Whole-unit drinks bundled inside Lightspeed deals.

Zak, 2026-08-10: "the pars on soft drinks are definitely way too low, you need
to look again".

He was right, and the reason is a naming mismatch one layer deeper than the
alias file reaches. Lightspeed's own costed recipe book
(`data/lightspeed_recipes_costed.json`) defines the DEALS -- "$60 BANQUET",
"$90 PIZZA PARTY", "Banquet Deal Pizzas" -- and each bundles a whole 1.25L
bottle of Coke. The bottle never rings as its own sale: the customer buys one
deal, and a bottle leaves the fridge. Measured over 13 weeks that is 5.54
bottles a week of `1.25L Coke` -- against a par SKU spelled `Coke 1.25L`.
Same product, reversed word order, so nothing ever matched it. Coke 1.25L was
modelled at 5.72/wk when the real draw is ~11.3/wk, on a live par of 9.9:
under one week of cover on a line that sells every day.

WHAT THIS MODULE DOES *NOT* DO, and why that matters more than what it does:
it ingests **whole-unit components only** (`ea` / `each` / `unit` / blank).
Ingredients measured in ml or g are already converted and counted by the normal
recipe path in model.py. Ingesting those here would double-count every spirit
and every keg in the book -- Rooster, Bombay, Stone & Wood, Aperol -- and
silently inflate the pars it is supposed to be correcting. `WHOLE_UNITS` is the
guard, and the tests assert the ml/g SKUs do not move.

Food is deliberately not a concern here. Zak: "we aren't doing pars on food
items" -- the pizzas, wings and bases inside these same deals are out of scope.
Deals are read for their DRINKS and nothing else.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from collections import defaultdict

WHOLE_UNITS = {"ea", "each", "unit", ""}
LS_BOOK = "lightspeed_recipes_costed.json"


def _tokens(s: str) -> frozenset:
    """Order-insensitive identity for a product name.

    `1.25L Coke` and `Coke 1.25L` are the same bottle. A token SET makes them
    equal without the fragile substring rules that made this bug in the first
    place. Full-set equality (never subset) keeps `Coke 1.25L` from colliding
    with `Coke Zero 1.25L` -- the extra token is exactly the difference that
    matters, and a subset match would happily merge the two.
    """
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = re.sub(r"[^a-z0-9.]+", " ", s)
    return frozenset(t for t in s.split() if t)


def load_book(data_dir: str) -> dict:
    path = os.path.join(data_dir, LS_BOOK)
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        return (json.load(fh) or {}).get("recipes") or {}


def build_index(par_names) -> dict:
    """token-set -> par SKU. Ambiguous token-sets are dropped, not guessed."""
    idx, seen = {}, defaultdict(list)
    for name in par_names:
        seen[_tokens(name)].append(name)
    for toks, names in seen.items():
        if len(names) == 1 and toks:
            idx[toks] = names[0]
    return idx


def deal_units(data_dir, par_names, weekly_qty, weeks, aliases=None):
    """Whole-unit drink components of sold deals, per par SKU per week.

    weekly_qty: {pos_product_name: [qty per week]} aligned to `weeks`.
    Returns (series, resolved, refused) where series is {par_sku: [units/wk]}.
    """
    book = load_book(data_dir)
    idx = build_index(par_names)
    n = len(weeks)
    series = defaultdict(lambda: [0.0] * n)
    resolved, refused = [], []

    for product, rec in book.items():
        qty = weekly_qty.get(product)
        if not qty or not any(qty):
            continue
        # SOLD-AS-BOUGHT: Lightspeed's book carries ~47 entries whose "recipe" is
        # the product itself ("Coke 1.25L" made of one "Coke 1.25L"), so it can
        # cost a resale line from its own Back Office price. That is not a deal
        # and its volume is ALREADY counted by the pour path — ingesting it adds
        # the same bottle twice. Measured: it inflated Coke 1.25L by +5.4/wk on
        # top of the genuine +5.5 from real deals. Skip self-reference.
        own = idx.get(_tokens(product))
        for ing in (rec.get("ingredients") or []):
            if not isinstance(ing, dict):
                continue
            desc = str(ing.get("desc") or ing.get("name") or "").strip()
            if not desc:
                continue
            unit = str(ing.get("unit") or "").strip().lower()
            if unit not in WHOLE_UNITS:
                continue          # ml/g -> the normal recipe path owns it
            try:
                per = float(ing.get("qty") or 0)
            except (TypeError, ValueError):
                continue
            if per <= 0:
                continue

            target = None
            if aliases is not None:
                target = aliases.target(desc)          # explicit alias wins
            if target is None:
                target = idx.get(_tokens(desc))
            if target is None:
                refused.append({"deal": product, "ingredient": desc,
                                "qty_per_deal": per,
                                "reason": "no par SKU with this token set"})
                continue
            if own is not None and target == own:
                refused.append({"deal": product, "ingredient": desc,
                                "qty_per_deal": per,
                                "reason": "sold-as-bought self-reference; the "
                                          "pour path already counts this sale"})
                continue

            for i, q in enumerate(qty):
                if q:
                    series[target][i] += q * per
            resolved.append({"deal": product, "ingredient": desc,
                             "target_sku": target, "qty_per_deal": per,
                             "units_per_week": round(sum(qty) * per / max(n, 1), 3)})

    return dict(series), resolved, refused
