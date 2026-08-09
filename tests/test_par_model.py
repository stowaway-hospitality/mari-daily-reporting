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
     a thin SKU falls back to its reporting-group index.
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
        assert set(d) == {"pour_wk", "recipe_wk", "variance_wk", "true_wk"}
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
