"""`data/functions_pipeline.json` — the monday enquiry tracker as a feed.

Two things are proved here, and the numbers in them are the real board as it
stood on 2026-08-21, not fixtures.

**It reproduces.** The feed is committed, so MODULES.md rule 4 applies: a
derived file that no longer regenerates from its source is a fossil and every
line on it is quietly wrong. `--check` rebuilds it from
`data/functions_monday_raw.json` and byte-compares, the same contract
`data/costs.csv` and `data/functions_gp.json` live under. It is here rather
than in a workflow step because `.github/workflows/` needs the `ops` claim, and
because a pytest also runs before the push instead of after it.

**The reading of the log is the reading a person would give.** `whose_move` is
the whole point of this feed — it is the one thing the board knows that no
other system does — and it is derived by matching phrases in prose. That is
exactly the kind of rule that rots silently: a wording change in the autodraft
turns "waiting on us" into "waiting on them", nothing errors, and the screen
starts saying there is nothing to do. So the verdict is asserted against named
rows, with the sentence it was read off quoted in the test.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.functions.enquiries import (                     # noqa: E402
    DRINK_MAP, brief_prefill, build, outstanding, parse_log, title_date,
    whose_move)

RAW = ROOT / "data" / "functions_monday_raw.json"
FEED = ROOT / "data" / "functions_pipeline.json"
SCHEMA = ROOT / "data" / "schemas" / "functions_pipeline.schema.json"
BUILD = "modules/functions/pipeline/build_functions_pipeline.py"


def feed():
    return json.loads(FEED.read_text(encoding="utf-8"))


def by_name(name):
    return next(e for e in feed()["enquiries"] if e["name"] == name)


# ------------------------------------------------------------ it reproduces
def test_the_feed_reproduces_byte_for_byte_from_the_capture():
    r = subprocess.run([sys.executable, BUILD, "--check"], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_capture_and_the_feed_hold_the_same_sixty_rows():
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    f = feed()
    assert len(raw["items"]) == raw["items_count"] == 60
    assert len(f["enquiries"]) == 60 == f["counts"]["total"]
    assert {i["id"] for i in raw["items"]} == {e["item_id"] for e in f["enquiries"]}


def test_nothing_is_filtered_out_including_the_archive():
    """Zak: "the pipeline should reflect all enquiries, whether or not they've
    been replied to." Thirty-six of the sixty are archived — more than half —
    so a pipeline that dropped them would report 24 and look exactly like the
    bug it replaced."""
    c = feed()["counts"]["by_group"]
    assert c == {"Harry Gatos": 8, "Stowaway Bar": 16, "archive": 36}
    assert sum(c.values()) == 60


def test_the_feed_says_when_the_board_was_read():
    f = feed()
    assert f["captured_at"].startswith("2026-08-21"), f["captured_at"]
    assert f["schema"] == json.loads(SCHEMA.read_text(encoding="utf-8"))["title"]


def test_every_required_field_the_schema_names_is_present():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    required = schema["$defs"]["enquiry"]["required"]
    codes = set(schema["$defs"]["flag"]["properties"]["code"]["enum"])
    moves = set(schema["$defs"]["enquiry"]["properties"]["whose_move"]["enum"])
    for e in feed()["enquiries"]:
        assert not [k for k in required if k not in e], e["name"]
        assert e["whose_move"] in moves
        for fl in e["flags"]:
            assert fl["code"] in codes, (e["name"], fl["code"])
        assert e["min_spend_cents"] is None or isinstance(e["min_spend_cents"], int)


# ------------------------------------------------------------- whose move
def test_the_log_parses_into_dated_entries():
    log = parse_log(by_name("Emma - 3 Sep")["notes"])
    assert len(log) == 6
    assert log[0]["date"] == "2026-08-13" and log[0]["author"] == "auto"
    assert log[-1]["date"] == "2026-08-18"
    # Diane's last entry was written by a person, not the automation, and the
    # author is kept: "[2026-08-15 Zak]" is a different kind of evidence from
    # a line the mailbox scraper wrote.
    assert parse_log(by_name("Diane - 15 Aug")["notes"])[-1]["author"] == "Zak"


def test_waiting_on_them_is_only_said_when_the_log_says_it():
    e = by_name("Lisbet - 21 Nov")
    assert e["whose_move"] == "them"
    assert e["whose_move_since"] == "2026-08-21"
    assert "no response since our 12 Aug email" in e["whose_move_evidence"]


def test_a_chase_in_the_last_entry_outranks_awaiting_a_reply():
    """"Awaiting customer reply — no reply since our first reply on 14 Jul
    (23 days). Large enquiry (100 pax, 28 Nov) — worth a chase." says both.
    The chase wins: "awaiting a reply" states what last happened, "worth a
    chase" names the next action, and the next action is ours."""
    e = by_name("Ariyah - 28 Nov")
    assert e["whose_move"] == "us"
    assert e["whose_move_why"] == "the log says it is worth a chase"
    assert "worth a chase" in e["whose_move_evidence"]


def test_an_unanswered_question_is_our_move():
    e = by_name("Maryanne - 16th aug")
    assert e["whose_move"] == "us"
    assert "unanswered" in e["whose_move_evidence"]


def test_a_correction_asked_for_is_our_move():
    e = by_name("Michael - 25 Oct")
    assert e["whose_move"] == "us"
    assert "start time should be 2pm, not 6pm" in e["whose_move_evidence"]


def test_a_fresh_enquiry_with_no_reply_logged_is_our_move():
    e = by_name("Elizabeth - 10 Oct")
    assert e["whose_move"] == "us"
    assert e["log_entries"] == 1


def test_no_notes_at_all_is_nobody_rather_than_anybody():
    e = by_name("Charlotte")
    assert e["whose_move"] == "nobody"
    assert e["whose_move_evidence"] is None
    assert e["notes"] is None


def test_a_note_that_is_not_a_dated_log_cannot_say_whose_move_it_is():
    e = by_name("Saxon Wyatt - confirmed")
    assert e["whose_move"] == "unclear"
    assert e["whose_move_evidence"] == "Regular - local super fun guy. I said no deposit."
    assert e["log_entries"] == 0


def test_a_bare_customer_reply_is_not_read_as_either_side():
    """The log records replies going both ways. "Customer replied 7 Jul: tried
    to call (no answer) ... will try calling again tomorrow afternoon" does not
    say whether we answered, and it does not say she did not. Guessing "waiting
    on them" here is the expensive direction: it is indistinguishable from
    "nothing to do"."""
    e = by_name("Kathy - 31 Jul")
    assert e["whose_move"] == "unclear"
    assert "the last entry is a customer reply" in e["whose_move_why"]


def test_a_truncated_last_entry_is_never_given_a_verdict():
    e = by_name("Emma - 3 Sep")
    assert e["notes_truncated"] and e["notes_chars"] == 2000
    assert e["whose_move"] == "unclear"
    assert e["whose_move_evidence"].endswith("a guitarist has a B...")


def test_the_four_verdicts_across_the_whole_board():
    """The distribution itself, pinned. A wording change in the autodraft that
    quietly reclassifies a dozen rows moves these numbers and fails here,
    which is the only way anyone would ever notice."""
    assert feed()["counts"]["by_whose_move"] == {
        "nobody": 19, "them": 12, "unclear": 6, "us": 23}


def test_every_verdict_carries_its_evidence_and_its_reason():
    for e in feed()["enquiries"]:
        assert e["whose_move_why"], e["name"]
        if e["whose_move"] != "nobody":
            assert e["whose_move_evidence"], e["name"]


def test_whose_move_reads_the_last_entry_and_not_an_earlier_one():
    """Marcus's log has an "Awaiting customer reply" line in August under four
    earlier lines, two of which are customer replies. An implementation that
    scanned the whole note would find whichever phrase it liked first."""
    older = ("[2026-07-01 auto] Awaiting customer reply — nothing back.\n"
             "[2026-07-09 auto] Customer replied 8 Jul: needs a reply.")
    assert whose_move(older)["who"] == "us"
    assert whose_move(older)["since"] == "2026-07-09"


# -------------------------------------------------------- the awkward data
def test_the_row_titled_10th_oct_whose_form_said_3_october_is_flagged_not_resolved():
    e = by_name("Marcus - 10th Oct")
    fl = [f for f in e["flags"] if f["code"] == "date_conflict"]
    assert len(fl) == 1
    assert "2026-10-10" in fl[0]["note"] and "2026-10-03" in fl[0]["note"]
    # The feed does NOT pick one. The event_date column is carried through as
    # it stands and the disagreement is published beside it.
    assert e["event_date"] == "2026-10-03"


def test_title_dates_that_agree_raise_nothing():
    assert title_date("Michael - 25 Oct", "2026-10-25") == "2026-10-25"
    assert title_date("Cameron - 21 May 2027", "2027-05-21") == "2027-05-21"
    assert title_date("Roman", "2026-08-08") is None
    assert not [f for f in by_name("Michael - 25 Oct")["flags"]
                if f["code"] == "date_conflict"]


def test_the_two_enquiries_for_28_november_each_name_the_other():
    a, b = by_name("Ariyah - 28 Nov"), by_name("Belinda - 28 Nov")
    fa = next(f for f in a["flags"] if f["code"] == "date_shared")
    fb = next(f for f in b["flags"] if f["code"] == "date_shared")
    assert fa["others"] == [b["item_id"]] and fb["others"] == [a["item_id"]]
    assert a["group_size"] == b["group_size"] == 100


def test_a_shared_date_needs_both_rows_live_and_at_the_same_venue():
    """19 September carries Heather (Stowaway) and Ruth (Harry Gatos), which is
    two rooms in two buildings and not a clash. Keying on the date alone
    reported thirty of these, most of them noise, and a flag that is usually
    noise is a flag people stop reading."""
    assert not [f for f in by_name("Ruth")["flags"] if f["code"] == "date_shared"]
    assert not [f for f in by_name("Heather - 19 Sep")["flags"]
                if f["code"] == "date_shared"]
    # Roman and Harry really were two functions on one night — but both are
    # archived, and 8 August has happened.
    assert not [f for f in by_name("Roman")["flags"] if f["code"] == "date_shared"]


def test_a_note_at_mondays_cap_is_reported_as_incomplete():
    cut = [e["name"] for e in feed()["enquiries"] if e["notes_truncated"]]
    assert cut == ["Emma - 3 Sep", "Maryanne - 16th aug"]
    for name in cut:
        e = by_name(name)
        assert e["notes_chars"] == 2000
        assert any(f["code"] == "notes_truncated" for f in e["flags"])


def test_a_row_with_no_date_no_size_and_no_notes_is_still_published():
    e = by_name("CJ Mckenzie")
    assert e["event_date"] is None and e["group_size"] is None and e["notes"] is None
    assert e["whose_move"] == "nobody"
    assert set(e["outstanding"]) == {"date", "start time", "room",
                                     "guest count", "food choice", "drink choice"}


def test_a_row_with_nobody_to_chase_says_so():
    e = by_name("Roman")
    assert e["contactable"] is False
    assert any(f["code"] == "no_contact" for f in e["flags"])


# --------------------------------------------------------- the newer columns
def test_the_columns_added_in_august_are_carried():
    """Drinks, bar tab covers, food, deposit, music, settling up and min spend.
    Diane's row is the one that has most of them, and it is the one where the
    facts arrived by SMS rather than through the mailbox sync."""
    e = by_name("Diane - 15 Aug")
    assert e["drinks"] == "Bar tab"
    assert e["bar_tab_covers"].startswith("Not prepaid.")
    assert e["food"] == "Food list to drop through the night"
    assert e["settling_up"] == "Running a tab, not prepaid - settled on the night"
    assert e["min_spend_dollars"] == 1500 and e["min_spend_cents"] == 150000
    assert by_name("Heather")["music"] == "Band"
    assert by_name("Michael - 25 Oct")["deposit"] == "Paid"


def test_an_open_bar_tab_with_nothing_written_about_it_is_outstanding():
    """The board's own column description: an open-ended tab with nothing here
    "is the single most common way a function bill turns into an argument"."""
    assert "bar tab terms" in outstanding(
        {"drinks": "Bar tab", "bar_tab_covers": None})
    assert "bar tab terms" not in by_name("Diane - 15 Aug")["outstanding"]


# ---------------------------------------------------------- into a brief
def test_the_brief_is_keyed_so_a_second_press_converges():
    e = by_name("Diane - 15 Aug")
    assert e["source_ref"] == "monday:2778822241"
    assert e["brief_prefill"]["source_ref"] == e["source_ref"]


def test_a_package_name_is_translated_into_the_engines_own_vocabulary():
    """functions.validate() checks `drink` against DRINK_CHOICES because a
    package name IS a price. The board's label carries the price in it, so
    "SOIRÈE $60pp" would be rejected as an unknown package."""
    assert DRINK_MAP["SOIRÈE $60pp"] == "SOIRÈE"
    assert DRINK_MAP["Not decided yet"] is None
    assert by_name("Diane - 15 Aug")["brief_prefill"]["drink"] == "bar tab"
    assert by_name("Diane - 15 Aug")["brief_prefill"]["tab_restriction"]


def test_an_area_the_engine_would_refuse_is_not_sent_at_all():
    """"Whole venue" and "Not sure yet" are both real board answers and neither
    is an area a brief may name. Sent as nothing rather than as a guess: the
    panel then shows the room as unanswered, which is true."""
    row = {"group": "Stowaway Bar", "source_ref": "monday:1", "name": "x",
           "email": None, "phone": None, "occasion": None, "event_date": None,
           "start_time": None, "area": "Whole venue", "group_size": None,
           "food": None, "drinks": None, "bar_tab_covers": None, "music": None,
           "settling_up": None, "source": None, "min_spend_dollars": None,
           "notes": None}
    assert "area" not in brief_prefill(row)
    row["area"] = "Old Stow"
    assert brief_prefill(row)["area"] == "Old Stow"


def test_harry_gatos_is_tracked_but_never_offered_a_deposit():
    """There is no floor plan for Harry Gatos in the booking engine, so a brief
    there could never hold a room. Offering a button that mints a deposit link
    against a room nobody can hold is worse than offering nothing."""
    hg = [e for e in feed()["enquiries"] if e["group"] == "Harry Gatos"]
    assert len(hg) == 8
    for e in hg:
        assert e["brief_prefill"] is None
        assert any(f["code"] == "no_floor_plan" for f in e["flags"])


def test_the_brief_never_exceeds_the_engines_own_field_limits():
    for e in feed()["enquiries"]:
        p = e["brief_prefill"]
        if not p:
            continue
        assert len(p["name"]) <= 120
        assert len(p.get("notes", "")) <= 2000
        assert len(p.get("settlement", "")) <= 200
        assert len(p.get("tab_restriction", "")) <= 200
        assert len(p.get("occasion", "")) <= 120
        if "guests" in p:
            assert 1 <= p["guests"] <= 500
