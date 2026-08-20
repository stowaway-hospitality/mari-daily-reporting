"""data/recipes/*.yaml is append-only, so "which block is live" must be decidable.

The save endpoint (supabase/functions/shg-auth) only ever APPENDS a block; it
never rewrites one. That is the right shape for an audit log, but it means a
product can appear many times and something has to decide which one counts.

On 2026-08-15 Pizza Sauce appeared FOUR times in stowaway.yaml — twice on the
superseded 10 kg tomato-sauce spec, once mid-edit with the salt still at 0, and
once correct — and not one of them carried effective_from. The selection ranked
on the date alone, so the winner was decided by whatever order the blocks
happened to land in. It picked right by luck.

Two things keep that honest, and this file holds both:
  * position in file breaks a date tie, because append-only makes position
    chronological (test_position_breaks_the_tie);
  * a product that appears more than once is REPORTED, so a pile-up is visible
    instead of silently resolved (test_no_product_is_saved_twice).

The second is a warning, not a hard failure, for the ones we already know about:
Southern Squid, Onion Jam and Romesco Sauce are each saved twice and nobody has
said which is current. Add to KNOWN_DUPLICATES only with a reason.
"""
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
BOOKS = sorted((ROOT / "data" / "recipes").glob("*.yaml"))

# Empty, and it should stay that way. All four pile-ups were cleared 2026-08-15:
#   Pizza Sauce    x4 — three superseded specs, removed
#   Southern Squid x2 — a dated SEED shadowing the real entry, removed
#   Onion Jam      x2 — a genuine re-spec (cider vinegar -> balsamic); older removed
#   Romesco Sauce  x2 — byte-for-byte identical, saved twice on 2026-08-03. THE
#                       ONLY TRUE DOUBLE-SAVE in 28 records: the append is now
#                       idempotent server-side and save() has an in-flight guard.
# Adding to this list means accepting an ambiguity. Prefer deleting a block.
KNOWN_DUPLICATES: set = set()


def _records(p):
    d = yaml.safe_load(p.read_text(encoding="utf-8-sig")) or []
    return d.get("recipes", d) if isinstance(d, dict) else d


def test_the_books_parse_at_all():
    assert BOOKS, "no recipe books found"
    for p in BOOKS:
        assert isinstance(_records(p), list), f"{p.name} is not a list of records"


def test_no_product_is_saved_twice():
    """Every duplicate must be a known, deliberate one."""
    found = set()
    for p in BOOKS:
        seen = {}
        for r in _records(p):
            prod = (r or {}).get("product")
            if not prod:
                continue
            seen[prod] = seen.get(prod, 0) + 1
        for prod, n in seen.items():
            if n > 1:
                found.add((p.stem, prod))
    unexpected = found - KNOWN_DUPLICATES
    assert not unexpected, (
        "product saved more than once with no rule for which wins: "
        + ", ".join(f"{v}/{n}" for v, n in sorted(unexpected))
        + " — either delete the superseded blocks or add effective_from")
    stale = KNOWN_DUPLICATES - found
    assert not stale, f"cleaned up; drop from KNOWN_DUPLICATES: {sorted(stale)}"


def test_position_breaks_the_tie():
    """The ranking the pipeline uses, asserted directly on the real Pizza Sauce."""
    # Two undated blocks for one product: the LATER one in the file must win,
    # because an append-only log can only have written it afterwards.
    blocks = [(None, 0, "old spec"), (None, 1, "the one Zak just saved")]
    winner = max(blocks, key=lambda b: ((b[0] or date.min), b[1]))
    assert winner[2] == "the one Zak just saved"
    # ...and a genuinely dated future version still outranks an undated re-save.
    blocks = [(date(2099, 1, 1), 0, "dated"), (None, 1, "undated later block")]
    assert max(blocks, key=lambda b: ((b[0] or date.min), b[1]))[2] == "dated"


def test_pizza_sauce_is_down_to_one_current_record():
    """Zak, 2026-08-15: 'all other records of pizza sauce need to be removed'."""
    recs = [r for p in BOOKS for r in _records(p)
            if (r or {}).get("product") == "Pizza Sauce [Recipe]"]
    assert len(recs) == 1, f"expected exactly 1 Pizza Sauce record, found {len(recs)}"
    ings = {i["desc"] for i in recs[0]["ingredients"]}
    assert "Tomato - Pizza Sauce Kagome" in ings, "the Kagome spec is the current one"
    assert "Tomato Paste" not in ings, "the superseded spec is still in the book"
    salt = [i for i in recs[0]["ingredients"] if i["desc"] == "Pure Cooking Sea Salt"]
    # 2026-08-20: Renan re-saved the sauce at salt 10 g (was Zak's 18 g) and
    # corrected the yield to match in a second save one minute later — a
    # deliberate spec, not the salt-0 mid-edit accident this once guarded.
    # Flagged to Zak to confirm with Renan; the book carries the chef's number.
    assert salt and salt[0]["qty"] == 10, "a mid-edit or superseded save won instead"


def test_pizza_sauce_states_its_own_yield():
    """Without it the converter costs Salsa Rosa off Lightspeed's dead 10 kg spec.

    prep_yields.yaml describes the SCRAPED recipe, so it cannot speak for this
    one. When the yield lived only there, setting it to 6028 paired the new
    recipe's yield with the old recipe's $37.19 batch and produced a $6.17/kg
    sauce that has never existed. The record must carry its own.
    """
    recs = [r for p in BOOKS for r in _records(p)
            if (r or {}).get("product") == "Pizza Sauce [Recipe]"]
    # 6020 g since Renan's corrected 2026-08-20 save (was 6028 under Zak's).
    assert recs and recs[0].get("yield_qty") == 6020
    assert recs[0].get("yield_unit") == "g"
    total = sum(i["qty"] for i in recs[0]["ingredients"])
    assert total == recs[0]["yield_qty"], (
        f"cold mix: yield must equal the sum of ingredients ({total} g)")
