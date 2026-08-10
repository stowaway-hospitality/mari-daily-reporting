"""Par model v3 guards.

The guarantees that must never regress:
  1. the coverage gate fails when a live Classic/Signature cocktail has no recipe;
  2. hard overrides are honoured — a min is never lowered, a zero is forced to 0,
     a max is never exceeded, a hold pins exactly, a reserve is ADDITIVE;
  3. sanity — a margarita's qty reaches Rooster and Rooster stays >= its reserve;
  4. v3 shrinkage — a consistent negative variance raises a par, the 50% cap
     works, and a SKU absent from a count is never read as a zero loss;
  5. v3 (R,S) — a higher service level means a higher par, low movers take the
     Poisson path, and sigma scales with the square root of exposure;
  6. v3 calendar — a holiday Monday stretches the exposure, a holiday Friday
     removes a delivery, and the Christmas 2026 chain is 14 days / ~2x normal;
  7. v3 seasonality — a summer-peaking SKU indexes higher in Dec than in Jun and
     a thin SKU falls back to its reporting-group index;
  8. POS<->par NAME DRIFT — an aliased till line reaches its par SKU, reaches it
     exactly once, and the unattributed-volume gate fails the build when
     stock-bearing drink volume reaches no par SKU at all. This is the bug class
     that cost $54,794 of Stowaway drink sales over 13 weeks: the till called it
     'Petits Detours Rosé' and 'Fresh is Best Lager', the Purchase module called
     it 'Petits Detours Rosé Mediterranee - Bottle' and 'Alehouse Draught Lager
     [Keg]', and every drop of that volume was thrown away in silence.
"""
import math
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.par import bookings as bookings_mod  # noqa: E402
from modules.par import calendar as par_calendar  # noqa: E402
from modules.par import model  # noqa: E402
from modules.par import seasonal as seasonal_mod  # noqa: E402
from modules.par import service as service_mod  # noqa: E402
from modules.par import shrinkage as shrinkage_mod  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


# ── 1. coverage gate ────────────────────────────────────────────────────────
def test_coverage_gate_fails_when_cocktail_lacks_recipe():
    week = "2026-08-09"
    rows = [
        {"venue": "stow", "reporting_group": "Cocktails - Classic",
         "product_name": "Nonexistent Test Cocktail", "week_ending": week, "qty": 12.0},
        {"venue": "stow", "reporting_group": "Cocktails - Classic",
         "product_name": "$21 Custom Cocktail", "week_ending": week, "qty": 9.0},
    ]
    matcher = lambda name: None  # nothing resolves
    gaps = model.coverage_gap(rows, "stow", matcher, {}, [week])
    names = [n for n, _ in gaps]
    assert "Nonexistent Test Cocktail" in names          # real miss is caught
    assert "$21 Custom Cocktail" not in names            # open-price custom excused


def test_coverage_gate_passes_when_recipe_present():
    week = "2026-08-09"
    rows = [{"venue": "stow", "reporting_group": "Cocktails - Signature",
             "product_name": "Testini", "week_ending": week, "qty": 5.0}]
    matcher = lambda name: "testini"
    gaps = model.coverage_gap(rows, "stow", matcher, {"testini": {"x": 1.0}}, [week])
    assert gaps == []


def test_live_coverage_gate_currently_passes():
    """The real committed data must satisfy the gate (build would exit nonzero)."""
    for venue in ("stow", "hg"):
        _, meta = model.compute_venue(venue, DATA)
        assert meta["coverage_gaps"] == [], f"{venue}: {meta['coverage_gaps']}"


# ── 2. hard overrides ───────────────────────────────────────────────────────
def test_hard_min_never_lowered():
    assert model.apply_override(5.0, {"type": "min", "value": 40.0}) == 40.0   # raised
    assert model.apply_override(55.0, {"type": "min", "value": 40.0}) == 55.0  # not lowered


def test_hard_zero_forces_zero():
    assert model.apply_override(99.0, {"type": "zero", "value": None}) == 0.0


def test_hard_max_never_exceeded():
    assert model.apply_override(99.0, {"type": "max", "value": 10.0}) == 10.0  # capped
    assert model.apply_override(3.0, {"type": "max", "value": 10.0}) == 3.0    # left alone


def test_hard_hold_pins_exactly():
    assert model.apply_override(7.3, {"type": "hold", "value": 1.0}) == 1.0


def test_reserve_is_additive():
    # reserve = physical drum float ADDED on top of modelled cover, not a floor
    assert model.apply_override(12.0, {"type": "reserve", "value": 21.0}) == 33.0
    assert model.apply_override(0.0, {"type": "reserve", "value": 21.0}) == 21.0


def test_rooster_reserve_is_additive_in_full_build():
    recs, _ = model.compute_venue("stow", DATA)
    rooster = recs["Rooster Rojo Blanco Tequila [Bottle]"]
    ov = rooster["override"]
    assert ov and ov["type"] == "reserve" and ov["value"] == 21.0
    # par = 21-bottle physical drum reserve + modelled weekly cover; never below
    # the reserve, and strictly above it because margaritas draw Rooster.
    assert rooster["rec_par"] >= 21.0
    assert rooster["rec_par"] > 21.0


# ── 3. sanity: margarita consumption reaches Rooster ─────────────────────────
def test_margarita_qty_reaches_rooster_consumption():
    id2name, bo_meta = model.load_bo(DATA, "stow")
    scrape = model.load_scrape(DATA, "stow")
    overrides = model.load_overrides(DATA, "stow")
    leaves_by_norm, recipe_norms = model.load_recipes(DATA, "stow")
    idx = model.ParIndex(scrape, overrides, bo_meta)
    matcher = model.build_recipe_matcher(recipe_norms)
    weeks = ["2026-08-09"]
    rows = [{"venue": "stow", "reporting_group": "Cocktails - Classic",
             "product_name": "Classic Margarita", "week_ending": "2026-08-09", "qty": 100.0}]
    pour, recipe = model._build_consumption(rows, "stow", idx, leaves_by_norm, matcher, id2name, weeks)
    rooster = "Rooster Rojo Blanco Tequila [Bottle]"
    assert rooster in recipe
    # 100 margaritas * 40ml / 700ml = ~5.71 bottles
    assert recipe[rooster][0] > 0
    assert recipe[rooster][0] == pytest.approx(100 * 40 / 700, rel=0.05)


def test_rooster_recipe_driver_positive_on_real_data():
    recs, _ = model.compute_venue("stow", DATA)
    assert recs["Rooster Rojo Blanco Tequila [Bottle]"]["drivers"]["recipe_wk"] > 0


# ── shared: the real build, computed once ───────────────────────────────────
@pytest.fixture(scope="module")
def stow_build():
    return model.compute_venue("stow", DATA)


# ── 4. shrinkage ────────────────────────────────────────────────────────────
def _period(start, end, losses):
    return {"start": date.fromisoformat(start), "end": date.fromisoformat(end),
            "weeks": (date.fromisoformat(end) - date.fromisoformat(start)).days / 7.0,
            "losses": losses, "names": {}}


def test_shrinkage_consistent_negative_variance_raises_the_par():
    """A SKU bleeding 2 units a week must end up with a bigger par than the same
    SKU with a clean count history. This is the whole point of the module."""
    weeks = [f"2026-0{m}-0{d}" for m, d in ((1, 4), (1, 11), (1, 18), (1, 25))]
    weeks = ["2026-01-04", "2026-01-11", "2026-01-18", "2026-01-25"]
    consumption = {"BLEEDER": [10.0] * 4, "CLEAN": [10.0] * 4}
    periods = [
        _period("2026-01-04", "2026-01-11", {"1": 2.0, "2": 0.0}),
        _period("2026-01-11", "2026-01-18", {"1": 2.0, "2": 0.0}),
        _period("2026-01-18", "2026-01-25", {"1": 2.0, "2": 0.0}),
    ]
    sku_of_pid = {"1": "BLEEDER", "2": "CLEAN"}.get
    est = shrinkage_mod.estimate(periods, sku_of_pid, weeks, consumption,
                                 lambda s: "Test")
    assert est["BLEEDER"]["loss_per_week"] > 0
    assert est["CLEAN"]["loss_per_week"] < est["BLEEDER"]["loss_per_week"]

    # ...and that feeds through the (R,S) engine as a strictly bigger par.
    par_bleed, _ = service_mod.order_up_to(
        10.0, 0.3, 10.0, 10.0, "standard",
        shrink_fraction=est["BLEEDER"]["loss_fraction"])
    par_clean, _ = service_mod.order_up_to(
        10.0, 0.3, 10.0, 10.0, "standard",
        shrink_fraction=est["CLEAN"]["loss_fraction"])
    assert par_bleed > par_clean


def test_shrinkage_cap_holds_at_half_of_demand():
    """A SKU 'losing' 3x its demand is a data problem. Cap it and flag it."""
    weeks = ["2026-01-04", "2026-01-11", "2026-01-18"]
    consumption = {"MAD": [1.0] * 3}
    periods = [
        _period("2026-01-04", "2026-01-11", {"1": 5.0}),
        _period("2026-01-11", "2026-01-18", {"1": 5.0}),
    ]
    est = shrinkage_mod.estimate(periods, {"1": "MAD"}.get, weeks, consumption,
                                 lambda s: "Test")
    row = est["MAD"]
    assert row["capped"] is True
    assert row["investigate"] is True
    assert row["loss_per_week"] <= 0.5 * row["modelled_demand_wk"] + 1e-9
    assert row["loss_fraction"] <= shrinkage_mod.MAX_UPLIFT_FRACTION + 1e-9


def test_sku_absent_from_a_count_is_not_treated_as_zero():
    """The trap: a partial count. A SKU nobody counted must be SKIPPED for that
    period, not averaged in as a clean zero-loss observation."""
    weeks = ["2026-01-04", "2026-01-11", "2026-01-18"]
    consumption = {"A": [10.0] * 3}
    seen_both = [
        _period("2026-01-04", "2026-01-11", {"1": 3.0}),
        _period("2026-01-11", "2026-01-18", {"1": 3.0}),
    ]
    # second count did not include SKU 1 at all
    seen_once = [
        _period("2026-01-04", "2026-01-11", {"1": 3.0}),
        _period("2026-01-11", "2026-01-18", {}),
    ]
    both = shrinkage_mod.estimate(seen_both, {"1": "A"}.get, weeks, consumption,
                                  lambda s: "Test")["A"]
    once = shrinkage_mod.estimate(seen_once, {"1": "A"}.get, weeks, consumption,
                                  lambda s: "Test")["A"]
    assert once["n_periods"] == 1                 # skipped, not counted
    assert both["n_periods"] == 2
    # the raw rate is unchanged — an uncounted period must not dilute it
    assert once["raw_loss_per_week"] == pytest.approx(both["raw_loss_per_week"])


def test_zero_variance_template_export_is_dropped():
    """2026-07-14a is a pre-apply export: Counted == Qty on every line. It must
    not become a count, or it creates a zero-length period against 07-14b."""
    counts = shrinkage_mod.load_counts(DATA, "stow")
    dates = [c["date"].isoformat() for c in counts]
    assert len(dates) == len(set(dates)), "one count per date"
    assert "2026-07-14" in dates
    kept = next(c for c in counts if c["date"].isoformat() == "2026-07-14")
    assert kept["path"].endswith("2026-07-14b_export.csv")


def test_the_28_jul_net_variance_hides_the_loss():
    """The measured fact this whole module exists for: gross -$1,598 vs +$1,636
    nets to +$37 and reads as a clean count."""
    path = os.path.join(DATA, "stock_counts",
                        "stock_count_stowaway_bar_2026-07-28_export.csv")
    c = shrinkage_mod.parse_count_file(path)
    assert c["gross_loss_cost"] == pytest.approx(1598, abs=5)
    assert c["gross_gain_cost"] == pytest.approx(1636, abs=5)
    assert abs(c["net_cost"]) < 60          # the net looks clean...
    assert c["gross_loss_cost"] > 1000      # ...and it is not


def test_live_shrinkage_reaches_the_par_model(stow_build):
    recs, meta = stow_build
    assert meta["shrinkage"], "Stowaway must have a shrinkage estimate"
    rooster = recs["Rooster Rojo Blanco Tequila [Bottle]"]
    # v2 hard-coded this to 0.0. It must now be a measured number.
    assert rooster["drivers"]["variance_wk"] > 0
    assert rooster["drivers"]["true_wk"] > (
        rooster["drivers"]["pour_wk"] + rooster["drivers"]["recipe_wk"] - 1e-9)


# ── 5. (R,S) service-level engine ───────────────────────────────────────────
def test_higher_service_level_means_higher_par():
    kw = dict(forecast_wk=10.0, cv=0.4, exposure_units=10.0, normal_units=10.0)
    tail, _ = service_mod.order_up_to(service_class="tail", **kw)
    std, _ = service_mod.order_up_to(service_class="standard", **kw)
    core, _ = service_mod.order_up_to(service_class="core", **kw)
    assert tail < std < core
    assert service_mod.Z["tail"] < service_mod.Z["standard"] < service_mod.Z["core"]


def test_low_movers_take_the_poisson_path():
    lo, dlo = service_mod.order_up_to(forecast_wk=0.8, cv=0.9, exposure_units=10.0,
                                      normal_units=10.0, service_class="tail")
    hi, dhi = service_mod.order_up_to(forecast_wk=8.0, cv=0.9, exposure_units=10.0,
                                      normal_units=10.0, service_class="tail")
    assert dlo["path"] == "poisson"
    assert dhi["path"] == "normal"
    assert lo == float(int(lo)), "a Poisson par is a whole number of units"
    # 95th percentile of Poisson(0.8) is 2 — never the fractional 0.83 the
    # normal approximation would have produced.
    assert lo == 2.0


def test_poisson_quantile_is_the_real_distribution():
    assert service_mod.poisson_quantile(0.0) == 0
    assert service_mod.poisson_quantile(1.0, 0.95) == 3      # P(X<=3)=0.981
    assert service_mod.poisson_quantile(3.0, 0.95) == 6      # P(X<=6)=0.966
    assert service_mod.poisson_quantile(3.0, 0.95) > service_mod.poisson_quantile(3.0, 0.85)


def test_sigma_scales_with_the_square_root_of_exposure():
    _, d1 = service_mod.order_up_to(forecast_wk=10.0, cv=0.5, exposure_units=10.0,
                                    normal_units=10.0, service_class="standard")
    _, d2 = service_mod.order_up_to(forecast_wk=10.0, cv=0.5, exposure_units=40.0,
                                    normal_units=10.0, service_class="standard")
    # 4x the exposure = 2x the sigma, not 4x
    assert d2["sigma_exposure"] == pytest.approx(2 * d1["sigma_exposure"], rel=1e-6)
    assert d2["demand_over_exposure"] == pytest.approx(4 * d1["demand_over_exposure"])


def test_cv_is_shrunk_toward_the_group_not_a_flat_fallback():
    """v2 gave every unmeasurable SKU the same buffer. A SKU with no history must
    now inherit its reporting group's cv, and a SKU with lots of history must
    keep (mostly) its own."""
    noisy = [0.0, 8.0, 0.0, 9.0, 0.0, 7.0, 0.0, 8.0] * 6
    steady = [5.0, 5.2, 4.8, 5.1] * 12
    book = service_mod.VolatilityBook(
        {"NOISY": noisy, "STEADY": steady, "THIN": [1.0, 0.0]},
        lambda s: "Test")
    cv_noisy, src_noisy = book.cv_for("NOISY", "Test")
    cv_steady, _ = book.cv_for("STEADY", "Test")
    cv_thin, src_thin = book.cv_for("THIN", "Test")
    assert cv_noisy > cv_steady
    assert src_noisy == "sku"
    assert src_thin == "group"          # no own estimate -> the group's
    assert service_mod.CV_FLOOR <= cv_thin <= service_mod.CV_CEIL


def test_live_build_uses_the_poisson_path_for_the_premium_tail(stow_build):
    recs, _ = stow_build
    poisson = [r for r in recs.values() if r["service"].get("path") == "poisson"]
    assert poisson, "the long tail must not be priced with a normal approximation"
    for r in poisson:
        assert r["forecast_wk"] < service_mod.POISSON_THRESHOLD_WK


# ── 6. calendar / lead time ─────────────────────────────────────────────────
@pytest.fixture(scope="module")
def cal():
    return par_calendar.load_calendar(DATA)


def test_normal_cycle_is_ten_day_units(cal):
    exp = par_calendar.exposure(date(2026, 8, 9), cal)      # an ordinary week
    assert exp["delivery"] == "2026-08-12"                  # Wednesday
    assert exp["next_delivery"] == "2026-08-19"
    assert exp["days"] == 7
    assert exp["day_units"] == pytest.approx(10.0)
    assert exp["exposure_ratio"] == pytest.approx(1.0)


def test_holiday_monday_stretches_the_exposure(cal):
    """King's Birthday, Mon 8 Jun 2026. The 8 Jun week's run slips Wed->Fri, so
    the PREVIOUS cycle (delivered Wed 3 Jun) has to stretch to cover it."""
    prev = par_calendar.exposure(date(2026, 5, 31), cal)
    assert prev["delivery"] == "2026-06-03"
    assert prev["next_delivery"] == "2026-06-12"            # the slipped Friday
    assert prev["days"] == 9
    assert prev["day_units"] > 10.0
    assert prev["exposure_ratio"] > 1.0

    shifted, note = par_calendar.resolve_delivery(date(2026, 6, 7), cal)
    assert shifted == date(2026, 6, 12)                     # Friday, not Wednesday
    assert "holiday Monday" in note


def test_holiday_friday_removes_a_delivery_entirely(cal):
    """Mon 28 Dec 2026 slips the run to Fri 1 Jan; Fri 1 Jan is New Year's Day,
    so there is no delivery in that cycle at all."""
    delivery, note = par_calendar.resolve_delivery(date(2026, 12, 27), cal)
    assert delivery is None
    assert "NO delivery" in note
    assert par_calendar.next_delivery_after(date(2026, 12, 27), cal) == date(2027, 1, 6)


def test_christmas_2026_gap_is_fourteen_days_and_roughly_double(cal):
    exp = par_calendar.christmas_2026_exposure(cal)
    assert exp["delivery"] == "2026-12-23"
    assert exp["next_delivery"] == "2027-01-06"
    assert exp["days"] == 14
    assert exp["day_units"] == pytest.approx(21.0)
    assert 2.0 <= exp["exposure_ratio"] <= 2.4
    assert {h["name"] for h in exp["holidays_in_window"]} >= {
        "Christmas Day", "New Year's Day"}


def test_public_holiday_monday_trades_like_a_weekend_day(cal):
    assert par_calendar.day_unit(date(2026, 12, 28), cal) == 2.0   # PH Monday
    assert par_calendar.day_unit(date(2026, 12, 21), cal) == 1.0   # normal Monday
    assert par_calendar.day_unit(date(2026, 12, 19), cal) == 2.0   # Saturday


def test_calendar_holiday_dates_match_the_reorder_skill(cal):
    """The Monday list was copied out of the reorder skill's rules.json. If it
    drifts, the delivery chain silently stops matching what actually happens."""
    hol = par_calendar.holiday_map(cal)
    for d in ("2026-01-26", "2026-04-06", "2026-06-08", "2026-10-05", "2026-12-28",
              "2027-03-29", "2027-06-14", "2027-10-04"):
        assert d in hol, f"holiday Monday {d} missing from par_calendar.json"
    for d in ("2026-04-03", "2026-12-25", "2027-01-01", "2027-03-26"):
        assert d in hol, f"holiday Friday {d} missing from par_calendar.json"


# ── 7. seasonality ──────────────────────────────────────────────────────────
def _two_years_of_weeks(start="2024-01-07", n=104):
    from datetime import timedelta
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=7 * i)).isoformat() for i in range(n)]


def test_summer_peaking_sku_indexes_higher_in_december_than_june():
    weeks = _two_years_of_weeks()
    series = []
    for w in weeks:
        woy = seasonal_mod.week_of_year(w)
        # Sydney summer: peak around week 51, trough around week 25.
        summer = 1.0 if (woy >= 48 or woy <= 6) else 0.0
        winter = 1.0 if 22 <= woy <= 30 else 0.0
        series.append(10.0 + 5.0 * summer - 4.0 * winter)
    idx = seasonal_mod.build_index(series, weeks)
    assert idx, "two years of a clear seasonal shape must produce an index"
    assert idx[51] > idx[25]
    assert idx[51] > 1.0 > idx[25]
    assert all(seasonal_mod.INDEX_LO <= v <= seasonal_mod.INDEX_HI for v in idx.values())


def test_thin_sku_falls_back_to_its_reporting_group_index():
    weeks = _two_years_of_weeks()
    fat = []
    for w in weeks:
        woy = seasonal_mod.week_of_year(w)
        fat.append(20.0 + 8.0 * (1.0 if (woy >= 48 or woy <= 6) else 0.0))
    thin = [0.0] * (len(weeks) - 3) + [1.0, 0.0, 2.0]     # launched three weeks ago
    book = seasonal_mod.SeasonalBook(weeks, {"FAT": fat, "THIN": thin},
                                     lambda s: "Test")
    assert book.source_for("FAT") == "sku"
    assert book.source_for("THIN") == "group"
    # and the fallback actually carries the group's summer shape
    assert book.index_for("THIN", "Test", 51) > book.index_for("THIN", "Test", 25)


def test_level_shift_does_not_become_a_season():
    """Harry Gatos tripled in one week in July 2026. A ratio-to-MA index reads
    that as season unless the cross-year disagreement guard catches it."""
    weeks = _two_years_of_weeks()
    half = len(weeks) // 2 + 20
    series = [10.0] * half + [30.0] * (len(weeks) - half)   # pure level shift
    idx = seasonal_mod.build_index(series, weeks)
    # either no index at all, or a flat-ish one — never a 40% "season"
    if idx:
        assert max(idx.values()) < 1.25, idx


def test_sanity_floor_uses_the_seasonal_window_not_the_trailing_13_weeks():
    weeks = _two_years_of_weeks()
    series = []
    for w in weeks:
        woy = seasonal_mod.week_of_year(w)
        series.append(50.0 if 48 <= woy <= 52 else 5.0)
    # asking about a December week must see the December weeks of BOTH years
    dec = seasonal_mod.seasonal_window_values(series, weeks, 50)
    jun = seasonal_mod.seasonal_window_values(series, weeks, 25)
    assert max(dec) == 50.0
    assert max(jun) == 5.0
    assert model.sanity_floor(series, weeks, 50, 1.0) > model.sanity_floor(
        series, weeks, 25, 1.0)


def test_sanity_floor_is_a_floor_not_a_driver(stow_build):
    recs, _ = stow_build
    floored = [r for r in recs.values() if "sanity_floored" in r["flags"]]
    for r in floored:
        # ...unless a hard override wins, which it always does (a `max`/`zero`
        # override is a human saying "I don't care what the floor says").
        if r["override"]:
            continue
        assert r["rec_par"] >= r["sanity_floor"] - 1e-9
    # the floor must not be the primary driver of the book
    assert len(floored) < len(recs) / 2


# ── 8. bookings stay in the shadows ─────────────────────────────────────────
def test_bookings_are_shadow_only():
    assert bookings_mod.BOOKINGS_LIVE is False
    assert model.BOOKINGS_LIVE is False


def test_bookings_degrade_to_zero_uplift_without_a_token():
    up, status = bookings_mod.shadow_uplift({"A": [1.0]}, date(2026, 8, 12),
                                            date(2026, 8, 19), token="")
    assert up == {}
    assert status.startswith("unavailable")


def test_bookings_cover_shape_is_adults_plus_kids():
    doc = {"sittings": [{"bookings": [{"adults": 4, "kids": 1},
                                      {"adults": 2, "kids": 0}]},
                        {"bookings": [{"adults": 6, "kids": 2}]}]}
    assert bookings_mod.covers_from_day(doc) == 15
    assert bookings_mod.covers_from_day({"bookings": [{"adults": 3, "kids": 3}]}) == 6
    assert bookings_mod.covers_from_day(None) == 0


def test_live_build_records_but_does_not_apply_the_shadow_uplift(stow_build):
    recs, meta = stow_build
    assert meta["bookings_live"] is False
    for r in recs.values():
        assert "bookings_uplift_shadow" in r
        assert r["bookings_applied"] is False


# ── 9. the v3 output contract ───────────────────────────────────────────────
def test_drivers_expose_pour_recipe_and_shrinkage_separately(stow_build):
    recs, _ = stow_build
    for r in recs.values():
        d = r["drivers"]
        # cross_venue_wk is a BREAKDOWN of pour_wk (how much of it came off the
        # other venue's till on a shared-stock alias), never a fourth term.
        assert set(d) == {"pour_wk", "recipe_wk", "variance_wk", "true_wk",
                          "cross_venue_wk"}
        # (pour_wk is published to 2dp and cross_venue_wk to 3dp, so allow half
        # of pour's last place — the invariant is "subset", not "equal".)
        assert d["cross_venue_wk"] <= d["pour_wk"] + 0.005
        assert d["true_wk"] == pytest.approx(
            d["pour_wk"] + d["recipe_wk"] + d["variance_wk"], abs=1e-6)


def test_exposure_is_reported_so_a_reader_can_check_the_cycle(stow_build):
    _, meta = stow_build
    exp = meta["exposure"]
    assert exp["days"] >= 7
    assert exp["day_units"] > 0
    assert exp["normal_day_units"] == 10.0


def test_v2_engine_still_runs_for_the_impact_comparison():
    recs, meta = model.compute_venue("hg", DATA, engine="v2")
    assert meta["engine"] == "v2"
    assert recs
    for r in recs.values():
        assert r["drivers"]["variance_wk"] == 0.0     # v2 had no variance channel


# ── clumped demand: negative binomial + burst floor ─────────────────────────
def test_negbinom_exceeds_poisson_when_overdispersed():
    """Drinks arrive in rounds, not independently. Measured variance/mean at this
    venue runs 2.1-4.3; Poisson assumes 1.0 and under-stocks every session
    product. Hyoketsu: mean 1.73/wk, var/mean 4.29 -> Poisson 95th = 4,
    negative binomial = 7, observed burst = 9."""
    from modules.par import service as sv
    mean = 1.73
    var = 4.29 * mean
    pois = sv.poisson_quantile(mean, 0.95)
    nb = sv.negbinom_quantile(mean, var, 0.95)
    assert nb > pois, f"negbinom {nb} should exceed poisson {pois} on clumped demand"
    assert nb >= 6


def test_negbinom_falls_back_to_poisson_when_not_overdispersed():
    from modules.par import service as sv
    mean = 3.0
    assert sv.negbinom_quantile(mean, mean, 0.95) == sv.poisson_quantile(mean, 0.95)
    assert sv.negbinom_quantile(mean, mean * 0.5, 0.95) == sv.poisson_quantile(mean, 0.95)


def test_burst_floor_lifts_a_par_that_cannot_serve_a_round():
    """You cannot have 2 cans in the fridge for a product people drink 4 of."""
    from modules.par import service as sv
    par_no_floor, _ = sv.order_up_to(forecast_wk=1.7, cv=0.5, exposure_units=10,
                                     normal_units=10, service_class="tail")
    par_floor, det = sv.order_up_to(forecast_wk=1.7, cv=0.5, exposure_units=10,
                                    normal_units=10, service_class="tail",
                                    burst_floor=9.0)
    assert par_no_floor < 9.0
    assert par_floor == 9.0
    assert det["burst_floored"] is True


def test_burst_floor_never_lowers_a_par():
    from modules.par import service as sv
    par, det = sv.order_up_to(forecast_wk=40.0, cv=0.3, exposure_units=10,
                              normal_units=10, service_class="core",
                              burst_floor=5.0)
    assert par > 5.0
    assert det["burst_floored"] is False


def test_hyoketsu_can_serve_a_round_in_full_build():
    """Regression for the real finding: v3 initially put Hyoketsu at 2.0 cans."""
    recs, _ = model.compute_venue("stow", DATA)
    h = next((v for k, v in recs.items() if "hyoketsu" in k.lower()), None)
    if h is None:
        return  # SKU delisted; nothing to assert
    assert h["rec_par"] >= 6.0, f"Hyoketsu par {h['rec_par']} cannot serve a round"


# ── 10. POS <-> par name drift: the alias map and the unattributed gate ─────
WEEK = "2026-08-09"


def _stow_parts():
    id2name, bo_meta = model.load_bo(DATA, "stow")
    scrape = model.load_scrape(DATA, "stow")
    overrides = model.load_overrides(DATA, "stow")
    leaves, recipe_norms = model.load_recipes(DATA, "stow")
    idx = model.ParIndex(scrape, overrides, bo_meta)
    matcher = model.build_recipe_matcher(recipe_norms)
    return id2name, idx, leaves, matcher


def test_alias_book_reads_both_the_short_and_the_long_form():
    doc = {
        "stow": {"Till Name": "Par SKU [Bottle]",
                 "Poured Thing": {"sku": "Bulk Thing [Bottle]", "serve_ml": 150}},
        "_intentionally_unattributed": {
            "stow": [{"product": "Pepsi Glass", "reason": "post-mix gun"}]},
        "_unmapped_investigate": {
            "stow": [{"product": "Mystery Beer", "note": "no par SKU exists"}]},
    }
    ab = model.AliasBook(doc, "stow")
    assert ab.target("Till Name") == "Par SKU [Bottle]"
    assert ab.target("till   name") == "Par SKU [Bottle]"      # normalised lookup
    assert ab.serve_ml("Till Name") is None
    assert ab.target("Poured Thing") == "Bulk Thing [Bottle]"
    assert ab.serve_ml("Poured Thing") == 150.0
    assert ab.is_intentional("Pepsi Glass") and not ab.is_intentional("Till Name")
    assert ab.is_flagged_for_investigation("Mystery Beer")
    assert ab.target("Nothing At All") is None


def test_aliased_pos_line_contributes_to_its_par_sku():
    """THE bug. 'Petits Detours Rosé' is 34.2 glasses a week and the par SKU is
    called 'Petits Detours Rosé Mediterranee - Bottle'. Without the alias the
    volume reaches nothing at all."""
    id2name, idx, leaves, matcher = _stow_parts()
    sku = "Petits Detours Rosé Mediterranee - Bottle"
    rows = [{"venue": "stow", "reporting_group": "Rose Wine",
             "product_name": "Petits Detours Rosé", "week_ending": WEEK,
             "qty": 35.0, "sales_ex_gst": "560"}]

    without = {}
    pour, _ = model._build_consumption(rows, "stow", idx, leaves, matcher,
                                       id2name, [WEEK], unattributed_out=without)
    assert sku not in pour, "precondition: the raw name must NOT resolve"
    assert "Petits Detours Rosé" in without, "...and must be reported as a miss"

    ab = model.load_aliases(DATA, "stow")
    assert ab.target("Petits Detours Rosé") == sku
    missed = {}
    pour, _ = model._build_consumption(rows, "stow", idx, leaves, matcher,
                                       id2name, [WEEK], aliases=ab,
                                       unattributed_out=missed)
    # 35 glasses * 150ml / 700ml bottle = 7.5 bottles
    assert pour[sku][0] == pytest.approx(35 * 150 / 700, rel=1e-6)
    assert missed == {}, "an aliased line is not unattributed"


def test_aliased_tap_beer_reaches_its_keg():
    """'Fresh is Best Lager' is the house tap; the keg is stocked as 'Alehouse
    Draught Lager [Keg]'. Recipe-proven mapping, schooner/pint -> keg."""
    id2name, idx, leaves, matcher = _stow_parts()
    ab = model.load_aliases(DATA, "stow")
    sku = "Alehouse Draught Lager [Keg]"
    assert ab.target("Fresh is Best Lager") == sku
    rows = [{"venue": "stow", "reporting_group": "Tap Beer",
             "product_name": "Fresh is Best Lager", "week_ending": WEEK,
             "qty": 200.0, "sales_ex_gst": "2000"}]
    pour, _ = model._build_consumption(rows, "stow", idx, leaves, matcher,
                                       id2name, [WEEK], aliases=ab)
    assert pour[sku][0] == pytest.approx(200 * model.TAP_SERVE_ML / model.KEG_ML)
    assert pour[sku][0] > 1.0, "200 schooners/pints is more than one keg"


def test_alias_wins_and_the_line_is_counted_exactly_once():
    """No double count: an aliased line must not ALSO be picked up by the
    fallback name matcher or the recipe matcher. 'Guinness Draught' resolves to
    nothing on its own, but 'Stone & Wood' DOES resolve via resolve_bulk — so
    alias it somewhere else and prove only the alias target is credited."""
    id2name, idx, leaves, matcher = _stow_parts()
    assert idx.resolve_bulk("Stone & Wood") == "Stone & Wood [Keg]", "precondition"
    ab = model.AliasBook({"stow": {"Stone & Wood": "Kirin [Keg]"}}, "stow")
    rows = [{"venue": "stow", "reporting_group": "Tap Beer",
             "product_name": "Stone & Wood", "week_ending": WEEK,
             "qty": 100.0, "sales_ex_gst": "1500"}]
    pour, recipe = model._build_consumption(rows, "stow", idx, leaves, matcher,
                                            id2name, [WEEK], aliases=ab)
    credited = {s: v[0] for s, v in pour.items() if v[0] > 0}
    credited.update({s: v[0] for s, v in recipe.items() if v[0] > 0})
    assert list(credited) == ["Kirin [Keg]"], credited
    assert "Stone & Wood [Keg]" not in credited
    assert credited["Kirin [Keg]"] == pytest.approx(
        100 * model.TAP_SERVE_ML / model.KEG_ML)


def test_alias_revenue_is_credited_once_not_twice():
    id2name, idx, leaves, matcher = _stow_parts()
    ab = model.load_aliases(DATA, "stow")
    rev = {}
    rows = [{"venue": "stow", "reporting_group": "Rose Wine",
             "product_name": "Petits Detours Rosé", "week_ending": WEEK,
             "qty": 10.0, "sales_ex_gst": "160"}]
    model._build_consumption(rows, "stow", idx, leaves, matcher, id2name, [WEEK],
                             revenue_out=rev, aliases=ab)
    assert sum(rev.values()) == pytest.approx(160.0)


def test_alias_serve_ml_override_is_applied_and_not_double_converted():
    """Vinada is a Non-alcoholic line, where the model's default is a 1:1
    whole-unit sale — but it is really 150ml poured out of a 750ml bottle (the
    bottle costs $12.56 and the glass sells for $12, so it cannot be 1:1)."""
    sku = "Vinada Sparking Rosé [Bottle]"
    ab = model.load_aliases(DATA, "stow")
    assert ab.serve_ml("Vinada Sparkling Rose") == 150.0
    units = model._alias_units("Vinada Sparkling Rose", "Non-alcoholic", 10.0, sku,
                               serve_ml=ab.serve_ml("Vinada Sparkling Rose"))
    assert units == pytest.approx(10 * 150 / 750)


def test_a_whole_unit_alias_is_never_silently_divided():
    """Bundaberg Ginger Beer is a $3.30 bottle sold whole for $5. A Non-alcoholic
    line with no explicit serve_ml must stay 1:1, not be guessed as a glass."""
    units = model._alias_units("Bundaberg Ginger Beer", "Non-alcoholic", 10.0,
                               "Bundaberg Ginger Beer [750ml]")
    assert units == 10.0


def test_spirit_alias_keeps_the_nip_conversion():
    units = model._alias_units("White Light Pure Vodka [House]", "Vodka", 60.0,
                               "White Light Vodka [20L]")
    assert units == pytest.approx(60 * model.SPIRIT_NIP_ML / 20000.0)


def test_tap_serve_ml_prefers_an_explicit_size_in_the_name():
    assert model.tap_serve_ml("Sapporo - 500ml") == 500.0
    assert model.tap_serve_ml("Stone & Wood - Schooner") == model.SCHOONER_ML
    assert model.tap_serve_ml("Stone & Wood - Pint") == model.PINT_ML
    assert model.tap_serve_ml("Fresh is Best Lager") == model.TAP_SERVE_ML


def test_every_alias_target_is_a_real_par_sku():
    """An alias pointing at a name that does not exist is worse than no alias:
    it looks mapped and attributes nothing. The build fails on this.

    Cross-venue targets ('stow:Kirin [Keg]' in the hg map) are checked against
    the TARGET venue's par universe, which is what the build does."""
    for venue in ("stow", "hg"):
        id2name, bo_meta = model.load_bo(DATA, venue)
        scrape = model.load_scrape(DATA, venue)
        overrides = model.load_overrides(DATA, venue)
        idx = model.ParIndex(scrape, overrides, bo_meta)
        ab = model.load_aliases(DATA, venue)
        assert ab.map, f"{venue} must have aliases"
        others = {v: model.par_universe(DATA, v) for v in ab.cross_venues()}
        assert ab.unknown_targets(idx.names, others) == {}, venue


def test_no_pos_line_is_aliased_to_two_different_par_skus():
    for venue in ("stow", "hg"):
        ab = model.load_aliases(DATA, venue)
        norms = [model._norm(k) for k in ab.map]
        assert len(norms) == len(set(norms)), f"{venue}: duplicate alias key"


# ── the unattributed-volume gate ────────────────────────────────────────────
def _raw(name, rg, qty, revenue, week=WEEK):
    return {name: {"product": name, "reporting_group": rg,
                   "weekly": {week: {"qty": qty, "revenue_ex_gst": revenue}}}}


def test_unattributed_gate_fires_on_a_high_revenue_stock_bearing_line():
    raw = _raw("Fresh is Best Lager", "Tap Beer", 3012.0, 26276.0)
    offenders, intentional = model.unattributed_report(raw, [WEEK], None)
    assert [r["product"] for r in offenders] == ["Fresh is Best Lager"]
    assert offenders[0]["stock_bearing"] is True
    assert offenders[0]["revenue_ex_gst_window"] == 26276.0
    assert intentional == []
    total = sum(r["revenue_ex_gst_window"] for r in offenders)
    assert total > model.UNATTRIBUTED_FAIL_REVENUE, "this must FAIL the build"


def test_intentionally_unattributed_lines_do_not_trip_the_gate():
    """Post-mix drinks consume no discrete stock unit. They can never attribute,
    so they must never be able to fail a build."""
    doc = {"stow": {},
           "_intentionally_unattributed": {
               "stow": [{"product": "Pepsi Max Glass", "reason": "post-mix gun"}]}}
    ab = model.AliasBook(doc, "stow")
    raw = _raw("Pepsi Max Glass", "Non-alcoholic", 538.0, 2290.0)
    offenders, intentional = model.unattributed_report(raw, [WEEK], ab)
    assert offenders == []
    assert [r["product"] for r in intentional] == ["Pepsi Max Glass"]
    assert intentional[0]["reason"] == "post-mix gun"
    assert sum(r["revenue_ex_gst_window"] for r in offenders) == 0


def test_non_stock_bearing_groups_are_not_the_gates_business():
    """Cocktails have their own coverage gate (the recipe book); food is not a
    drink. Neither may leak into this one."""
    raw = {}
    raw.update(_raw("Some Cocktail", "Cocktails - Classic", 50.0, 900.0))
    raw.update(_raw("Rosemary Salted Fries", "Small Plates", 500.0, 6089.0))
    raw.update(_raw("Real Beer", "Tap Beer", 10.0, 150.0))
    offenders, _ = model.unattributed_report(raw, [WEEK], None)
    assert [r["product"] for r in offenders] == ["Real Beer"]


def test_unattributed_window_is_the_last_thirteen_weeks_only():
    weeks = [f"2026-0{m}-0{d}" for m, d in
             ((1, 4), (2, 1), (3, 1), (4, 5), (5, 3), (6, 7), (7, 5))]
    raw = {"Old Beer": {"product": "Old Beer", "reporting_group": "Tap Beer",
                        "weekly": {weeks[0]: {"qty": 99.0, "revenue_ex_gst": 9999.0},
                                   weeks[-1]: {"qty": 1.0, "revenue_ex_gst": 10.0}}}}
    offenders, _ = model.unattributed_report(raw, weeks, None, window=2)
    assert offenders[0]["revenue_ex_gst_window"] == 10.0   # the old week is out


def test_known_unmapped_lines_are_labelled_but_still_counted():
    """`_unmapped_investigate` documents a line we could not confidently map. It
    is NOT an excuse — the revenue still counts toward the gate, so a genuinely
    missing par SKU cannot be parked forever."""
    doc = {"stow": {}, "_unmapped_investigate": {
        "stow": [{"product": "Philter Pale", "note": "no Philter Pale keg at stow"}]}}
    ab = model.AliasBook(doc, "stow")
    raw = _raw("Philter Pale", "Tap Beer", 24.0, 244.0)
    offenders, _ = model.unattributed_report(raw, [WEEK], ab)
    assert len(offenders) == 1
    assert offenders[0]["known_unmapped"] is True
    assert offenders[0]["revenue_ex_gst_window"] == 244.0


# ── the live build must stay under the gate ─────────────────────────────────
def test_live_unattributed_volume_is_under_the_build_threshold():
    for venue in ("stow", "hg"):
        _, meta = model.compute_venue(venue, DATA)
        total = meta["unattributed_revenue"]
        assert total <= model.UNATTRIBUTED_FAIL_REVENUE, (
            f"{venue}: ${total:,.0f} of stock-bearing drink revenue reaches no "
            f"par SKU — {[r['product'] for r in meta['unattributed']]}")


def test_live_tap_beer_volume_actually_reaches_the_kegs(stow_build):
    """Before this change EVERY keg sat on `held_no_recent_demand` with a zero
    demand driver, because the recipe rows are named per variant ('Stone & Wood
    - Schooner') and products_weekly collapses the sale line to 'Stone & Wood'."""
    recs, _ = stow_build
    for keg in ("Alehouse Draught Lager [Keg]", "Stone & Wood [Keg]",
                "Grifter Pale [Keg]", "Philter XPA [Keg]", "Guinness [Keg]",
                "Kirin [Keg]"):
        r = recs[keg]
        drivers = r["drivers"]
        assert drivers["pour_wk"] + drivers["recipe_wk"] > 0, f"{keg} sees no demand"
        assert "held_no_recent_demand" not in r["flags"], keg


def test_petits_detours_par_regression(stow_build):
    """The proven case. The model recommended 3.0 for a wine with a live par of
    22.9 because 90% of its volume was landing on a name it did not know."""
    recs, _ = stow_build
    r = recs["Petits Detours Rosé Mediterranee - Bottle"]
    assert r["rec_par"] >= 10.0, r
    assert r["drivers"]["pour_wk"] > 5.0, r["drivers"]


def test_house_vodka_and_the_typo_sku_are_no_longer_orphans(stow_build):
    recs, _ = stow_build
    # 48.2 nips/wk of the house vodka off a 20L cask
    assert recs["White Light Vodka [20L]"]["drivers"]["pour_wk"] > 0
    # par SKU is spelled 'Sparking', the till says 'Sparkling'
    assert recs["Vinada Sparking Rosé [Bottle]"]["drivers"]["pour_wk"] > 0
    assert recs["Bailey's Irish Cream 1L [Bottle]"]["drivers"]["pour_wk"] > 0
    assert recs["Balvenie 14yr Caribbean Cask [Bottle]"]["drivers"]["pour_wk"] > 0


def test_par_index_bulk_resolution_is_deterministic():
    """`ParIndex` iterated a set, so which of two colliding bulk names won a base
    depended on the per-process string hash seed and pars flapped between builds
    with no input change. Largest container wins, deterministically."""
    bo = {"Widget [30ml]": {"rg": ""}, "Widget [Bottle]": {"rg": ""},
          "Widget [Keg]": {"rg": ""}}
    for _ in range(5):
        idx = model.ParIndex({}, {}, dict(bo))
        assert idx.resolve_bulk("Widget") == "Widget [Keg]"
    bo.pop("Widget [Keg]")
    assert model.ParIndex({}, {}, bo).resolve_bulk("Widget") == "Widget [Bottle]"


def test_live_par_index_has_no_ambiguous_bulk_winner_left_to_chance():
    for venue in ("stow", "hg"):
        id2name, bo_meta = model.load_bo(DATA, venue)
        idx = model.ParIndex(model.load_scrape(DATA, venue),
                             model.load_overrides(DATA, venue), bo_meta)
        again = model.ParIndex(model.load_scrape(DATA, venue),
                               model.load_overrides(DATA, venue), bo_meta)
        assert idx.bulk_base == again.bulk_base, venue


# ── 11. shared stock across venues: the "stow:" cross-venue alias target ────
# Stowaway and Harry Gatos share some lines. The stock is held and ordered
# centrally against STOWAWAY par SKUs (the taps Philter XPA / Grifter Pale /
# Kirin / Crankshaft IPA / Status Quo / Guinness, and the Coke and Sprite cans)
# but it also pours through the HG till. Those HG sales reached NO par SKU at
# either venue, so Stowaway's pars under-counted real consumption and HG's
# unattributed report nagged about stock items HG will never own.
HG_WEEK = WEEK


def _hg_parts():
    id2name, bo_meta = model.load_bo(DATA, "hg")
    scrape = model.load_scrape(DATA, "hg")
    overrides = model.load_overrides(DATA, "hg")
    leaves, recipe_norms = model.load_recipes(DATA, "hg")
    idx = model.ParIndex(scrape, overrides, bo_meta)
    matcher = model.build_recipe_matcher(recipe_norms)
    return id2name, idx, leaves, matcher


def _hg_tap_row(name="Kirin", qty=100.0, rev="1500"):
    return {"venue": "hg", "reporting_group": "Tap Beer", "product_name": name,
            "week_ending": HG_WEEK, "qty": qty, "sales_ex_gst": rev}


def test_venue_prefix_is_parsed_only_for_real_venue_codes():
    assert model.parse_alias_target("stow:Kirin [Keg]", "hg") == ("stow", "Kirin [Keg]")
    assert model.parse_alias_target("hg:Sapporo [Keg]", "stow") == ("hg", "Sapporo [Keg]")
    # a plain target still means "this venue" — every existing alias relies on it
    assert model.parse_alias_target("Kirin [Keg]", "hg") == ("hg", "Kirin [Keg]")
    # ...and a name that merely contains a colon is NOT a venue prefix
    assert model.parse_alias_target("Grifter: Omen [Keg]", "stow") == (
        "stow", "Grifter: Omen [Keg]")


def test_alias_book_exposes_the_target_venue():
    ab = model.AliasBook({"hg": {"Kirin": "stow:Kirin [Keg]",
                                 "Harry's Lager": "Alehouse Premium Lager [Keg]"}}, "hg")
    assert ab.resolve("Kirin") == ("stow", "Kirin [Keg]")
    assert ab.target("Kirin") == "Kirin [Keg]"          # bare SKU, back-compat
    assert ab.target_venue("Kirin") == "stow"
    assert ab.resolve("Harry's Lager") == ("hg", "Alehouse Premium Lager [Keg]")
    assert ab.cross_venues() == {"stow"}
    # the value is preserved exactly as written, so the build's error output and
    # the meta block stay readable
    assert ab.map["Kirin"] == "stow:Kirin [Keg]"


def test_cross_venue_line_adds_volume_to_the_other_venues_par_sku():
    """THE change. An HG till line poured out of Stowaway's keg must reach
    Stowaway's par SKU, converted by the same schooner/pint -> keg rule."""
    id2name, idx, leaves, matcher = _stow_parts()
    hg_ab = model.load_aliases(DATA, "hg")
    assert hg_ab.resolve("Kirin") == ("stow", "Kirin [Keg]"), "precondition"

    rows = [_hg_tap_row("Kirin", qty=100.0)]
    cross_units = {}
    pour, recipe = model._build_consumption(
        rows, "stow", idx, leaves, matcher, id2name, [HG_WEEK],
        aliases=model.load_aliases(DATA, "stow"),
        cross_aliases={"hg": hg_ab}, cross_units_out=cross_units)
    expected = 100 * model.TAP_SERVE_ML / model.KEG_ML
    assert pour["Kirin [Keg]"][0] == pytest.approx(expected)
    # ...and the foreign portion is tracked separately, so a reader can tell
    # Stowaway's own till volume from Harry Gatos'.
    assert cross_units["Kirin [Keg]"][0] == pytest.approx(expected)


def test_cross_venue_cans_stay_one_to_one():
    """A can is a whole unit at either venue — the cross-venue path must not
    invent a pour conversion."""
    id2name, idx, leaves, matcher = _stow_parts()
    hg_ab = model.load_aliases(DATA, "hg")
    assert hg_ab.resolve("Coke Can") == ("stow", "Coke Can")
    rows = [{"venue": "hg", "reporting_group": "Non-Alcoholic",
             "product_name": "Coke Can", "week_ending": HG_WEEK,
             "qty": 9.0, "sales_ex_gst": "45"}]
    pour, _ = model._build_consumption(
        rows, "stow", idx, leaves, matcher, id2name, [HG_WEEK],
        aliases=model.load_aliases(DATA, "stow"), cross_aliases={"hg": hg_ab})
    assert pour["Coke Can"][0] == 9.0


def test_cross_venue_line_adds_nothing_to_any_par_sku_of_the_venue_that_sold_it():
    """No leakage. Building HG, the same row must credit NOTHING — not an HG par
    SKU, not the Stowaway SKU (that is Stowaway's build's job), and it must not
    be counted a second time by the fallback tap-beer matcher."""
    id2name, idx, leaves, matcher = _hg_parts()
    hg_ab = model.load_aliases(DATA, "hg")
    exported = {}
    missed = {}
    pour, recipe = model._build_consumption(
        [_hg_tap_row("Kirin", qty=100.0)], "hg", idx, leaves, matcher, id2name,
        [HG_WEEK], aliases=hg_ab, unattributed_out=missed, exported_out=exported)
    credited = {s: v[0] for s, v in pour.items() if v[0] > 0}
    credited.update({s: v[0] for s, v in recipe.items() if v[0] > 0})
    assert credited == {}, credited
    assert missed == {}, "it is attributed, just at the other venue"
    assert exported["Kirin"]["target_venue"] == "stow"
    assert exported["Kirin"]["target_sku"] == "Kirin [Keg]"


def test_a_cross_venue_line_is_counted_in_exactly_one_venue():
    """Belt and braces on the double-count: run BOTH venues over the same row
    set and prove the total units credited across both builds equal one keg's
    worth of that row, not two."""
    row = _hg_tap_row("Philter XPA", qty=200.0)
    hg_ab = model.load_aliases(DATA, "hg")
    stow_ab = model.load_aliases(DATA, "stow")

    sid2, sidx, sleaves, smatch = _stow_parts()
    spour, srec = model._build_consumption(
        [row], "stow", sidx, sleaves, smatch, sid2, [HG_WEEK],
        aliases=stow_ab, cross_aliases={"hg": hg_ab})
    hid2, hidx, hleaves, hmatch = _hg_parts()
    hpour, hrec = model._build_consumption(
        [row], "hg", hidx, hleaves, hmatch, hid2, [HG_WEEK],
        aliases=hg_ab, cross_aliases={"stow": stow_ab})

    total = (sum(v[0] for v in spour.values()) + sum(v[0] for v in srec.values())
             + sum(v[0] for v in hpour.values()) + sum(v[0] for v in hrec.values()))
    assert total == pytest.approx(200 * model.TAP_SERVE_ML / model.KEG_ML)


def test_cross_venue_line_is_not_unattributed_and_cannot_trip_the_gate():
    """It IS attributed — to another venue's par. It must leave the offenders
    list entirely, so it can never contribute a dollar to the $2,000 gate."""
    id2name, idx, leaves, matcher = _hg_parts()
    hg_ab = model.load_aliases(DATA, "hg")
    rows = [_hg_tap_row("Kirin", qty=100.0, rev="99999")]
    missed, exported = {}, {}
    model._build_consumption(rows, "hg", idx, leaves, matcher, id2name, [HG_WEEK],
                             aliases=hg_ab, unattributed_out=missed,
                             exported_out=exported)
    offenders, _ = model.unattributed_report(missed, [HG_WEEK], hg_ab)
    assert offenders == []
    assert sum(r["revenue_ex_gst_window"] for r in offenders) == 0
    report = model.cross_venue_report(exported, [HG_WEEK])
    assert [r["product"] for r in report] == ["Kirin"]
    assert report[0]["revenue_ex_gst_window"] == 99999.0
    assert report[0]["target_venue"] == "stow"


def test_cross_venue_target_that_is_not_a_real_par_sku_is_reported():
    """Requirement that keeps the whole scheme honest: a 'stow:' target that does
    not exist in STOWAWAY's par universe must still be caught, and the build
    still fails on it."""
    stow_names = model.par_universe(DATA, "stow")
    ab = model.AliasBook({"hg": {"Kirin": "stow:Kirin [Keg]",
                                 "Ghost Beer": "stow:Not A Real Keg [Keg]"}}, "hg")
    _, hg_bo = model.load_bo(DATA, "hg")
    hg_names = model.ParIndex(model.load_scrape(DATA, "hg"), {}, hg_bo).names
    bad = ab.unknown_targets(hg_names, {"stow": stow_names})
    assert bad == {"Ghost Beer": "stow:Not A Real Keg [Keg]"}


def test_a_cross_venue_target_is_never_assumed_good_when_unvalidatable():
    """If the target venue's par universe was not supplied, the entry is
    REPORTED rather than silently passed — an unchecked target is exactly the
    'looks mapped, attributes nothing' failure this guard exists for."""
    ab = model.AliasBook({"hg": {"Kirin": "stow:Kirin [Keg]"}}, "hg")
    assert ab.unknown_targets(["Kirin [Keg]"]) == {"Kirin": "stow:Kirin [Keg]"}


def test_juice_and_postmix_stay_intentionally_unattributed():
    """Zak, 2026-08-10: 'ignore the juice as we will just order that visually,
    too hard to track the stock on those.' The juice glasses are a POLICY
    exclusion, not a mapping gap — they must stay out of the offenders list at
    both venues and must not have been quietly aliased to a cask."""
    for venue in ("stow", "hg"):
        ab = model.load_aliases(DATA, venue)
        for line in ("Apple Juice Glass", "Orange Juice Glass",
                     "Pineapple Juice Glass"):
            assert ab.is_intentional(line), f"{venue}: {line}"
            assert ab.target(line) is None, f"{venue}: {line} must not be aliased"
            assert "visual" in ab.intentional_reason(line).lower(), (
                f"{venue}: {line} must record WHY it is excused")
        raw = _raw("Orange Juice Glass", "Non-alcoholic", 5.0, 40.0)
        offenders, intentional = model.unattributed_report(raw, [WEEK], ab)
        assert offenders == []
        assert [r["product"] for r in intentional] == ["Orange Juice Glass"]


# ── the live build ─────────────────────────────────────────────────────────
def test_live_hg_shared_lines_are_attributed_to_stowaway_not_missing():
    _, meta = model.compute_venue("hg", DATA)
    exported = {r["product"]: r for r in meta["attributed_to_other_venue"]}
    for line, sku in (("Philter XPA", "Philter XPA [Keg]"),
                      ("Grifter Pale", "Grifter Pale [Keg]"),
                      ("Kirin", "Kirin [Keg]"),
                      ("Crankshaft IPA", "Crankshaft IPA [Keg]"),
                      ("Status Quo", "Status Quo [Keg]"),
                      ("Guinness Draught", "Guinness [Keg]"),
                      ("Coke Can", "Coke Can"),
                      ("Sprite Can", "Sprite Can")):
        assert line in exported, f"{line} should be attributed to Stowaway"
        assert exported[line]["target_venue"] == "stow"
        assert exported[line]["target_sku"] == sku
    unattributed = {r["product"] for r in meta["unattributed"]}
    assert unattributed.isdisjoint(exported), unattributed & set(exported)


def test_live_diet_coke_is_still_flagged_not_folded_into_coke_zero():
    """Stowaway stocks Coke Zero, not Diet Coke. Mapping one onto the other
    would put real Diet Coke demand on a par that can never satisfy it."""
    ab = model.load_aliases(DATA, "hg")
    assert ab.target("Diet Coke Can") is None
    assert ab.is_flagged_for_investigation("Diet Coke Can")
    _, meta = model.compute_venue("hg", DATA)
    offenders = {r["product"] for r in meta["unattributed"]}
    assert "Diet Coke Can" in offenders, "must stay on the work queue"
    stow_names = model.par_universe(DATA, "stow")
    assert not any(model._norm(n) == model._norm("Diet Coke Can")
                   for n in stow_names), "no Stowaway Diet Coke SKU exists"


def test_live_stowaway_kegs_gain_the_harry_gatos_volume(stow_build):
    recs, _ = stow_build
    for keg in ("Philter XPA [Keg]", "Grifter Pale [Keg]", "Kirin [Keg]",
                "Crankshaft IPA [Keg]", "Status Quo [Keg]", "Guinness [Keg]"):
        d = recs[keg]["drivers"]
        assert d["cross_venue_wk"] > 0, f"{keg} sees no HG volume"
        # cross is a SUBSET of pour, never a separate term added to true_wk
        assert d["cross_venue_wk"] <= d["pour_wk"] + 0.005, keg
        assert recs[keg]["drivers"]["true_wk"] == pytest.approx(
            d["pour_wk"] + d["recipe_wk"] + d["variance_wk"], abs=1e-3)


def test_a_foreign_trickle_never_cuts_a_live_par(stow_build):
    """Stowaway's 'Coke Can' sells through Marilyna's, which products_weekly
    files under its own venue, so Stowaway's own till volume is invisible and
    the par (32.5) is held by the safety net. 0.23 HG cans a week is not
    evidence that mapping is healthy — releasing the hold on it would have cut
    the par to ~1. Foreign demand may RAISE such a par; it may never lower it."""
    recs, _ = stow_build
    for sku in ("Coke Can", "Sprite Can"):
        r = recs[sku]
        assert r["drivers"]["cross_venue_wk"] > 0, sku
        assert r["rec_par"] == r["current_par"], r
        assert "held_cross_venue_demand_only" in r["flags"], r["flags"]


def test_the_cross_venue_hold_is_targeted_not_blanket(stow_build):
    """The hold only fires where the foreign trickle is the ONLY recent demand.
    A SKU Stowaway's own till sells must keep being modelled from its demand —
    otherwise this safety net would freeze every shared line at its live par."""
    recs, _ = stow_build
    for keg in ("Philter XPA [Keg]", "Grifter Pale [Keg]", "Kirin [Keg]",
                "Guinness [Keg]"):
        flags = recs[keg]["flags"]
        assert "cross_venue_demand" in flags, keg
        assert "held_cross_venue_demand_only" not in flags, keg
        assert "held_no_recent_demand" not in flags, keg
        assert recs[keg]["rec_par"] > 0, keg


# ── 12. the [HG] suffix RULE ────────────────────────────────────────────────
# Zak, 2026-08-10, verbatim: "same as the wines. pretty much any SKU labelled
# with [HG] is a harrys SKU that draws from stowaway stock".
#
# Section 11 solved the shared taps and the Coke/Sprite cans by NAMING each one
# in data/par_aliases.json. That is a list, and a list rots: the next [HG]
# product Harry Gatos puts on sells for months into an empty par before anyone
# notices the line in the unattributed report. So the same fact is expressed as
# a RULE — strip the '[HG]', match the remainder against STOWAWAY's par
# universe, route the consumption there — and these are the guards on it.
#
# The three properties that matter, in order:
#   * an explicit alias ALWAYS beats the rule (a human overrules a machine);
#   * the rule never guesses (no Stowaway match => unattributed + reported);
#   * no leakage (the volume lands at Stowaway, once, and nowhere else).
def _rule(par_names=("Kirin [Keg]",), live=None):
    return model.HGSuffixRule("stow", set(par_names), live or {})


def _hg_row(name, rg="Bottles / Cans Alcoholic", qty=10.0, rev="200"):
    return {"venue": "hg", "reporting_group": rg, "product_name": name,
            "week_ending": HG_WEEK, "qty": qty, "sales_ex_gst": rev}


def test_hg_suffix_is_stripped_and_the_whitespace_tidied():
    assert model.strip_hg_suffix("Kaiju Hazy Pale [HG]") == "Kaiju Hazy Pale"
    assert model.strip_hg_suffix("Two Tonne Riesling - Bottle [HG]") == (
        "Two Tonne Riesling - Bottle")
    # the suffix can leave a dangling separator or a double space behind
    assert model.strip_hg_suffix("Cosmo - [HG]") == "Cosmo"
    assert model.strip_hg_suffix("Fries  [HG]  ") == "Fries"
    assert model.strip_hg_suffix("[hg] Odd One") == "Odd One"
    assert model.has_hg_suffix("Fries [HG]") and not model.has_hg_suffix("Fries")


def test_the_rule_tolerates_the_container_drift_between_the_two_catalogues():
    """The two catalogues write the same physical item differently — that IS the
    drift this whole area exists for. '- Bottle', '[Bottle]', '[Keg]', 'Can' and
    'Tin' must all agree on identity."""
    rule = _rule(("Trutta Streamside Shiraz [Chilled] - Bottle",
                  "Grifter Big Sur IPA Tin", "Two Tonne Riesling - Bottle"))
    assert rule.match("Trutta Streamside Shiraz - Bottle [HG]")[0] == (
        "Trutta Streamside Shiraz [Chilled] - Bottle")
    assert rule.match("Grifter Big Sur IPA Can [HG]")[0] == "Grifter Big Sur IPA Tin"
    # exact wins outright, and reports itself as exact
    sku, _, why = rule.match("Two Tonne Riesling - Bottle [HG]")
    assert (sku, why) == ("Two Tonne Riesling - Bottle", "exact")


def test_the_rule_is_case_and_diacritics_insensitive():
    rule = _rule(("Petits Detours Rosé Mediterranee - Bottle",))
    assert rule.match("PETITS DETOURS ROSE MEDITERRANEE - BOTTLE [hg]")[0] == (
        "Petits Detours Rosé Mediterranee - Bottle")


def test_an_ambiguous_base_is_broken_by_the_live_par_not_by_a_preference():
    """Two Stowaway SKUs share the base 'Hyoketsu Lemon'. The tie is broken by a
    FACT — which one is actually held and ordered — not by a guess about size."""
    rule = _rule(("Hyoketsu Lemon Can", "Hyoketsu Lemon [Keg]"),
                 live={"Hyoketsu Lemon Can": 3.9})
    assert rule.match("Hyoketsu Lemon [HG]")[0] == "Hyoketsu Lemon Can"
    # ...and with no fact to break it, it refuses rather than picks
    blind = _rule(("Hyoketsu Lemon Can", "Hyoketsu Lemon [Keg]"))
    sku, _, why = blind.match("Hyoketsu Lemon [HG]")
    assert sku is None and "ambiguous" in why


def test_an_hg_suffixed_line_auto_resolves_and_adds_volume_to_the_stowaway_par():
    """THE requirement. Nobody edited a file; the line still reaches Stowaway's
    par SKU, converted by the ordinary rules."""
    stow_names = model.par_universe(DATA, "stow")
    hg_ab = model.AliasBook({}, "hg").attach_auto_rule(
        model.HGSuffixRule("stow", stow_names, model.load_scrape(DATA, "stow")))
    assert hg_ab.resolve("Grifter Big Sur IPA Can [HG]") == (
        "stow", "Grifter Big Sur IPA Tin")

    sid2, sidx, sleaves, smatch = _stow_parts()
    pour, recipe = model._build_consumption(
        [_hg_row("Grifter Big Sur IPA Can [HG]", qty=7.0)], "stow", sidx,
        sleaves, smatch, sid2, [HG_WEEK], cross_aliases={"hg": hg_ab})
    # a can is a whole unit off the shelf: 7 rung up == 7 tins consumed
    assert pour["Grifter Big Sur IPA Tin"][0] == pytest.approx(7.0)
    assert sum(v[0] for v in recipe.values()) == 0


def test_an_explicit_alias_overrides_the_automatic_rule():
    """A human's decision beats the machine's, always. Without this the rule
    could not be corrected for the one product where it is wrong except by
    switching it off for every product."""
    rule = _rule(("Kaiju Hazy Pale Tin", "Kirin [Keg]"))
    ab = model.AliasBook(
        {"hg": {"Kaiju Hazy Pale [HG]": "stow:Kirin [Keg]"}}, "hg")
    ab.attach_auto_rule(rule)
    # the rule ALONE would have said 'Kaiju Hazy Pale Tin'...
    assert rule.match("Kaiju Hazy Pale [HG]")[0] == "Kaiju Hazy Pale Tin"
    # ...but the explicit alias wins, and the rule is never even consulted
    assert ab.resolve("Kaiju Hazy Pale [HG]") == ("stow", "Kirin [Keg]")
    assert ab.is_explicit("Kaiju Hazy Pale [HG]")
    assert "Kaiju Hazy Pale [HG]" not in rule.resolved


def test_an_explicit_alias_can_send_an_hg_suffixed_line_to_an_hg_par():
    """The override must be able to CONTRADICT the rule, not just redirect it
    within Stowaway — otherwise 'pretty much any' would have become 'every'."""
    ab = model.AliasBook(
        {"hg": {"House Thing [HG]": "Local HG Bottle"}}, "hg")
    ab.attach_auto_rule(_rule(("House Thing",)))
    assert ab.resolve("House Thing [HG]") == ("hg", "Local HG Bottle")


def test_an_hg_line_with_no_stowaway_match_is_never_guessed_at():
    """No match means NO match. It stays unattributed for the existing gate to
    surface, and it is listed with the stripped name so a human can write the
    alias — the one thing it must never do is quietly become someone else's par."""
    rule = _rule(("Kirin [Keg]", "Coke Can"))
    ab = model.AliasBook({}, "hg").attach_auto_rule(rule)
    assert ab.resolve("Zagara Orange [HG]") == (None, None)

    hid2, hidx, hleaves, hmatch = _hg_parts()
    missed, exported = {}, {}
    row = _hg_row("Zagara Orange [HG]", rg="Orange / Skins Wine", qty=6.0,
                  rev="124")
    model._build_consumption([row], "hg", hidx, hleaves, hmatch, hid2, [HG_WEEK],
                             aliases=ab, unattributed_out=missed,
                             exported_out=exported)
    assert exported == {}, "nothing was routed anywhere"
    assert "Zagara Orange [HG]" in missed, "and it is NOT silently dropped"

    _, unresolved = model.hg_suffix_report(rule, exported, missed, [HG_WEEK])
    assert [r["pos_line"] for r in unresolved] == ["Zagara Orange [HG]"]
    assert unresolved[0]["stripped"] == "Zagara Orange"
    assert unresolved[0]["still_unattributed"] is True
    assert unresolved[0]["revenue_ex_gst_window"] == 124.0
    assert "no stow par SKU" in unresolved[0]["reason"]


def test_an_intentionally_unattributed_line_is_never_offered_to_the_rule():
    """'Ginger Beer Glass [HG]' is a post-mix gun. A human has already said it
    consumes no discrete stock unit; a name-shaped guess must not overturn that."""
    rule = _rule(("Ginger Beer Glass",))
    ab = model.AliasBook(
        {"_intentionally_unattributed":
            {"hg": [{"product": "Ginger Beer Glass [HG]", "reason": "post-mix gun"}]}},
        "hg").attach_auto_rule(rule)
    assert ab.resolve("Ginger Beer Glass [HG]") == (None, None)
    assert rule.resolved == {} and rule.unresolved == {}


def test_the_auto_resolved_line_leaks_into_no_hg_par_and_is_not_double_counted():
    """Same contract the explicit cross-venue aliases hold to: a POS line feeds
    exactly ONE par SKU in exactly ONE venue. Run both builds over the same row
    and prove the units credited across the pair equal one line's worth."""
    row = _hg_row("Grifter Big Sur IPA Can [HG]", qty=9.0)
    stow_ab = model.load_aliases(DATA, "stow")
    hg_ab = model.load_aliases(DATA, "hg")          # rule attached by load_aliases
    assert hg_ab.auto_rule is not None, "the rule must be live on the real book"

    hid2, hidx, hleaves, hmatch = _hg_parts()
    missed, exported = {}, {}
    hpour, hrec = model._build_consumption(
        [row], "hg", hidx, hleaves, hmatch, hid2, [HG_WEEK], aliases=hg_ab,
        unattributed_out=missed, exported_out=exported,
        cross_aliases={"stow": stow_ab})
    assert {s: v[0] for s, v in hpour.items() if v[0]} == {}, "no HG par gains it"
    assert {s: v[0] for s, v in hrec.items() if v[0]} == {}
    assert missed == {}, "it is attributed, just at the other venue"
    assert exported["Grifter Big Sur IPA Can [HG]"]["via"] == "hg_suffix_rule"
    assert exported["Grifter Big Sur IPA Can [HG]"]["target_sku"] == (
        "Grifter Big Sur IPA Tin")

    sid2, sidx, sleaves, smatch = _stow_parts()
    spour, srec = model._build_consumption(
        [row], "stow", sidx, sleaves, smatch, sid2, [HG_WEEK],
        aliases=stow_ab, cross_aliases={"hg": model.load_aliases(DATA, "hg")})
    total = (sum(v[0] for v in spour.values()) + sum(v[0] for v in srec.values())
             + sum(v[0] for v in hpour.values()) + sum(v[0] for v in hrec.values()))
    assert total == pytest.approx(9.0), "counted once, at Stowaway, and only there"


def test_the_rule_marks_its_own_work_so_it_can_be_audited():
    """An automatic mapping that cannot be told apart from a reviewed one is a
    mapping nobody can check."""
    _, meta = model.compute_venue("hg", DATA)
    by_line = {r["product"]: r for r in meta["attributed_to_other_venue"]}
    assert by_line["Kirin"]["via"] == "alias"
    auto = {r["pos_line"]: r for r in meta["auto_hg_suffix"]}
    assert auto, "the live build must resolve at least one [HG] line"
    for line, r in auto.items():
        assert model.has_hg_suffix(line)
        assert r["target_venue"] == "stow"
        assert by_line[line]["via"] == "hg_suffix_rule"


def test_a_new_hg_product_needs_no_file_edit():
    """The point of a rule over a list. A product nobody has ever mapped, that
    Stowaway already stocks, attributes the first week it sells."""
    hg_ab = model.load_aliases(DATA, "hg")
    for line in ("Coke Can [HG]", "Corona [HG]", "Little Dragon Can [HG]"):
        assert not hg_ab.is_explicit(line), f"{line} must be unmapped by hand"
        venue, sku = hg_ab.resolve(line)
        assert venue == "stow" and sku, f"{line} did not auto-resolve"


# ── the live build ─────────────────────────────────────────────────────────
def test_live_hg_suffix_lines_reach_stowaway_and_leave_the_offenders_list():
    _, meta = model.compute_venue("hg", DATA)
    auto = {r["pos_line"]: r["target_sku"] for r in meta["auto_hg_suffix"]}
    assert auto == {"Grifter Big Sur IPA Can [HG]": "Grifter Big Sur IPA Tin",
                    "Monteith's Apple Cider [HG]": "Monteith's Apple Cider Bottle"}
    offenders = {r["product"] for r in meta["unattributed"]}
    assert offenders.isdisjoint(auto), offenders & set(auto)


def test_live_unresolved_hg_lines_are_reported_with_the_name_a_human_needs():
    """The work queue. Every [HG] line the rule refused must say what it looked
    for, so writing the explicit alias is mechanical."""
    _, meta = model.compute_venue("hg", DATA)
    unresolved = {r["pos_line"]: r for r in meta["hg_suffix_unresolved"]}
    # Don't pin a specific SKU: the work queue is meant to DRAIN as aliases get
    # written (Zagara Orange was the first one fixed this way). Assert the
    # contract instead — every entry carries the name a human needs.
    for line, r in unresolved.items():
        assert r["stripped"] and r["reason"], line
    # a cocktail carrying the suffix resolves through the RECIPE book instead —
    # the rule must not have stolen it, and must not report it as a work item
    assert unresolved["Vodka Martini [HG]"]["still_unattributed"] is False


def test_live_stowaway_pars_gained_the_hg_suffix_volume(stow_build):
    recs, _ = stow_build
    for sku in ("Grifter Big Sur IPA Tin", "Monteith's Apple Cider Bottle"):
        d = recs[sku]["drivers"]
        assert d["cross_venue_wk"] > 0, f"{sku} sees no [HG] volume"
        assert d["cross_venue_wk"] <= d["pour_wk"] + 0.005, sku
        assert "cross_venue_demand" in recs[sku]["flags"], sku


def test_the_hg_suffix_rule_cannot_release_a_no_demand_hold(stow_build):
    """The safety property from the cross-venue commit, restated for the rule:
    foreign volume may RAISE a Stowaway par, never lower one that is only held
    up by the no-recent-demand safety net. An automatic mapping makes this more
    important, not less — nobody reviewed the line that supplied the demand."""
    recs, _ = stow_build
    for r in recs.values():
        if "held_cross_venue_demand_only" in r["flags"]:
            assert r["rec_par"] == r["current_par"], r["product"]
        # a hold, however flagged, can never sit BELOW the live par
        if r["current_par"] is not None and (
                "held_cross_venue_demand_only" in r["flags"]
                or "held_no_recent_demand" in r["flags"]):
            assert r["rec_par"] >= float(r["current_par"]) - 1e-9, r["product"]


def test_no_hg_par_sku_receives_cross_venue_volume_from_the_rule():
    """Belt and braces on leakage at the whole-build level: HG imports nothing,
    because the flow is one-way — HG's till, Stowaway's stock."""
    _, meta = model.compute_venue("hg", DATA)
    assert meta["cross_venue_imported"] == {}
    assert meta["hg_suffix_rule_active"] is True
    _, smeta = model.compute_venue("stow", DATA)
    assert smeta["hg_suffix_rule_active"] is False, "the rule is HG's, not Stowaway's"
    for sku in ("Grifter Big Sur IPA Tin", "Monteith's Apple Cider Bottle"):
        assert smeta["cross_venue_imported"].get(sku, 0) > 0, sku
