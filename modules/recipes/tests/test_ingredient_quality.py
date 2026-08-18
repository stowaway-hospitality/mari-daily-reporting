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


# ── the dropped leading word (2026-08-15) ─────────────────────────────────
# Until today the FFT parser dropped a description's FIRST WORD into the unit
# column whenever the invoice header sat left of a hard-coded boundary, so one
# supplier code carried both "Carrot Large" and "Large". build_ingredients kept
# each code's LATEST name, so whichever spelling the most recent invoice happened
# to carry became the product name the chef saw. That is how "Large", "Hass",
# "Jap" and "Sweet" became products.
#
# The parser is fixed, but the history in data/ cannot be re-parsed (the source
# PDFs live behind the Supabase service key). _undo_dropped_prefix repairs it:
# a fragment that is a WORD-BOUNDARY SUFFIX of a longer spelling of the SAME code
# is a dropped word, never a rename.

def test_dropped_leading_word_is_restored_newest_first():
    # seq is NEWEST FIRST: the truncated spelling is the most recent.
    assert bi._undo_dropped_prefix(["Large", "Carrot Large"]) == "Carrot Large"
    assert bi._undo_dropped_prefix(["Hass", "Avocado Hass"]) == "Avocado Hass"
    assert bi._undo_dropped_prefix(["Jap", "Pumpkin Jap"]) == "Pumpkin Jap"
    assert bi._undo_dropped_prefix(["Sweet", "Corn Sweet"]) == "Corn Sweet"
    assert bi._undo_dropped_prefix(["700 Grams", "Eggs 700 Grams"]) == "Eggs 700 Grams"
    # doubled word: "Paw Paw Shredded" really does end with "Paw Shredded"
    assert bi._undo_dropped_prefix(["Paw Shredded", "Paw Paw Shredded"]) == "Paw Paw Shredded"


def test_a_genuine_rename_is_left_alone():
    # THE POINT of the word-suffix rule. A supplier renaming its own product must
    # still show the CURRENT name; a rename shares no suffix with the old name.
    assert bi._undo_dropped_prefix(["Carrot Jumbo", "Carrot Large"]) == "Carrot Jumbo"
    assert bi._undo_dropped_prefix(["Tomatoes Gourmet", "Tomatoes Roma"]) == "Tomatoes Gourmet"
    # single spelling -> unchanged
    assert bi._undo_dropped_prefix(["Broccolini"]) == "Broccolini"


def test_suffix_must_land_on_a_word_boundary():
    # "Green" must not be repaired from "Bean Green" via a mid-word match, and a
    # shorter word that merely ENDS the same must not be swallowed.
    assert bi._word_suffix("Green", "Bean Green") is True
    assert bi._word_suffix("Green", "Evergreen") is False
    assert bi._word_suffix("Red", "Radish Red") is True
    assert bi._word_suffix("Red", "Shredded") is False
    # not a suffix at all
    assert bi._word_suffix("Green", "Green Bean") is False
    # equal / longer fragment is never a suffix of a shorter full name
    assert bi._word_suffix("Carrot Large", "Carrot Large") is False
    assert bi._word_suffix("Carrot Large", "Large") is False


def test_the_repair_reaches_the_feed_and_kills_the_repairable_fragments():
    """
    End-to-end on the committed cost book.

    These nine fragments each had a FULL twin under the SAME supplier code in
    history, so _undo_dropped_prefix can repair them from data we already hold.
    They must never come back.

    The other fragments the morning triage flagged ("Ruby Red", "Baby Gem",
    "Roma", "Pencil", ...) are NOT asserted here, and deliberately so: their code
    only ever carried the truncated spelling, so there is nothing in the cost book
    to repair them FROM. Guessing across codes is exactly the pooling this module
    refuses. They self-heal the next time that product is invoiced, because the
    parser fix means the new row carries the full name and becomes the latest.
    Cost is unaffected either way — pack size is read from raw_uom, not the name.
    """
    if not bi.OUT.exists():
        return
    items = json.loads(bi.OUT.read_text())
    items = items if isinstance(items, list) else items.get("ingredients", items.get("items", []))
    names = {(i.get("description") or "").strip() for i in items if isinstance(i, dict)}
    for fragment in ("Large", "Hass", "Jap", "Sweet", "700 Grams",
                     "Button B Grade", "Pink Peeled", "Red And Green", "Chives"):
        assert fragment not in names, f"{fragment!r} is back in the picker as a product name"


def test_the_repair_collapsed_the_duplicate_picker_entries():
    # The point of restoring real names: two codes for the same product finally
    # merge, so the chef stops seeing the same carrot twice.
    if not bi.OUT.exists():
        return
    items = json.loads(bi.OUT.read_text())
    items = items if isinstance(items, list) else items.get("ingredients", items.get("items", []))
    fft = [i for i in items if isinstance(i, dict) and "Fresh Fruit" in (i.get("supplier") or "")]
    for name in ("Carrot Large", "Onion Spanish"):
        hits = [i for i in fft if (i.get("description") or "").strip() == name]
        assert len(hits) == 1, f"{name!r} appears {len(hits)}x for FFT — the collapse regressed"


# ── the collapse must never delete the dearer pack (2026-08-15) ────────────
# The second collapse pass merges two codes that share a (supplier, name) and its
# tiebreak prefers the CHEAPER row. That was safe only by accident: Fresh Fruit
# Team sells the same herb as a single bunch AND as a MARKET bunch and calls both
# "Herb Chives" on the invoice, and only the old parser bug — which truncated one
# of each pair to "Chives" / "Coriander" — kept the names apart.
#
# Repairing those names made the names match, and this pass promptly dropped both
# market bunches: HCMB $15.40 deleted in favour of HCBCH $2.42 (6x), HCDRMB $7.70
# in favour of HCB $2.64 (3x). Both packs had been confirmed by Zak himself in
# pack_overrides. Caught by diffing the rebuilt feed against the pre-change one.
#
# Merging is now conditional on the money agreeing.

def _feed_by_code():
    feed = _feed()
    out = {}
    for i in feed["ingredients"]:
        c = (i.get("supplier_code") or "").upper()
        if c:
            out.setdefault(c, []).append(i)
    return out


def test_market_bunch_is_not_swallowed_by_the_single_bunch():
    """BOTH packs must survive. That is the whole protection.

    The price comparison below is only made when the two are quoted in the SAME
    unit. Once Zak confirmed a market bunch of chives is 125 g (2026-08-15), HCMB
    became a per-GRAM entry at $123.20/kg while HCBCH is still per BUNCH at
    $2.42 — comparing 0.1232 against 2.42 says nothing at all, and an earlier
    version of this test did exactly that and went red on an improvement.
    """
    by = _feed_by_code()
    for dear, cheap in (("HCMB", "HCBCH"), ("HCDRMB", "HCB")):
        assert dear in by, (
            f"{dear} vanished — the dearer pack was merged away into {cheap}, "
            f"which understates every recipe that uses it")
        assert cheap in by, f"{cheap} missing"
        assert by[dear][0]["id"] != by[cheap][0]["id"], (
            f"{dear} and {cheap} collapsed onto one id")
        if by[dear][0]["pack_unit"] != by[cheap][0]["pack_unit"]:
            continue                       # not comparable; survival is the point
        d = float(by[dear][0]["cost_per_base_unit"])
        c = float(by[cheap][0]["cost_per_base_unit"])
        assert d > c * 1.5, (
            f"{dear} ({d}) and {cheap} ({c}) are supposed to be materially "
            f"different packs; if they have converged this test is now vacuous")


def test_names_stay_unique_even_when_two_packs_share_a_name():
    # The no-duplicate contract still holds: the pass disambiguates with the
    # supplier's own code rather than dropping a row.
    feed = _feed()
    counts = Counter((i["supplier"], i["description"]) for i in feed["ingredients"])
    assert not {k: n for k, n in counts.items() if n > 1}
    # Exactly one of a colliding pair carries the disambiguating code — WHICH one
    # is not pinned. `_rank` prefers a weight/volume entry, so once a market bunch
    # gets a real gram pack it becomes the primary and its per-bunch sibling takes
    # the suffix instead. That flip is an improvement, not a regression.
    by = _feed_by_code()
    if "HCMB" in by and "HCBCH" in by:
        pair = [by["HCMB"][0]["description"], by["HCBCH"][0]["description"]]
        assert sum(("HCMB" in d or "HCBCH" in d) for d in pair) == 1, pair


def test_a_brand_is_appended_when_the_invoice_drops_it():
    """B&E invoices name the category and size but not the brand, so the pizza
    sauce Marilyna's uses on everything read as "Tomato - Pizza Sauce" and could
    not be found by searching the name anyone actually says. Their own catalogue
    calls code 14580 "... #Kau04-4 Kagome"."""
    assert bi.with_brand("B&E", "14580", "Tomato - Pizza Sauce") \
        == "Tomato - Pizza Sauce Kagome"
    # composes, and never doubles up
    assert bi.with_brand("B&E", "14580", "Tomato - Pizza Sauce Kagome") \
        == "Tomato - Pizza Sauce Kagome"
    # a supplier/code with no brand recorded is untouched
    assert bi.with_brand("B&E", "99999", "Something Else") == "Something Else"
    assert bi.with_brand("Foodlink", "14580", "Not B&E's 14580") == "Not B&E's 14580"


def test_same_priced_packs_of_one_product_still_collapse():
    # The pass must keep doing its original job: two packs of the same product at
    # the same $/kg are ONE entry, not two.
    #
    # This used to name the codes — CL20KGBX must survive, CLKG must merge into
    # it — and that stopped being true on 2026-08-18 for a reason that is not a
    # regression: WE STOPPED BUYING THE 20 KG BOX. CL20KGBX has aged out of
    # data/cogs_list.csv entirely and every carrot line is now CLKG per kg.
    #
    # A test that pins which code wins is really pinning a purchasing decision,
    # and it fails the day the kitchen changes supplier or pack. What the pass
    # actually guarantees is the COLLAPSE: one carrot, one entry, whichever code
    # is the live one. That is what this asserts now.
    by = _feed_by_code()
    carrots = {c: v for c, v in by.items() if c in ("CLKG", "CL20KGBX")}
    assert carrots, "no Fresh Fruit Team carrot in the feed at all"
    assert len(carrots) == 1, (
        f"the carrot collapse regressed — both packs are in the feed: {sorted(carrots)}")
    for code, entries in carrots.items():
        assert len(entries) == 1, f"{code} appears {len(entries)} times, not collapsed"
