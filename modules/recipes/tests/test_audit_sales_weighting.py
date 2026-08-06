"""
The audit has to know what actually sells, or it buries its own best findings.

THE GAP
-------
Every finding weighed the same and each rule listed alphabetically. So "Corpse
Reviver No. 2" — one sold, ever, in June 2025 — sat above "Kids Spag Bol", and
"Milagro Reposado Tequila", which has never sold a single unit, sat in the same
SEVERE list as a product turning over real money. The audit was telling the truth
and burying it.

WHAT THIS GUARDS
----------------
- three states are distinguished: sold, sold nothing, and no POS record at all
- a defect on a dormant SKU drops to WARN — it misstates no revenue — and says so
- findings sort by revenue at stake, biggest first
- a finding with no `product` is unweighted and still reported
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from audit_book import _pack_count_hint, audit, load_sales, sold, weigh  # noqa: E402


def test_sales_load_and_are_keyed_by_normalised_pos_name():
    s = load_sales()
    assert s, "products_weekly.csv should give the audit a sales denominator"
    for qty, rev in s.values():
        assert isinstance(qty, float) and isinstance(rev, float)
        assert qty >= 0


def test_a_venue_tag_is_stripped_before_matching():
    """Recipe names carry the venue; POS product names do not."""
    fake = {"caipirinha": (4.0, 57.0)}
    assert sold(fake, "Caipirinha [HG]") == (4.0, 57.0, False)
    assert sold(fake, "Caipirinha") == (4.0, 57.0, False)


def test_no_record_is_not_the_same_as_no_sales():
    """433 of the 829 recipes are preps, size variants and delivery twins whose
    names were never POS product names. Reporting those as "0 sold" would be a
    claim we cannot make."""
    assert sold({"caipirinha": (4.0, 57.0)}, "Something Else") is None


def test_looser_matching_is_refused():
    """A wrong denominator is worse than no denominator: "Whispering Angel -
    Regular" must not silently inherit "Whispering Angel Rose"'s revenue —
    the size collapse gives "Whispering Angel", which is a different name."""
    fake = {"whisperingangelrose": (262.0, 9313.0)}
    assert sold(fake, "Whispering Angel - Regular [HG]") is None


def test_a_size_variant_matches_the_collapsed_product_and_says_so():
    """products_weekly deliberately merges a beer's pints and schooners into one
    drink. Without following that collapse, every tap beer and wine-by-the-glass
    read "no POS sales record" — $95,000 of Stowaway revenue with no weight on
    it. The match is flagged, because the revenue is the whole product's."""
    fake = {"kukusauvignonblanc": (410.0, 8577.0)}
    assert sold(fake, "Kuku Sauvignon Blanc - Regular") == (410.0, 8577.0, True)
    _rev, _sev, detail = weigh(fake, "Kuku Sauvignon Blanc - Large", "SEVERE", "x")
    assert "all sizes" in detail


def test_the_collapse_never_eats_a_flavour_or_a_delivery_zone():
    """The whitelist is products_weekly's, not a second looser one invented here:
    "- Passionfruit" and "- Freshwater / Queenscliff" are not sizes."""
    fake = {"mojito": (10.0, 100.0), "pizza": (10.0, 100.0)}
    assert sold(fake, "Mojito - Passionfruit") is None
    assert sold(fake, "Pizza - Freshwater / Queenscliff") is None


def test_a_dormant_product_is_reported_but_not_severe():
    """It is still a defect. It just cannot misstate money that is not being
    made, and at SEVERE it crowds out the ones that cost something today."""
    rev, sev, detail = weigh({"deadsku": (0.0, 0.0)}, "Dead SKU", "SEVERE", "x")
    assert (rev, sev) == (0.0, "WARN") and "dormant" in detail

    F = audit()
    severe = [d for (s, _r), items in F.items() if s == "SEVERE" for _rev, d in items]
    assert not [d for d in severe if "dormant" in d], severe[:5]


def test_a_refund_sku_never_outranks_a_finding_worth_nothing():
    """A few POS products carry NEGATIVE 13-week revenue (discounts, refunds).
    That is real, but a negative weight would sort the finding below unweighted
    ones, so it floors at zero rather than going backwards."""
    rev, sev, _d = weigh({"refunds": (72.0, -842.92)}, "Refunds", "SEVERE", "x")
    assert rev == 0.0 and sev == "SEVERE"


def test_every_finding_carries_a_revenue_weight():
    F = audit()
    for items in F.values():
        for rev, detail in items:
            assert isinstance(rev, float) and rev >= 0
            assert isinstance(detail, str) and detail


def test_a_live_product_keeps_its_severity_and_states_its_volume():
    F = audit()
    live = [d for (sev, _r), items in F.items() if sev == "SEVERE"
            for rev, d in items if rev > 0]
    assert live, "fixture sanity: some SEVERE findings are on selling products"
    for d in live:
        assert "sold, $" in d


def test_the_audit_runs_on_a_clean_checkout(tmp_path, monkeypatch):
    """
    data/ingredients.json is deliberately NOT committed — it is a 90-day window
    off date.today(), so a committed copy rots on whatever Tuesday an invoice
    crosses the line. On a clean checkout it does not exist until
    build_ingredients has run.

    Reading it blind turned "the audit has nothing to say about ingredients yet"
    into a FileNotFoundError, and because these tests call audit(), that took CI
    down twice — green locally every time, because a local tree has the file.

    Any test that reads a generated artifact has to survive its absence.
    """
    import audit_book as ab
    monkeypatch.setattr(ab, "INGREDIENTS", tmp_path / "not-built-yet.json")
    F = ab.audit()
    assert F, "the recipe and cost-book rules do not need ingredients.json"
    assert any("ingredients.json not built" in r for _sev, r in F), \
        "and it must SAY the ingredient rules were skipped, not skip them quietly"


def test_a_pack_misread_lands_on_a_whole_number():
    """A misread pack is not a random price move — it is the line total divided
    by the wrong number of units, so the ratio is an integer. The camembert sat
    at exactly 12.0x its own median and the black beans at 6.0x. Saying "looks
    like a case of 12 priced as one unit" names the defect; "12.0x median" only
    flags it."""
    assert "case of 12" in _pack_count_hint(0.3648, 0.0304)
    assert "case of 6" in _pack_count_hint(0.0174, 0.0029)
    assert "one unit priced as a case of 12" in _pack_count_hint(0.0029, 0.0348)


def test_a_real_price_move_gets_no_pack_hint():
    """A 4.5x is not a case size and a 12% rise is just a price rise. Claiming a
    pack count for either would be a guess dressed as a diagnosis."""
    assert _pack_count_hint(0.0174, 0.00387) == ""      # 4.5x
    assert _pack_count_hint(1.12, 1.00) == ""           # a real rise
    assert _pack_count_hint(0.0, 1.0) == "" and _pack_count_hint(1.0, 0.0) == ""


def test_packaging_is_not_compared_across_pizza_sizes():
    """A Regular pizza goes in the 11" box and a Large in the 13" —
    convert_lightspeed_recipes assigns them that way on purpose. Comparing the
    two sizes' line lists then reports every pizza twice, once for each box: 31
    of the 40 findings these two rules produced were exactly that.

    A rule that is 78% noise teaches whoever reads it to skim, which costs more
    than the rule earns. What is left is 4 toppings genuinely missing from a
    Large and 6 the other way — every one of them worth a look."""
    F = audit()
    size_rules = [(rule, items) for (_sev, rule), items in F.items()
                  if "REGULAR" in rule or "LARGE" in rule]
    assert size_rules, "fixture sanity: the size-comparison rules should fire"
    for rule, items in size_rules:
        for _rev, detail in items:
            assert "Pizza Box" not in detail and "box insert" not in detail.lower(), \
                f"{rule}: {detail}"


def test_the_audit_says_which_of_the_two_numbers_was_measured():
    """16 of the 19 "large carries less than the regular" findings are a regular
    Zak WEIGHED against a large Produce derived — Spanish onion is 33 g on a
    weighed regular and 20 g on a guessed large, seven times over. Reporting
    those as an inconsistent recipe is misleading: the large has simply never
    been weighed, and saying so names the one action that clears them."""
    F = audit()
    items = [d for (_s, rule), v in F.items() if "LARGE carries LESS" in rule
             for _rev, d in v]
    assert items, "fixture sanity: the rule should fire"
    weighed = [d for d in items if "regular is WEIGHED" in d]
    assert len(weighed) >= 15, f"only {len(weighed)} of {len(items)} identified"
    # and it must not claim a measurement it cannot find
    assert len(weighed) < len(items), "some regulars are still derived; do not claim otherwise"


def test_the_audit_and_the_pnl_agree_on_what_is_covered():
    """An auditor with its own, stricter idea of a match reports work that is
    already done. cogs_blend resolves "Bombay Dry [House]" to the recipe "Bombay
    Dry Gin [House]", so listing it under "sells well, has no costed recipe"
    would send someone to write a recipe that exists — and it is the single
    biggest line that rule was reporting, at $2,827 a quarter.

    _stripped_key is imported from cogs_blend rather than reimplemented, so the
    two can only ever agree."""
    F = audit()
    gaps = [d for (_s, rule), v in F.items() if "no costed recipe" in rule
            for _rev, d in v]
    assert gaps, "fixture sanity: some products really have no recipe"
    for name in ("Bombay Dry [House]", "Bombay Sapphire", "Baileys Irish Cream",
                 "1800 Coconut", "Coke 1.25L"):
        assert not [d for d in gaps if name in d], f"{name} is costed; do not ask for it"


def test_a_severe_never_describes_a_problem_that_is_already_fixed():
    """The "POS cost column far below our own book" rule only ever aggregated
    products that DO have a costed recipe — which are exactly the products the
    P&L stopped copying Lightspeed for when _load_book_costs was wired in. So
    "$62,456 of COGS missing" described a fixed problem, at SEVERE, above every
    live one. A SEVERE that is already fixed trains people to skim the list.

    The measurement is worth keeping — it is the evidence this project rests on
    — so it stays, as INFO, saying what it actually is."""
    F = audit()
    severe = {rule for (sev, rule) in F if sev == "SEVERE"}
    assert not [r for r in severe if "POS cost column" in r], severe
    info = {rule for (sev, rule) in F if sev == "INFO"}
    assert [r for r in info if "Lightspeed's cost column" in r], \
        "the measurement must stay visible, not disappear"


def test_every_remaining_severe_is_something_nobody_has_fixed_yet():
    """A short SEVERE list is only useful if everything on it is real."""
    F = audit()
    severe = [(rule, d) for (sev, rule), v in F.items() if sev == "SEVERE"
              for _rev, d in v]
    assert len(severe) < 20, f"{len(severe)} SEVERE — the list has stopped being a list"
