#!/usr/bin/env python3
"""One product, one name — the join key the stock ledger deducts on.

Product name is how a till line finds its recipe. Two things break that join,
and both are silent:

  1. The EMAILED Insights export mangles non-ASCII to a literal '|'
     ("No Jalape|os", "Dom P|rignon"). The pulled history spells them
     correctly. A recipe keyed on "Jalapeños" simply never matches the mangled
     rows — it does not raise, it just deducts nothing.
  2. A SKU renamed IN PLACE carries its NEW name on OLD sales in the history
     pull, because the report endpoint joins to the current product master.
     Where the SKU was REUSED for a different dish, old sales inherit the wrong
     recipe entirely.

`data/product_renames.yaml` is the adjudicated register. This module applies it.

Deliberately NOT clever: no fuzzy matching, no edit distance, no "these look
similar so they're probably the same". Every mapping here was decided by a
human against the committed exports. An unrecognised name passes through
untouched — a wrong join is worse than an unresolved one.
"""
from __future__ import annotations

import os
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
REGISTER = ROOT / "data" / "product_renames.yaml"
SPELLINGS = ROOT / "data" / "product_spellings.csv"

# The emailed export replaces every non-ASCII character with a literal '|'
# (verified in the raw bytes of insights_stow_2026-08-08.csv: b'No Jalape|os').
# It is not a mis-decode — no codec recovers it — so the correct spelling is
# recovered by key instead: mangle the known-good names the same way and look
# the broken one up. Ambiguous keys are left alone rather than guessed.
_MANGLE = re.compile(r"[^\x00-\x7F]")


@lru_cache(maxsize=1)
def _spelling_index() -> dict[str, str]:
    if not SPELLINGS.exists():
        return {}
    seen: dict[str, set[str]] = {}
    with SPELLINGS.open(encoding="utf-8") as f:
        next(f, None)
        for line in f:
            name = line.rstrip("\n").strip().strip('"')
            if not name:
                continue
            seen.setdefault(_MANGLE.sub("|", name), set()).add(name)
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def _parse_register(text: str) -> tuple[dict[str, str], list[tuple[str, str, date]]]:
    """-> (canonical spelling map, [(old, new, changed_on)]).

    A deliberately small hand-parser: the register is a flat list of entries
    with the same handful of keys, and this module is imported by the daily
    aggregator, which must not grow a YAML dependency for 8 records.
    """
    canon: dict[str, str] = {}
    reused: list[tuple[str, str, date]] = []
    old = new = identity = changed = None

    def flush():
        if not (old and new and identity):
            return
        if identity in ("encoding_artifact", "same_product"):
            canon[old] = new
        elif identity == "different_product" and changed:
            reused.append((old, new, changed))

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("- old_name:"):
            flush()
            old = new = identity = changed = None
            old = _unquote(line.split(":", 1)[1])
        elif line.startswith("new_name:"):
            new = _unquote(line.split(":", 1)[1])
        elif line.startswith("identity:"):
            identity = line.split(":", 1)[1].split("#")[0].strip()
        elif line.startswith("changed_on:"):
            v = line.split(":", 1)[1].split("#")[0].strip()
            changed = date.fromisoformat(v) if v else None
    flush()
    return canon, reused


def _unquote(s: str) -> str:
    s = s.strip()
    m = re.match(r'^"(.*)"$|^\'(.*)\'$', s)
    return (m.group(1) if m.group(1) is not None else m.group(2)) if m else s


@lru_cache(maxsize=1)
def _register() -> tuple[dict[str, str], tuple[tuple[str, str, date], ...]]:
    if not REGISTER.exists():
        return {}, ()
    canon, reused = _parse_register(REGISTER.read_text())
    return canon, tuple(reused)


def canonical_name(name: str, day: date | None = None, *,
                   source_kind: str = "committed_export") -> str:
    """The name this till line should be known by.

    `day` and `source_kind` matter only for reused SKUs: the relabel applies to
    the HISTORY PULL, which reports old sales under the new name. A committed
    export already carries the name as at the sale, so it is left alone — the
    fact beats the reconstruction.
    """
    canon, reused = _register()
    out = name
    if "|" in out:
        out = _spelling_index().get(out, out)
    out = canon.get(out, out)

    if source_kind == "history_pull" and day is not None:
        for old, new, changed_on in reused:
            if out == new and day < changed_on:
                return old
    return out


def register_summary() -> str:
    canon, reused = _register()
    return (f"{len(canon)} canonical spelling(s), {len(reused)} reused SKU(s)")


if __name__ == "__main__":
    canon, reused = _register()
    print(register_summary())
    for k, v in sorted(canon.items()):
        print(f"  {k!r} -> {v!r}")
    for old, new, changed in reused:
        print(f"  reused: {new!r} before {changed} is really {old!r}")
