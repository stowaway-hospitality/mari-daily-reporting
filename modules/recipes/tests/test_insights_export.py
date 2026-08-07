"""
A Lightspeed export we cannot read must never be indistinguishable from a quiet day.

Lightspeed renamed the Sales-by-Product columns mid-week on 2026-07-11.
build_products_weekly.py knew only the new names, read every old-schema file as a
stack of footer rows, and published week-ending 2026-07-12 at $15,955 against
$67,000 in the daily history. $51,046 vanished with no error, no warning and no
empty file — just a smaller number on the dashboard, which is the one failure
shape nobody goes looking for.

So the contract is: parse both real shapes, and for anything else say which file
and why. The only silence allowed is a closed day (Stow shuts Mondays, HG
Tuesdays), where Lightspeed serves a header-only file and nothing was lost.
"""

from __future__ import annotations

import pytest

from core.insights_export import (
    InsightsSchemaError, WrongReportError, ex_gst, read_insights)

NEW = ("Product Name,Product Quantity,$ Sales,Total Tax,Cost,% of Quantity,% of Sale Amount,Gross Profit %\n"
       "Large Margherita,5,$110.00,$10.00,$11.48,5%,5%,89%\n"
       ",,\"$110.00\",,,,,\n")                                  # footer row
OLD = ("Position,Product Number,Product,Quantity,Percent of Quantity,Sale Amount,"
       "Percent of Sale Amount,Cost,Percent of Gross Profit\n"
       "1,,Large Margherita,5,5,110,5,11.48,89\n")
GROUPS = ("Reporting Group Name,Total Quantity,$ Sales,Total Tax,Cost,# of Sales,"
          "# of products,% of Quantity,% of Sale Amount,Gross Profit %\n")


def _w(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_both_schemas_read_the_same_day_the_same_way(tmp_path):
    new = read_insights(_w(tmp_path, "insights_stow_2026-07-11.csv", NEW))
    old = read_insights(_w(tmp_path, "insights_stow_2026-07-10.csv", OLD))
    assert len(new) == len(old) == 1, "the footer row is dropped, the product row is not"
    assert new[0]["name"] == old[0]["name"] == "Large Margherita"
    assert new[0]["qty"] == old[0]["qty"] == 5
    # NEW states tax; OLD has no tax column, so ex falls back to /1.1. $110 inc
    # with $10 GST is the same $100 either way — that equivalence is the whole
    # reason one week can mix the two shapes and still add up.
    assert ex_gst(new[0]) == pytest.approx(100.0)
    assert ex_gst(old[0]) == pytest.approx(100.0)


def test_a_zip_committed_as_a_csv_is_named_not_crashed(tmp_path):
    # data/insights_2026-07-11.csv really was this: the raw Lightspeed download
    # bundle, committed unopened. csv.DictReader died on its NULs three frames
    # deep in _mean_large_pizza_cost and took the whole recipe build with it.
    p = tmp_path / "insights_2026-07-11.csv"
    p.write_bytes(b"PK\x03\x04\x14\x00\x00\x00\x08\x00rubbish\x00\x00")
    with pytest.raises(InsightsSchemaError, match="ZIP archive"):
        read_insights(p)


def test_the_wrong_report_with_rows_is_an_error(tmp_path):
    p = _w(tmp_path, "insights_hg_2026-07-12.csv", GROUPS + "Tap Beer [Harrys],4,$49.50,$4.50,$8.72,4,1,29%,51%,82%\n")
    with pytest.raises(WrongReportError, match="reporting-groups"):
        read_insights(p)


def test_the_wrong_report_with_no_rows_is_a_closed_day(tmp_path):
    # HG is shut Tuesdays, Stow Mondays. Lightspeed serves a header-only file of
    # whatever report it likes. Nothing was lost, so nothing is said — otherwise
    # every closed day cries wolf and the real misses stop being read.
    assert read_insights(_w(tmp_path, "insights_hg_2026-07-14.csv", GROUPS)) == []


def test_an_unknown_header_refuses_rather_than_publishing_empty(tmp_path):
    p = _w(tmp_path, "insights_stow_2027-01-01.csv", "Widget,Thing,Amount\na,1,2\n")
    with pytest.raises(InsightsSchemaError, match="unrecognised"):
        read_insights(p)


def test_a_mixed_schema_week_adds_up(tmp_path):
    """The regression itself. Six old-schema days and one new-schema day is a
    real week in this repo; reading only one shape loses six sevenths of it."""
    week = [_w(tmp_path, f"insights_stow_2026-07-{d:02d}.csv", OLD) for d in range(6, 11)]
    week.append(_w(tmp_path, "insights_stow_2026-07-11.csv", NEW))
    week.append(_w(tmp_path, "insights_stow_2026-07-12.csv", OLD))
    total = sum(ex_gst(r) for f in week for r in read_insights(f))
    assert total == pytest.approx(700.0), "7 days x $100 ex — not $100 for the one new-schema day"
