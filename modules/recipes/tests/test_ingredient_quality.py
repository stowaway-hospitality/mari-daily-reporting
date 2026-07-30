"""
Quality gate on the recipe ingredient feed — the guard that stops a mangled name
or a duplicate from reaching the picker in the first place.

The Fresh Fruit Team bug (unit word bled into the code, name truncated to a
fragment or left echoing the code — 'BBRYP Punnet') got noticed only when a human
squinted at the picker. This regenerates the feed from the committed cogs_list and
fails if any of that class is back:

  - a name that echoes its supplier code, is empty, or is only a unit word
  - two ingredients with the identical (supplier, name) — a duplicate

Deterministic from data/cogs_list.csv, so it runs in CI.

    python3 -m pytest modules/recipes/tests/test_ingredient_quality.py
"""

from __future__ import annotations

import json
from collections import Counter

from modules.recipes.pipeline import build_ingredients as bi


def _feed():
    bi.main()                       # regenerate from cogs_list (gitignored output)
    return json.loads(bi.OUT.read_text())


def test_no_suspect_names_reach_the_picker():
    feed = _feed()
    suspects = feed.get("suspect_names", [])
    assert not suspects, (
        "parse-artefact names in the ingredient feed — the parser mangled these, "
        f"fix the source (see normalize_code / _NAME_FIX): {suspects}")


def test_no_duplicate_ingredients():
    feed = _feed()
    counts = Counter((i["supplier"], i["description"]) for i in feed["ingredients"])
    dupes = {k: n for k, n in counts.items() if n > 1}
    assert not dupes, f"the same product appears more than once: {dupes}"


def test_suspect_name_gate_is_precise():
    # catches the artefact class...
    assert bi.suspect_name("BBRYP Punnet", "BBRYP Punnet")
    assert bi.suspect_name("Punnet", "SPUN")
    assert bi.suspect_name("", "X")
    # ...but never a real acronym product or a normal name
    assert not bi.suspect_name("Msg 1Kg (20)", "12874")
    assert not bi.suspect_name("Bbq Sauce 4Lt Heinz", "100164")
    assert not bi.suspect_name("Avocado Hass", "AH20T")
    assert not bi.suspect_name("Carrot", "CARK")
