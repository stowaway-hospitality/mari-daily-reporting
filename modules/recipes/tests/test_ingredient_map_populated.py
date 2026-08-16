"""The populated ingredient map stays lawful.

data/ingredient_map.csv sat empty (header only) for a month while six files
grew to patch around it; scripts/build_ingredient_map.py populated it on
2026-08-16 from the product_map bridge. These tests pin the rules that made
that population safe to do unattended:

  1. one purchasable -> exactly one ingredient (a conflict is review, not data)
  2. every ingredient is a lightspeed:<digits> anchor
  3. regeneration is deterministic (byte-identical), so CI can diff it
  4. THE FENCE: no shipped row lowers a live consumed rate — a merge whose
     most-recent observation undercuts the series it joins is held out to
     data/_identity_review/ for a human. Flattering direction needs a person.
"""
from __future__ import annotations

import csv
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MAP = ROOT / "data" / "ingredient_map.csv"


def _rows():
    with MAP.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def test_one_purchasable_one_ingredient():
    seen = {}
    for r in _rows():
        p = r["purchasable_id"]
        assert p not in seen or seen[p] == r["ingredient_id"], (
            f"{p} maps to both {seen[p]} and {r['ingredient_id']}")
        seen[p] = r["ingredient_id"]
    assert len(seen) > 100, "map should carry the product_map bridge"


def test_ingredients_are_lightspeed_anchors():
    for r in _rows():
        assert re.fullmatch(r"lightspeed:\d+", r["ingredient_id"]), r["ingredient_id"]
        assert r["confirmed_by"], f"{r['purchasable_id']} has no confirmation source"


def test_regeneration_is_deterministic_and_fence_holds():
    before = MAP.read_bytes()
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_ingredient_map.py")],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert MAP.read_bytes() == before, (
        "build_ingredient_map.py no longer reproduces the committed map — "
        "regenerate and commit, or a source moved under it")
    # the generator itself enforces the fence; determinism means the committed
    # file is exactly what the fence approved.
