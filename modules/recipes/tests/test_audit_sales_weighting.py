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
    # ANY severity, not SEVERE specifically. SEVERE reached ZERO on 2026-08-17
    # and this fixture-sanity line then asserted the book still had defects --
    # a test that fails when the thing it guards succeeds. What it actually
    # guards is that a finding on a SELLING product states its volume, and that
    # is true of every severity.
    live = [d for (sev, _r), items in F.items() if sev in ("SEVERE", "WARN")
            for rev, d in items if rev > 0]
    assert live, "fixture sanity: some findings are on selling products"
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
    been weighed, and saying so names the one action that clears them.

    THE DEFECT IS NOW FIXED IN THE DATA, so this can no longer assert against
    the live book: on 2026-08-19 the converter began lifting a large to
    regular / 0.716 wherever it fell below a weighed regular, and the rule
    correctly reports nothing. A test whose "fixture sanity" check is a live
    defect stops being a test the day someone fixes it — and the thing worth
    guarding was never the defect, it was whether the MESSAGE names which of
    the two numbers is a measurement.

    So it is asked directly, of the function that decides.
    """
    from audit_book import _weighed_regular_annotation

    # a regular quantity that is on Zak's weighed sheet -> say so.
    # 10 g, not the 33 g this test used to assert: pizza_portions.yaml (v2,
    # 2026-08-19) weighed it and the old regular-only sheet was badly out.
    assert "WEIGHED" in (_weighed_regular_annotation(
        "Sanchez", "Spanish Onion [10Kg]", 10.0) or "")
    # ...and one that is not -> claim nothing
    assert not (_weighed_regular_annotation(
        "Sanchez", "Some Topping Nobody Weighed", 7.0) or "")

    # the live book must now be CLEAN of the finding, which is the real proof
    # What survives is a topping the sheet does NOT cover — basil pesto, where
    # Produce still holds large 30 g against regular 40 g and nobody has weighed
    # either. That is a real question for the kitchen, and the rule reporting it
    # is the rule working.
    F = audit()
    items = [d for (_s, rule), v in F.items() if "LARGE carries LESS" in rule
             for _rev, d in v]
    for d in items:
        assert "pesto" in d.lower(), f"an unexpected size disagreement: {d}"


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


def test_the_retracted_pos_cost_claim_does_not_come_back():
    """"The POS cost column is ~3.6x low, on everything" was wrong. It was
    measured on products_weekly.csv, where 3,767 of 5,018 rows in the window
    carry a cost of exactly zero because the Looker backfill has no costs;
    summing those against real recipe costs manufactures a 0.28x ratio.

    On the daily exports — what the P&L actually reads — Lightspeed agrees with
    our book to 0.96x where it states a cost at all. The defect is that 17.5% of
    revenue has no cost stated, which is what the rule measures now."""
    F = audit()
    rules = {rule for (_sev, rule) in F}
    assert not [r for r in rules if "POS cost column" in r], rules
    assert [r for r in rules if "booked at 100% GP" in r], \
        "the real measurement must be reported"


def test_the_hundred_percent_gp_measure_reads_the_daily_exports():
    """Not products_weekly. That file's cost column is incomplete and has now
    produced two retracted claims."""
    F = audit()
    hits = [d for (_s, rule), v in F.items() if "booked at 100% GP" in rule
            for _rev, d in v]
    assert any("of $" in d and "%" in d for d in hits), hits[:3]
    assert any("product-days" in d for d in hits), "measured per product per DAY"


def test_every_remaining_severe_is_something_nobody_has_fixed_yet():
    """A short SEVERE list is only useful if everything on it is real."""
    F = audit()
    severe = [(rule, d) for (sev, rule), v in F.items() if sev == "SEVERE"
              for _rev, d in v]
    assert len(severe) < 20, f"{len(severe)} SEVERE — the list has stopped being a list"


def test_a_batch_masquerading_as_a_serve_is_named_as_one():
    """Potato Salad's Produce recipe is a kilo of potato and half a kilo of
    Kewpie against a $7.00 side. Reported as "negative GP" and "costs more than
    it sells for", it sends someone to fix the price, and the price is not what
    is wrong.

    THIS TEST HAS OUTLIVED ITS FIRST TWO FORMS, and the reason is worth keeping.

      v1  asserted the finding said "recipe is a BATCH, not a serve".
      v2  widened to accept "a serve portion is declared" too, when Zak ruled the
          portion on 2026-08-17 and audit_book started dividing.
      v3  is this one. On 2026-08-20 the CONVERTER started dividing as well, so
          the book itself now holds a 130 g serve costing $1.00 at 84.2% GP.
          Potato Salad is no longer batch-shaped, so it raises no finding at all
          and both earlier forms fail — a test that pinned the defect expiring
          the day the defect was fixed, which is the trap this repo has fallen
          into three times in one week.

    So the assertion is now the thing that was always actually true and never
    written down: NOBODY MAY REPORT POTATO SALAD AS NEGATIVE GP. Whether that is
    achieved by naming it a batch, by declaring a portion, or by the book
    dividing the tray is an implementation detail. Being told a $7.00 side loses
    money when it does not is the failure.
    """
    F = audit()
    for rule_frag in ("negative GP", "costs more than it sells for",
                      "BATCH, not a serve"):
        clashing = [d for (_s, rule), v in F.items() if rule_frag in rule
                    for _rev, d in v if "Potato Salad" in d]
        assert not clashing, (
            f"Potato Salad is a declared 130 g serve costing about $1.00 — "
            f"nothing should still be reporting it as {rule_frag!r}: {clashing}")

    # ...and the division must actually be in the book, not only in the audit.
    # This is the half that was missing until 2026-08-20: serve_portions.yaml was
    # read by audit_book alone, so the finding closed while our_cost went on
    # holding the whole tray at -55.1% GP.
    import json
    from pathlib import Path
    book_p = (Path(__file__).resolve().parents[3]
              / "data" / "lightspeed_recipes_costed.json")
    if book_p.exists():
        r = json.loads(book_p.read_text())["recipes"].get("Potato Salad")
        if r:
            assert r.get("serve_portion"), (
                "Potato Salad carries a declared portion in "
                "data/serve_portions.yaml and the costed book does not show it "
                "— the converter has stopped reading the declaration")
            assert float(r["our_cost"]) < float(r["sell_incl"]), (
                f"a declared serve still costs more than it sells for: "
                f"{r['our_cost']} against {r['sell_incl']}")


def test_a_real_serve_is_never_called_a_batch():
    """1,006 g of Super House Special and a 1,000 ml jug are real menu items."""
    from audit_book import SERVE_MAX_BASE_UNITS
    assert SERVE_MAX_BASE_UNITS > 1006
    F = audit()
    batch = [d for (_s, rule), v in F.items() if "BATCH, not a serve" in rule
             for _rev, d in v]
    for real in ("Super House Special", "Jug", "Burrito", "Unlimited Dumplings"):
        assert not [d for d in batch if real in d], real


def test_an_ingredient_problem_no_recipe_can_reach_is_not_a_warning():
    """All 75 "pack size unconfirmed" ingredients are referenced by exactly zero
    recipes. At WARN they sat at the top of the list, ahead of everything that
    does misstate money. They still matter — a chef building a new recipe would
    meet them — so they stay, at INFO, saying which they are.

    Asserted as a classification, not a count: these rules read
    data/ingredients.json, which is deliberately not committed, so on a clean
    checkout there is nothing to classify. Requiring the finding to EXIST is the
    same trap that took CI down earlier today — a test that passes on a
    developer's tree because a generated file happens to be sitting there."""
    F = audit()
    for sev, rule in F:
        # ONLY the variant no recipe can reach. There are TWO rules with this
        # prefix and they say opposite things: "pack size unconfirmed" (INFO,
        # nothing is mispriced today) and "...and a recipe uses it" (WARN,
        # something is). Matching the prefix caught both, so the moment a real
        # recipe reached an unconfirmed pack — Southern Squid, once its authored
        # recipe stopped being silently dropped on 2026-08-19 — this demanded
        # the audit call a LIVE problem INFO.
        if ("pack size unconfirmed" in rule
                and "a recipe uses it" not in rule):
            assert sev == "INFO", f"{sev}: {rule}"
            assert "no recipe uses it" in rule, rule


def test_the_100pc_gp_number_is_split_into_work_someone_can_do():
    """$45,370 at 100% GP reads as one problem. It is three, and they need
    different work: a fee has no food cost and never will, a deal contains real
    product whose contents are not declared anywhere, and everything else is a
    dish nobody has written a recipe for. The patterns are triage labels only —
    they change no figure."""
    F = audit()
    lines = [d for (_s, rule), v in F.items() if "booked at 100% GP" in rule
             for _rev, d in v]
    split = [d for d in lines if "SPLIT:" in d]
    assert len(split) == 1, split
    for phrase in ("dishes with no recipe", "deals whose contents are undeclared",
                   "fees that have no food cost"):
        assert phrase in split[0], phrase


def test_the_pricing_pages_pack_read_movers_are_gone():
    """WAS: this asserted the DEFECT. The /pricing page's top entries were
    Pellegrino +2300% and Coca Cola +1100% — exactly 24.00x and 12.00x, one
    invoice pricing the bottle and the next pricing the case. Aperol went $30.11
    a bottle to $174.50 a case, which is $29.08 a bottle: a 3% FALL shown as a
    480% rise. The page exists so a supplier creeping prices up gets noticed the
    week it happens, and those three were drowning it.

    NOW: the ILG parser reads the pack as ONE unit of whatever qty counts
    (ilg.one_unit_pack), the last six invoices that predated the fix have been
    re-parsed, and none of the three is a mover at all. So this guards the FIX —
    re-break the pack reading and these come back and it reds.

    A single ingredient's per-unit price does not double between two deliveries
    from the same supplier."""
    F = audit()
    flagged = [d for (_s, rule), v in F.items() if "pack-size change" in rule
               for _rev, d in v]
    if not flagged:
        return          # compare.json is generated; absent on a clean checkout
    for name in ("Pellegrino", "Coca Cola", "Aperol"):
        assert not any(name in d for d in flagged), (
            f"{name} is a pack-size mover again — the price and the pack have "
            f"stopped describing the same unit: {[d for d in flagged if name in d]}")
