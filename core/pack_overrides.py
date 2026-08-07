"""
Chef-confirmed pack sizes for ingredients whose pack the parser couldn't read.

Some invoice lines don't state how much is in a pack ("OLIVES" with no weight), so
resolve_pack can't reduce them to $/kg and they show "confirm pack" in the recipe
builder. When a chef confirms the size, the worker's /pack route appends it to
data/pack_overrides.yaml — an append-only log, so the LATEST confirmation per
ingredient wins and history is never lost. build_costs and build_ingredients both
consult this so a confirmed pack becomes a real cost observation and the ingredient
stops needing review. Keyed by purchasable_id (the same id everything else uses).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml


def load_pack_overrides(path: Path) -> dict[str, tuple[Decimal, str]]:
    """{purchasable_id: (pack_qty, base_unit)} — base_unit in g | ml | ea. Last wins."""
    if not path.exists():
        return {}
    # PIN THE ENCODING, AND DO NOT SWALLOW A READ ERROR.
    #
    # read_text() with no encoding uses locale.getpreferredencoding(). Under a C
    # or latin-1 locale that is ASCII, and this file carries UTF-8 (chef notes
    # about "Pizza Box Inserts — Gulli", "Fresh Fruit Team market herbs —"). The
    # decode then raised UnicodeDecodeError, the bare `except Exception` turned
    # it into {}, and EVERY chef-confirmed pack vanished without a word: 45
    # overrides -> 0, and 322 chef-confirmed observations dropped out of
    # costs.csv while the build still exited 0. Silent under-costing on the file
    # the whole system derives from — the same defect class as the unpinned
    # WRITES, but this one never even raised.
    #
    # Malformed YAML is still tolerated (a half-written append is expected); an
    # unreadable file is not, because that is a bug, not a chef's typo.
    try:
        docs = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or []
    except yaml.YAMLError:
        return {}
    out: dict[str, tuple[Decimal, str]] = {}
    for d in docs:
        if not isinstance(d, dict):
            continue
        _id = str(d.get("id") or "").strip()
        unit = str(d.get("pack_unit") or "").strip().lower()
        try:
            qty = Decimal(str(d.get("pack_qty")))
        except (InvalidOperation, TypeError):
            continue
        if _id and unit and qty > 0:
            out[_id] = (qty, unit)          # append-only log -> last confirmation wins
    return out
