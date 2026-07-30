"""
Pack-size parsing: the stated-size rules, and the guards that stop a stated
number from being read the wrong way.

Every case here is a real supplier line. The rule of the whole pack layer is:
read what the invoice STATES, never invent what it doesn't. So these tests come
in two halves — sizes we must now read (case formats, litre variants, bare
counts, unit-in-code), and sizes we must still REFUSE (a piece price mistaken
for a case, a size grade mistaken for a pack).

    python3 -m pytest modules/recipes/tests/test_pack_parser.py
"""

from __future__ import annotations

from decimal import Decimal

from modules.recipes.pipeline.build_ingredients import (
    parse_pack, resolve_pack, is_non_food, normalize_code, _better_name)


# ── FFT code/name normalisation (dedupe + proper names) ────────────────────

def test_unit_word_bled_into_code_is_stripped():
    # "AH20T Tray" and "AH20T" are the same product — the unit leaked into the code.
    assert normalize_code("AH20T Tray") == "AH20T"
    assert normalize_code("ONBRKG Kilogram") == "ONBRKG"
    assert normalize_code("HCMB Market") == "HCMB"
    assert normalize_code("TCPUN Punnet") == "TCPUN"


def test_normalize_code_is_idempotent_and_never_empty():
    assert normalize_code("AH20T") == "AH20T"
    assert normalize_code("AH20T Tray") == normalize_code(normalize_code("AH20T Tray"))
    assert normalize_code("Bunch") == "Bunch"      # nothing left to strip -> keep


def test_fuller_name_wins_over_truncated_or_code_like():
    assert _better_name("Hass", "Avocado Hass") == "Avocado Hass"
    assert _better_name("Avocado Hass", "Hass") == "Avocado Hass"
    assert _better_name("BRL Box", "Broccolini") == "Broccolini"      # real words beat a code
    assert _better_name("Brown", "Onion Brown") == "Onion Brown"


# ── non-food consumables must be kept out of the recipe picker ──────────────

def test_non_food_consumables_are_excluded():
    for d in ("NAPKINS - DINNER BROWN", "SPONGE SCOURER -", "STAINLESS STEEL",
              "SAUCE CONTAINER - WITH", "GLOVES NITRILE BLACK L",
              "CHEMICAL - DISHWASHING", "DETERGENT - MACHINE", "SANITISER SPRAY"):
        assert is_non_food(d), f"{d!r} should be excluded as non-food"


def test_real_ingredients_are_not_excluded():
    # Guard the blast radius — none of these must be mistaken for non-food.
    for d in ("FZ BEEF - ANGUS BURGER", "TOMATO - PIZZA SAUCE", "HERB BASIL BCH",
              "SQUID PINEAPPLE CUT IMP U5 5KG", "Chives", "Pizza Box Inserts 22.5cm x 100"):
        assert not is_non_food(d), f"{d!r} is a real ingredient, must not be excluded"


# ── stated sizes we must now read ──────────────────────────────────────────

def test_case_format_reads_the_piece_not_the_case():
    # "20/454gm" is twenty pieces of 454 g; the invoice prices ONE piece.
    qty, unit, how = parse_pack('"Jun" Frozen Soybean (Edamame) 20/454gm')
    assert (qty, unit) == (Decimal("454"), "g")
    assert "piece" in how


def test_case_format_costs_per_piece():
    qty, unit, per, how, bad = resolve_pack(
        '"Jun" Frozen Soybean (Edamame) 20/454gm', Decimal("2.50"), basis="per_unit")
    assert (qty, unit, bad) == (Decimal("454"), "g", None)
    assert per == (Decimal("2.50") / Decimal("454")).quantize(Decimal("0.000001"))


def test_ltr_variant_is_millilitres():
    qty, unit, per, how, bad = resolve_pack(
        "JUICE RUBY GRAPEFRUIT 2LTR", Decimal("5.80"), basis="per_unit")
    assert (qty, unit, bad) == (Decimal("2000"), "ml", None)


def test_bare_count_is_pieces_priced_per_piece():
    # "x 50" with no weight = 50 boxes; $24.10 is the carton, so per box = $0.4820.
    qty, unit, per, how, bad = resolve_pack(
        'B Flute Lock Top 11" Pizza Boxes x 50', Decimal("24.1010"), basis="per_unit")
    assert (qty, unit) == (Decimal("50"), "ea")
    assert per == Decimal("0.482020")
    assert "count" in how


def test_unit_from_supplier_code_when_description_is_bare():
    # Fresh Fruit Team puts the sold unit as the code's last word.
    qty, unit, per, how, bad = resolve_pack(
        "Spanish Peeled", Decimal("5.90"), basis="per_unit", code="KITOSPKG Kilogram")
    assert (qty, unit) == (Decimal("1000"), "g")          # per kg
    assert per == Decimal("0.005900")
    assert how.startswith("code:")


def test_code_bunch_is_a_bunch():
    qty, unit, per, how, bad = resolve_pack(
        "Thyme", Decimal("2.20"), basis="per_unit", code="HTBCH Bunch")
    assert (qty, unit, per) == (Decimal("1"), "bunch", Decimal("2.200000"))


# ── stated numbers we must still refuse to misread ─────────────────────────

def test_size_grade_is_not_a_case_format():
    # "200/300" is a fish size grade, NOT twenty of 300 — it has no unit after it,
    # and the real pack (5KG) must win.
    qty, unit, how = parse_pack("BARRAMUNDI FILLETS IMP 200/300 S/OFF 5KG")
    assert (qty, unit) == (Decimal("5000"), "g")


def test_market_code_word_is_not_a_unit():
    # "Market" states a price basis, not a pack size — must stay confirm-once.
    qty, unit, per, how, bad = resolve_pack(
        "Chives", Decimal("15.40"), basis="per_unit", code="HCMB Market")
    assert qty is None and unit is None
    assert bad


def test_piece_price_that_reads_as_case_is_flagged_not_shipped():
    # The camembert trap: 125GM is one wheel, $45.60 is the box. Parsed per-gram
    # it is $364/kg — absurd — so it must come back flagged, never costed silently.
    qty, unit, per, how, bad = resolve_pack(
        "CHEESE CAMEMBERT 125GM Rosenberg", Decimal("45.60"), basis="per_unit")
    assert bad, "an implausible per-gram cost must be flagged for review"


def test_no_size_anywhere_stays_confirm_once():
    # A truncated B&E line with no size in text, note or code: refuse, don't guess.
    qty, unit, per, how, bad = resolve_pack(
        "FZ BEEF - ANGUS BURGER", Decimal("130.00"), basis="per_unit", note="CTN")
    assert qty is None and bad
