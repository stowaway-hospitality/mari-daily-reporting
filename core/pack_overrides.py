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
    try:
        # encoding pinned, not inherited: this file carries UTF-8 (em-dashes and
        # quotes in the chef's notes), and under an ASCII locale read_text() raised
        # a UnicodeDecodeError that the old bare `except Exception` swallowed —
        # every confirmation vanished silently and ~700 cost observations with it.
        docs = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or []
    except yaml.YAMLError:
        return {}          # malformed log: no confirmations, but say so by having none
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
