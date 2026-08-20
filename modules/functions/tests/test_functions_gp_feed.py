"""`data/functions_gp.json` is COMMITTED, so it has to keep reproducing.

A derived file that no longer regenerates from its sources is a fossil, and
every number on it is quietly wrong (MODULES.md rule 4). The other derived
feeds in this repo are gitignored and rebuilt on every CI run, which enforces
that for free. This one cannot be: adding a build step to `.github/workflows/`
needs the `ops` claim, and the pass that wrote it did not hold one.

So the guarantee is moved here instead. `--check` rebuilds the feed from
`data/function_tabs/*.json` and byte-compares. That is the same contract
`data/costs.csv` lives under, and it is strictly stronger than a workflow step
because it also runs on every developer's machine before the push.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FEED = ROOT / "data" / "functions_gp.json"
SCHEMA = ROOT / "data" / "schemas" / "functions_gp.schema.json"
BUILD = "modules/functions/pipeline/build_functions_gp.py"


def test_the_feed_reproduces_byte_for_byte_from_its_sources():
    r = subprocess.run([sys.executable, BUILD, "--check"], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_feed_matches_the_schema_it_claims():
    """Hand-walked rather than via `jsonschema`, which is not in
    requirements.txt and so is not guaranteed to be in CI. The check that
    matters is the required/type contract, and that is cheap to walk."""
    feed = json.loads(FEED.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert feed["schema"] == schema["title"]

    required = schema["$defs"]["outcome"]["required"]
    codes = set(schema["$defs"]["caveat"]["properties"]["code"]["enum"])
    assert feed["functions"], "an empty feed proves nothing"

    for o in feed["functions"]:
        missing = [k for k in required if k not in o]
        assert not missing, f"{o.get('id')}: missing {missing}"
        for k, v in o.items():
            if k.endswith("_cents") and v is not None:
                assert isinstance(v, int) and not isinstance(v, bool), \
                    f"{o['id']}.{k} = {v!r} — money is whole cents, never a float"
        assert o["caveats"], f"{o['id']}: a GP with no caveats renders as a refusal"
        for c in o["caveats"]:
            assert c["code"] in codes, f"{o['id']}: undeclared caveat {c['code']!r}"
            assert c.get("note"), f"{o['id']}: caveat {c['code']} has no words"
        assert o["gp_basis"] == "beverage"


def test_every_published_night_names_the_booking_it_is():
    """The join key, checked at the source rather than only at the screen.

    The feed knows what the bar called the tab; the diary knows who booked.
    "Dazzle drinks" is not a customer name and 8 August 2026 carries TWO
    functions, so neither the name nor the date can pair them -- `booking_id`
    is the entire join, and an entry without one is a report that can never
    reach a screen.

    Two entries claiming ONE booking is the failure worth a test of its own: it
    files one night's gross profit under another night's name, and nothing on
    the screen looks wrong while it does.
    """
    feed = json.loads(FEED.read_text(encoding="utf-8"))
    ids = []
    for o in feed["functions"]:
        assert o.get("booking_id"), f"{o['id']}: no booking_id -- joins to nothing"
        assert len(o.get("booking_evidence") or "") > 40, \
            f"{o['id']}: no evidence for the pairing, so nobody can check it"
        ids.append(o["booking_id"])
    assert len(set(ids)) == len(ids), f"two functions claim one booking: {ids}"


def test_the_tab_files_are_where_the_booking_id_comes_from():
    """The id is recorded by a human on the tab and copied, never derived."""
    tabs = ROOT / "data" / "function_tabs"
    feed = json.loads(FEED.read_text(encoding="utf-8"))
    by_source = {o["source_file"]: o for o in feed["functions"]}
    for path in sorted(tabs.glob("*.json")):
        if path.name.startswith("cost_book_"):
            continue
        tab = json.loads(path.read_text(encoding="utf-8"))
        entry = by_source[f"data/function_tabs/{path.name}"]
        assert entry["booking_id"] == tab.get("booking_id")
        assert entry["booking_evidence"] == (tab.get("booking_evidence") or None)


def test_every_field_the_page_reads_is_on_the_feed():
    """The join that no test on either side of it can see.

    `dashboard/functions/functions.js` reads an outcome by field name. If the
    pipeline renames one, the page reads `undefined`, `pct()` draws an em dash,
    nothing throws and nothing 404s — the report simply loses a figure. So the
    field names the page actually reaches for are extracted from its source and
    checked against a real published entry.
    """
    js = (ROOT / "dashboard" / "functions" / "functions.js").read_text(encoding="utf-8")
    feed = json.loads(FEED.read_text(encoding="utf-8"))
    entry = feed["functions"][0]

    # Everything the page reads off an outcome object, taken from the source of
    # the four functions that render one.
    reads = ["gp_pct", "gp_pct_ex_mixer", "gp_basis", "benchmark_gp_pct",
             "out_earn_ratio", "margin_foregone_ex_cents", "drinks_poured",
             "drinks_per_head", "drinks_per_hour", "package_hours",
             "cogs_ex_cents", "mixer_est_ex_cents", "total_cogs_ex_cents",
             "cogs_ex_cents_per_head", "menu_value_inc_cents",
             "menu_value_inc_cents_per_head", "revenue_inc_cents",
             "actual_heads", "booked_guests", "tickets_sold", "pos_refs",
             "caveats", "booking_id", "booking_evidence", "cost_book_as_of",
             "source_file"]
    for field in reads:
        assert f"o.{field}" in js or f"'{field}'" in js, \
            f"{field} is not read by the page — has the page changed?"
        assert field in entry, \
            f"the page reads o.{field} and the feed does not publish it"
