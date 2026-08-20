#!/usr/bin/env python3
"""Build `data/functions_gp.json` -- what each package function actually made.

    data/function_tabs/*.json   ──►  gross_profit()  ──►  data/functions_gp.json
    the comped tab, line by line                          the feed the screen reads

Run:  python3 modules/functions/pipeline/build_functions_gp.py
      python3 modules/functions/pipeline/build_functions_gp.py --check

`--check` rebuilds and byte-compares instead of writing, which is what the
suite uses. The feed is COMMITTED rather than gitignored-and-rebuilt-in-CI,
because wiring a new build step into `.github/workflows/` needs the `ops`
claim (SESSIONS.md rule 7) and this pass does not hold it. A committed derived
file that no longer reproduces is a fossil, so
`modules/functions/tests/test_functions_gp_feed.py` regenerates it on every
pytest run and fails on any difference -- the same contract `data/costs.csv`
lives under.

THE COST BOOK, AND WHY IT IS CHOSEN PER NIGHT
---------------------------------------------
`data/lightspeed_recipes_costed.json` carries no effective date. It answers
"what does a pour cost today". For a function that ran in August that is the
wrong question and the answer drifts: between 8 and 20 August 2026 Rooster Rojo
Blanco Tequila [House] moved $1.9641 -> $2.6065 in the live book.

So a night is costed against `data/function_tabs/cost_book_<date>.json` when one
exists -- a CLOSED, dated snapshot, where a product that is absent was uncosted
on the night and is reported as uncosted rather than as free. A night with no
snapshot falls through to the live book, and the feed says `cost_book_as_of:
"live"` so nobody mistakes the figure for a reproducible one.
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.functions.gross_profit import (            # noqa: E402
    BEVERAGE_BENCHMARK_GP_PCT, FunctionNight, Line, MixerAssumption, gross_profit)

TABS = ROOT / "data" / "function_tabs"
FEED = ROOT / "data" / "functions_gp.json"
SCHEMA = "functions_gp/1"


# --------------------------------------------------------------- cost books

def dated_book(date: str):
    """A closed, dated cost book for `date`, or None.

    Returns `(costs, as_of, mixer)`. `costs` returns None for anything the book
    does not price -- deliberately, and that is the whole point of "closed".
    """
    p = TABS / f"cost_book_{date}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    table = {k: Decimal(v) for k, v in d["unit_cost_ex"].items()}
    m = d.get("mixer") or {}
    mixer = MixerAssumption(
        per_pour_ex=Decimal(m.get("per_pour_ex", "0.9483")),
        also=frozenset(m.get("also") or ()))
    return (lambda name: table.get(name)), d["as_of"], mixer


def live_book(venue: str = "stowaway"):
    """The current book, read exactly the way the P&L reads it.

    `scripts.cogs_blend` is the one reader: exact POS name first, then the
    category-word-stripped fallback that settles "Fresh is Best Lager Pint" vs
    the book's "Fresh is Best Lager - Pint". Writing a second reader here would
    be a second set of rules for the same question, and the two would disagree
    on the day one of them was improved.
    """
    from scripts.cogs_blend import _load_book_costs, book_cost
    table = _load_book_costs(venue)
    return (lambda name: book_cost(table, name)), "live", MixerAssumption()


# --------------------------------------------------------------- the build

def read_tab(path: Path) -> FunctionNight:
    d = json.loads(path.read_text(encoding="utf-8"))
    if d.get("schema") != "function_tab/1":
        raise ValueError(f"{path.name}: unknown schema {d.get('schema')!r}")
    return FunctionNight(
        name=d["name"], date=d["date"], venue=d.get("venue", "stowaway"),
        heads=int(d["heads"]),
        package_price_inc=Decimal(d["package_price_inc"]),
        package_hours=Decimal(d["package_hours"]) if d.get("package_hours") else None,
        food_revenue_inc=Decimal(d.get("food_revenue_inc") or "0"),
        booked_guests=d.get("booked_guests"),
        tickets_sold=d.get("tickets_sold"),
        pos_refs=d.get("pos_refs", ""),
        lines=[Line(l["product"], int(l["qty"]), Decimal(l["menu_value_inc"]))
               for l in d["lines"]],
    )


def build() -> dict:
    out = []
    for path in sorted(TABS.glob("*.json")):
        if path.name.startswith("cost_book_"):
            continue
        night = read_tab(path)
        chosen = dated_book(night.date) or live_book(night.venue)
        costs, as_of, mixer = chosen
        rep = gross_profit(night, costs, mixer=mixer, cost_book_as_of=as_of)
        rep["id"] = f"{night.date}_{path.stem.split('_', 1)[-1]}"
        rep["source_file"] = f"data/function_tabs/{path.name}"
        out.append(rep)
    return {
        "schema": SCHEMA,
        "benchmark_gp_pct": float(BEVERAGE_BENCHMARK_GP_PCT),
        "note": "Beverage gross profit per package function, computed from the "
                "comped tab's own line items against a dated cost book. Every "
                "entry carries `caveats`; a consumer that renders `gp_pct` "
                "without them is misreporting the number.",
        "functions": out,
    }


def render(feed: dict) -> str:
    return json.dumps(feed, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="rebuild and byte-compare instead of writing")
    a = ap.parse_args()
    text = render(build())
    if a.check:
        have = FEED.read_text(encoding="utf-8") if FEED.exists() else ""
        if have != text:
            print(f"{FEED.relative_to(ROOT)} does NOT reproduce from its sources.")
            print("Rebuild: python3 modules/functions/pipeline/build_functions_gp.py")
            return 1
        print(f"{FEED.relative_to(ROOT)} reproduces byte-identically.")
        return 0
    FEED.write_text(text, encoding="utf-8")
    n = len(json.loads(text)["functions"])
    print(f"wrote {FEED.relative_to(ROOT)} -- {n} function(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
