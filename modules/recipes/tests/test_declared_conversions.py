"""The declared-conversion layer stays lawful.

data/declared_conversions.yaml restates pack-unit cost series (bottle) into
base units at derivation — the change that lets the ingredient map merge a
supplier's bottle-priced series into a per-ml canon without building the
mixed-unit series this book fights everywhere else (defect class 4.1).

Rules pinned here:
  1. an id covered by a conversion has NO remaining rows in its from_unit —
     a half-restated series is worse than an unrestated one
  2. restatement is arithmetic, not opinion: bottle price / to_qty, exactly
     (Mother's Milk: 16.0817/750 = 0.021442 — matching the canon's own
     invoice-fed rate to the microcent was the proof the conversion is true)
  3. spelling variants of a supplier code hit the same conversion
     ("FD2MOTHER 23" vs "FD2MOTHER23" — one code, two documents)
"""
from __future__ import annotations

import csv
from pathlib import Path

from core.conversions import load_declared_conversions, conversion_key

ROOT = Path(__file__).resolve().parents[3]


def test_no_pack_unit_rows_survive_for_converted_ids():
    declared = load_declared_conversions()
    assert len(declared) >= 16
    leftovers = []
    with (ROOT / "data" / "costs.csv").open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            conv = declared.get(conversion_key(r["ingredient"]))
            if conv and r["unit"].lower() == conv["from_unit"]:
                leftovers.append((r["ingredient"], r["observed_on"]))
    assert not leftovers, f"half-restated series: {leftovers[:5]}"


def test_restatement_is_exact_division():
    with (ROOT / "data" / "costs.csv").open(encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f)
                if r["ingredient"] == "bacchus:FD2MOTHER23" and "declared" in r["pack"]]
    assert rows, "Mother's Milk restated row went missing"
    assert rows[-1]["unit"] == "ml"
    assert rows[-1]["cost_per_unit"] == "0.021442"     # 16.0817 / 750, 6dp


def test_spelling_variants_share_a_conversion():
    d = load_declared_conversions()
    a = d.get(conversion_key("bacchus:FD2MOTHER 23"))
    b = d.get(conversion_key("bacchus:FD2MOTHER23"))
    assert a is not None and a is b
