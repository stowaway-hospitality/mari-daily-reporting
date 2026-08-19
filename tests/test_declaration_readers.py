"""
Every declaration is read by everyone it names.

THE FAILURE THIS PINS
---------------------
A ruling written down once, with its arithmetic, that one reader was not
consulting. Four of them surfaced in a single session on 2026-08-19 — the
Tandoori line relabel, the ILG account codes, the Back Office DefaultSize, the
Garlic Oil and Mint Yoghurt yields — each already solved, each reaching a module
that was not the one pricing the menu, and each found by accident.

`check_declarations_bind.py` asks whether a declaration still matches a record.
This asks the other question: does everyone who should read it, read it. Nothing
answered that before core/declarations.py, which is why every session found the
next disconnected pair instead of the last one.

WHY THESE ASSERTIONS ARE SHAPED THIS WAY
----------------------------------------
`<=`, never `==`. A test written as "there are exactly 55 hand-rolled parses"
expires the moment somebody converts one, and three tests written earlier that
same day did exactly that — they pinned a defect, so fixing the defect broke the
suite and the fix looked like the problem. A ratchet only ever objects to the
number going the wrong way.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from core.declarations import all_declarations                      # noqa: E402
from check_declaration_readers import bypasses, consults, sweep     # noqa: E402

BASELINE = ROOT / "baselines" / "declaration_readers.json"


def _pin() -> dict:
    return json.loads(BASELINE.read_text()) if BASELINE.exists() else {}


def test_every_declaration_names_at_least_one_reader():
    """A declaration nobody is bound to read is already dead — it just does not
    know it yet. That is the ILG account-code case exactly: a correct rule, in
    the file, with a worked example in EXTRACTION.md, and 173 invoices that
    never met it."""
    orphans = [d.name for d in all_declarations() if not d.readers]
    assert not orphans, (
        f"{orphans} declare no readers. Name the modules that must honour each "
        f"one in core/declarations.py, or delete the file.")


def test_every_declaration_file_exists():
    missing = [d.name for d in all_declarations() if not d.path.exists()]
    assert not missing, (
        f"{missing} are in the registry with no file behind them. A registry "
        f"that describes files that are not there teaches people to distrust it.")


def test_no_reader_is_named_without_being_wired():
    """The whole point. A NEW gap means somebody added a declaration and wired
    one reader — the habit that cost the days."""
    new, _pinned = sweep()
    assert not new, (
        "declaration(s) name a reader that never opens them:\n  "
        + "\n  ".join(f"{g['declaration']} -> {g['reader']} ({g['why']})"
                      for g in new)
        + "\n\nWire it, or move it to that declaration's known_gaps with the "
          "reason it cannot be wired blind. An unexplained gap is the state "
          "this whole registry exists to make impossible.")


def test_pinned_gaps_are_still_gaps():
    """A known_gap that has been CLOSED must be deleted, not left standing.

    Deliberately fails when the news is good, because the alternative is a
    registry carrying a paragraph of evidence about a defect that no longer
    exists — and a stale finding is how a reader learns to skim the file.
    The fix is one line: delete the entry.
    """
    stale = []
    for d in all_declarations():
        for reader in d.known_gaps:
            if reader not in d.readers:
                stale.append(f"{d.name} -> {reader} (not in readers at all)")
            elif consults(reader, d)[0]:
                stale.append(f"{d.name} -> {reader} (now reads it — delete the "
                             f"known_gaps entry)")
    assert not stale, "known_gaps is out of date:\n  " + "\n  ".join(stale)


def test_hand_rolled_parses_only_ever_fall():
    """Six modules each spelled the pack_overrides path out for themselves. Every
    one is somewhere the encoding, the last-wins rule, or the document shape can
    diverge — core/pack_overrides.py already lost ~700 cost observations to a
    per-reader read_text() under an ASCII locale. Ratcheted: this number may
    fall, and may not rise."""
    pin = _pin().get("hand_rolled_parses")
    if pin is None:
        return
    found = bypasses()
    assert len(found) <= pin, (
        f"{len(found)} modules parse a declaration file directly, against a pin "
        f"of {pin}. Import it from core.declarations instead.\n  "
        + "\n  ".join(f"{b['module']} -> {b['declaration']}"
                      for b in found[:12]))
