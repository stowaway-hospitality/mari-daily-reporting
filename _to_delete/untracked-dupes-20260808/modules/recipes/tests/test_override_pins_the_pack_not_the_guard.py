"""
A chef-confirmed pack states the PACK. It does not state that the rate is sane.

THE GAP
-------
Both cost builders had the same line in their override block:

    qty, unit, bad, how = oq, ou, "", "chef-confirmed"

`bad` is the plausibility guard's answer — the smoke alarm that stops a
description stating the PIECE size while the price is for the CASE. Clearing it
meant that confirming one pack for a supplier code switched the alarm off for
EVERY line that code ever carries, including lines the supplier billed on a
different basis.

Foodlink bills 100487 camembert both ways, on the same code:

    2026-06-06  SI4396136  CHEESE CAMEMBERT 125GM            $ 3.80   note "EA"
    2026-06-09  SI4398567  CHEESE CAMEMBERT 125GM            $ 3.80   note "EA"
    2026-07-16  SI4467596  CHEESE CAMEMBERT 125GM Rosenberg  $45.60   note "UOM CTN-12"
    2026-07-23  SI4480678  CHEESE CAMEMBERT 125GM Rosenberg  $ 3.80   note "CTN-12"

The override pins 125 g, which is right for the piece the invoice usually
charges for and twelve times wrong for the carton. $45.60 / 125 g published
$0.364800/g — $364.80/kg — against the $30.40/kg the same code's own EA lines
charge and the $45.60/12/125g = $0.0304/g the carton note itself implies. Any
day recomputed between 16 and 22 July 2026 costed camembert at twelve times its
price, and nothing in the pipeline said a word: the ingredient read
`needs_pack_review: false` because a chef had confirmed something else.

data/pack_overrides.yaml:318-329 records this as a KNOWN TRADE-OFF. It is not a
trade-off that has to be made. An override is authoritative about the pack; the
bounds still get to judge what falls out of it, and a rate no food has is
skipped — which is what the rest of build_costs already does with an unreadable
pack.

MEASURED CONSEQUENCE OF TURNING THE GUARD BACK ON
-------------------------------------------------
Exactly ONE row in data/cogs_list.csv is newly refused: the camembert carton
above. 354 other override-applied rows are unaffected. Both other known cases of
this class sit INSIDE the deliberately wide bounds and are NOT reached by this
fix — Foodlink 100175 black beans at $0.0174/g (a CTN-6 carton against a 3000 g
tin pin, 6x dear, and $17.40/kg is a plausible price for a food) and Fresh Fruit
Team CL20KGBX carrots at $0.0275/g (a 20 kg box billed basis=per_kg, 20x dear).
audit_book's own-history rule already reports the black beans at 6.0x its median;
the carrot has only two observations, below that rule's floor. Both need
resolve_pack to read a CTN-n note by magnitude, which is a change to the invoice
pack reader and wants its own pass.

WHAT THIS GUARDS
----------------
- an override still decides the pack and the unit (it must keep correcting a
  wrongly-parsed "1 box" into a real weight)
- ...but the resulting rate is still judged, and an absurd one is skipped, not
  published
- a normal override-priced line is untouched
- and the invariant on the real book: nothing in costs.csv sits above the
  per-gram ceiling
"""

from __future__ import annotations

import csv
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.recipes.pipeline import build_costs as bc                   # noqa: E402
from modules.recipes.pipeline import build_ingredients as bi             # noqa: E402

CAMEMBERT = "foodlink:100487"

COGS_COLS = ["supplier", "supplier_code", "invoice_description",
             "cost_per_unit_incl_gst", "basis", "pack_qty", "pack_unit",
             "cost_per_base_unit", "venue", "source_invoice", "invoice_date", "note"]


def _cogs(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COGS_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COGS_COLS})


def _overrides(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def _camembert_rows():
    """The two real Foodlink lines, one per basis, both on code 100487."""
    common = dict(supplier="Foodlink", supplier_code="100487", basis="per_unit",
                  venue="stowaway", pack_qty="1", pack_unit="ea")
    return [
        dict(common, invoice_description="CHEESE CAMEMBERT 125GM",
             cost_per_unit_incl_gst="3.8000", note="EA",
             source_invoice="SI4396136", invoice_date="2026-06-06"),
        dict(common, invoice_description="CHEESE CAMEMBERT 125GM Rosenberg",
             cost_per_unit_incl_gst="45.6000", note="UOM CTN-12; stock unit UNKNOWN",
             source_invoice="SI4467596", invoice_date="2026-07-16"),
    ]


PIN_125G = ('- id: "foodlink:100487"\n'
            "  pack_qty: 125\n"
            "  pack_unit: g\n"
            '  by: "test"\n'
            "  on: 2026-08-05\n")


# --- the guard itself ------------------------------------------------------

def test_the_carton_rate_is_still_out_of_bounds():
    """$45.60 / 125 g is $364.80/kg. The bounds have always known that is not a
    price for camembert; the override block simply stopped asking."""
    assert bi.out_of_bounds(Decimal("0.364800"), "g")
    assert bi.out_of_bounds(Decimal("0.030400"), "g") is None


# --- build_costs -----------------------------------------------------------

def _run_costs(tmp_path, monkeypatch):
    _cogs(tmp_path / "cogs.csv", _camembert_rows())
    _overrides(tmp_path / "packs.yaml", PIN_125G)
    monkeypatch.setattr(bc, "COGS", tmp_path / "cogs.csv")
    monkeypatch.setattr(bc, "OUT", tmp_path / "costs.csv")
    monkeypatch.setattr(bc, "PACK_OVERRIDES", tmp_path / "packs.yaml")
    monkeypatch.setattr(bc, "PRODUCT_MAP", tmp_path / "nomap.csv")
    monkeypatch.setattr(bc, "ROOT", tmp_path)
    bc.main()
    return list(csv.DictReader((tmp_path / "costs.csv").open(encoding="utf-8-sig")))


def test_the_confirmed_pack_still_prices_the_line_it_was_confirmed_for(tmp_path,
                                                                       monkeypatch):
    """The override must keep doing its job: $3.80 for one 125 g piece is
    $0.030400/g, and that is the live price four days a week."""
    rows = _run_costs(tmp_path, monkeypatch)
    good = [r for r in rows if r["source_invoice"] == "SI4396136"]
    assert len(good) == 1
    assert good[0]["cost_per_unit"] == "0.030400"
    assert good[0]["unit"] == "g"
    assert good[0]["pack"] == "chef-confirmed"


def test_the_carton_line_is_skipped_instead_of_published_at_12x(tmp_path, monkeypatch):
    """The finding. Before the fix this row reached costs.csv at $0.364800/g and
    won the as-of lookup for 16-22 July."""
    rows = _run_costs(tmp_path, monkeypatch)
    assert not [r for r in rows if r["source_invoice"] == "SI4467596"], (
        "the CTN-12 carton line was published at 12x the piece price")


# --- build_ingredients (the chef-facing half, which must agree) ------------

def test_the_picker_flags_the_same_row_instead_of_calling_it_confirmed(tmp_path,
                                                                       monkeypatch):
    """build_ingredients had the identical line. An ingredient priced at
    $364.80/kg must not read `needs_pack_review: false` just because a chef
    confirmed a different pack for the same code."""
    import json
    from datetime import date, timedelta
    recent = (date.today() - timedelta(days=5)).isoformat()
    _cogs(tmp_path / "cogs.csv", [dict(_camembert_rows()[1], invoice_date=recent)])
    _overrides(tmp_path / "packs.yaml", PIN_125G)
    monkeypatch.setattr(bi, "ROOT", tmp_path)
    monkeypatch.setattr(bi, "COGS", tmp_path / "cogs.csv")
    monkeypatch.setattr(bi, "OUT", tmp_path / "ing.json")
    monkeypatch.setattr(bi, "PACK_OVERRIDES", tmp_path / "packs.yaml")
    bi.main()
    items = json.loads((tmp_path / "ing.json").read_text())["ingredients"]
    cam = [i for i in items if i["id"] == CAMEMBERT]
    assert cam and cam[0]["needs_pack_review"] is True
    assert "implausibly DEAR" in cam[0]["review_reason"]


# --- the invariant on the real book ---------------------------------------

def test_no_row_in_the_real_cost_book_is_priced_above_the_per_gram_ceiling():
    """The regression, on the real file. Before the fix costs.csv carried one
    row at $0.364800/g; nothing else in the book has ever been above $0.20/g."""
    costs = ROOT / "data" / "costs.csv"
    if not costs.exists():
        return                     # clean checkout: nothing generated yet
    dear = []
    for r in csv.DictReader(costs.open(encoding="utf-8-sig")):
        if r["unit"] != "g":
            continue
        if Decimal(r["cost_per_unit"]) > Decimal("0.20"):
            dear.append(f"{r['ingredient']} {r['observed_on']} "
                        f"${r['cost_per_unit']}/g  {r['description'][:34]}")
    assert not dear, "per-gram rates above $200/kg:\n  " + "\n  ".join(dear)
