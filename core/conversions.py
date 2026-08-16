"""Declared pack -> base-unit conversions (data/declared_conversions.yaml).

Every entry cites documented evidence (Appendix K conventions, BO product
names, chef confirmations) — the file's header comment is the contract.
Consumed by build_costs.py to restate pack-unit cost series (bottle) into
base units, which is what lets the ingredient map merge a supplier's
bottle-priced series into a per-ml canon without building a mixed-unit
series (defect class 4.1). Refusal over guessing: an id with no entry here
simply stays in whatever unit its source declared.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from core.domain import canonical_purchasable


def conversion_key(pid: str) -> str:
    """Space-insensitive canonical key: 'bacchus:FD2MOTHER 23' and
    'bacchus:FD2MOTHER23' are one supplier code with two spellings."""
    return canonical_purchasable(pid).replace(" ", "")


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "data" / "declared_conversions.yaml"


def load_declared_conversions(path: Path = PATH) -> dict[str, dict]:
    """id -> {from_unit, to_qty, to_unit, evidence}, keyed by every id in
    applies_to (falling back to the ingredient id itself)."""
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    for e in yaml.safe_load(path.read_text(encoding="utf-8-sig")) or []:
        entry = {"from_unit": str(e["from_unit"]).lower(),
                 "to_qty": int(e["to_qty"]), "to_unit": str(e["to_unit"]).lower(),
                 "evidence": e.get("evidence", "")}
        for pid in e.get("applies_to") or [e["ingredient"]]:
            out[conversion_key(pid)] = entry
    return out
