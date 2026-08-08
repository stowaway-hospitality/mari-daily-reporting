"""
The cost book holds most stock items twice. Nothing ever compared the two copies.

THE GAP
-------
Back Office is filed per venue, so one physical bottle, keg or mixer has a
Stowaway ProductID and a Harry Gatos ProductID, and a recipe costs off whichever
its venue's menu was built from. Every existing rule checks a number against
something OUTSIDE the book — ILG's published price list, the invoice, the same
supplier code's own history. None of them can see that the book holds one item at
two prices, because each copy is internally consistent and neither is an outlier
on its own. So these survived every sweep:

    Angostura Bitters   lightspeed:20747514  $1.34 a 200 ml bottle  (Harry Gatos)
                        lightspeed:20487270  $20.89 a 200 ml bottle (Stowaway)

      14.93x apart on the cost book's own latest rates. ILG's price book lists
      390-021-0 "Angostura Bitters 200ml 12pk R" at $15.10 a bottle and their
      invoices bill $17.31-$17.45; ILG's book is one of the two prices in this
      system that neither we nor Lightspeed derived. $1.34 is 8.9% of it. That is
      not a discount, it is a keying error, and four live Harry Gatos cocktails
      cost off it — Manhattan - Perfect, Manhattan - Dry, Mai Tai, Dark & Stormy.

    Plantation 3 Stars  both copies seeded at the same $60.83, but Harry Gatos
                        states 4500 ml and Stowaway 700 ml — one bottle read as a
                        case, 6.43x low.

Both cheap copies read LOW, which is the direction nobody investigates.

WHAT THE EVIDENCE DOES NOT SETTLE
---------------------------------
Two of the three cases the finding raised are NOT fixed by this rule and it says
so rather than pretending:

  * Rooster Rojo. Stowaway's bottle (20483410) is seeded at $51.6617/700 ml,
    which ILG's own invoice confirms to four decimal places ($309.97 a 4.2 L case
    = $0.073802/ml; the 2026-07-14 per-bottle line reads $51.6611). Harry Gatos'
    copy (20744471) sits at $45.83/700 ml — 11.3% under, on 5 recipes. The names
    differ by a word ("Rooster Rojo Blanco Tequila" vs "Rooster Blanco Tequila")
    so no normaliser pairs them, and 11.3% is inside the band two separate ILG
    accounts genuinely produce.

  * Harry Gatos' "Alehouse Premium Lager" (20744549, 5 products including Harry's
    Lager) is seeded at $185.00. ILG bill 122-2858 ALEHOUSE PREMIUM KEG at
    $212.44 and 122-2867 ALEHOUSE CRISP KEG at $184.94 — so the Premium product
    carries the Crisp price, 12.9% low. Every ILG keg invoice in the data is
    billed to Stowaway, so nothing here can say WHICH of the two is wrong, the
    name or the price. Guessing would be inventing a number. It needs Zak.

WHAT THIS GUARDS
----------------
- one stock item written two ways is recognised as one
- two STATED pack sizes are NOT (bulk salt really is cheaper per gram)
- units are never crossed
- the band is wide enough that two venues buying separately is not a finding
- and the invariant on the real book: the Angostura pair is reported, with the
  recipe count that says why it matters
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from audit_book import (TWIN_IDENTITY_X, bo_product_names,          # noqa: E402
                        cost_book_latest, stock_item,
                        twin_identity_conflicts)

ANGOSTURA_HG, ANGOSTURA_STOW = "lightspeed:20747514", "lightspeed:20487270"


# --- what counts as one stock item ----------------------------------------

def test_the_two_venues_spellings_of_one_bottle_are_one_stock_item():
    """Stowaway brackets the container, Harry Gatos dashes it. Same bottle."""
    assert stock_item("Angostura Bitters [200ml]")[0] == \
        stock_item("Angostura Bitters - Bottle 200ml")[0]


def test_the_stated_size_is_kept_so_two_pack_sizes_stay_two_products():
    """Olssons salt is bought as a 10 kg sack and a 1 kg shaker. The $/g differ
    7x for the ordinary reason that bulk is cheaper, and reporting that as a
    defect is how a work queue becomes wallpaper."""
    big, small = stock_item("Salt Cooking 10kg Olssons"), stock_item("Salt Cooking 1kg Olssons")
    assert big[0] == small[0]           # same item...
    assert big[1] != small[1]           # ...but the sizes are stated and differ


def test_a_stated_size_difference_suppresses_the_finding():
    latest = {"lightspeed:1": ("2026-01-01", 0.0008, "g"),
              "lightspeed:2": ("2026-01-01", 0.005578, "g")}
    names = {"1": "Salt Cooking 10kg Olssons", "2": "Salt Cooking 1kg Olssons"}
    assert twin_identity_conflicts(latest, names) == []


def test_units_are_never_crossed():
    """A per-ml copy against a per-each one is a unit question, and other rules
    own it. Comparing the numbers would produce a meaningless ratio."""
    latest = {"lightspeed:1": ("2026-01-01", 0.0067, "ml"),
              "lightspeed:2": ("2026-01-01", 20.89, "ea")}
    names = {"1": "Angostura Bitters", "2": "Angostura Bitters"}
    assert twin_identity_conflicts(latest, names) == []


# --- the band --------------------------------------------------------------

def test_two_venues_buying_separately_is_not_a_finding():
    """13 of the 17 name-matched pairs in the real book sit between 1.10x and
    1.25x. Stowaway and Harry Gatos are on separate ILG accounts and the two
    exports were taken on different days; that band is real buying."""
    assert TWIN_IDENTITY_X > 1.25
    latest = {"lightspeed:1": ("2026-01-01", 0.034457, "ml"),      # HG Aperol
              "lightspeed:2": ("2026-01-01", 0.041543, "ml")}      # stow Aperol
    names = {"1": "Aperol", "2": "Aperol"}
    assert twin_identity_conflicts(latest, names) == []


def test_a_copy_at_a_fraction_of_the_other_is():
    latest = {ANGOSTURA_HG: ("2026-01-01", 0.0067, "ml"),
              ANGOSTURA_STOW: ("2026-01-02", 0.1000, "ml")}
    names = {"20747514": "Angostura Bitters - Bottle 200ml",
             "20487270": "Angostura Bitters [200ml]"}
    found = twin_identity_conflicts(latest, names)
    assert len(found) == 1
    ratio, members = found[0]
    assert 14.0 < ratio < 16.0
    assert members[0][1] == ANGOSTURA_HG          # cheapest first: the suspect one
    assert members[-1][1] == ANGOSTURA_STOW


# --- the invariant on the real book ---------------------------------------

def test_the_angostura_pair_is_reported_on_the_real_book():
    """The regression. Before this rule existed nothing in the repo said that a
    200 ml bottle of Angostura is costed at $1.34 at one venue and $20.89 at the
    other, and no other rule can: each copy is self-consistent."""
    latest = cost_book_latest()
    if not latest:
        return                     # clean checkout: nothing generated yet
    hits = [m for _r, m in twin_identity_conflicts(latest, bo_product_names())
            if any(i == ANGOSTURA_HG for _c, i, _n, _d, _u in m)]
    assert hits, "the Angostura twin identity is no longer reported"
    assert any(i == ANGOSTURA_STOW for _c, i, _n, _d, _u in hits[0])


def test_the_rule_stays_a_short_list_a_human_will_actually_read():
    """A rule that reports 60 pairs reports nothing. Measured today: 7."""
    latest = cost_book_latest()
    if not latest:
        return
    found = twin_identity_conflicts(latest, bo_product_names())
    assert 1 <= len(found) <= 20, [f"{r:.2f}x {m[0][2]}" for r, m in found]
