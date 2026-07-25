"""
Chef pack-confirmations must (a) parse as an append-only log with last-wins, and
(b) make an otherwise-unreadable ingredient costable — the whole point of the
/pack loop. Without this, a confirmed pack stays a session-only guess and the
recipe that uses it can never cost.
"""

from __future__ import annotations

import csv
from decimal import Decimal

from core.domain import purchasable_id
from core.pack_overrides import load_pack_overrides
from modules.recipes.pipeline import build_costs as bc


def test_load_pack_overrides_last_confirmation_wins(tmp_path):
    p = tmp_path / "po.yaml"
    p.write_text('- id: "x:1"\n  pack_qty: 500\n  pack_unit: g\n'
                 '- id: "x:1"\n  pack_qty: 2000\n  pack_unit: g\n')   # re-confirmed bigger
    assert load_pack_overrides(p) == {"x:1": (Decimal("2000"), "g")}


def test_load_pack_overrides_ignores_junk(tmp_path):
    assert load_pack_overrides(tmp_path / "missing.yaml") == {}
    p = tmp_path / "bad.yaml"
    p.write_text('- id: "a:1"\n  pack_qty: 0\n  pack_unit: g\n'      # zero qty -> dropped
                 '- id: "b:2"\n  pack_unit: g\n'                      # no qty -> dropped
                 '- id: "c:3"\n  pack_qty: 100\n  pack_unit: ""\n')   # no unit -> dropped
    assert load_pack_overrides(p) == {}


def test_override_makes_an_unreadable_pack_costable(tmp_path, monkeypatch):
    iid = purchasable_id("Foodlink", "abc")
    cogs = tmp_path / "cogs.csv"
    with cogs.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["supplier", "supplier_code", "invoice_description",
                                          "cost_per_unit_incl_gst", "basis", "note", "venue",
                                          "source_invoice", "invoice_date"])
        w.writeheader()
        w.writerow({"supplier": "Foodlink", "supplier_code": "abc", "invoice_description": "MYSTERY 2.50",
                    "cost_per_unit_incl_gst": "2.50", "basis": "per_unit", "venue": "stowaway",
                    "source_invoice": "INV1", "invoice_date": "2026-07-25"})
    out = tmp_path / "costs.csv"
    ov = tmp_path / "po.yaml"
    ov.write_text(f'- id: "{iid}"\n  pack_qty: 2000\n  pack_unit: g\n')
    # force the "parser can't read the pack" case so the override path is exercised
    monkeypatch.setattr(bc, "resolve_pack", lambda *a, **k: (None, None, None, "unreadable", ""))
    monkeypatch.setattr(bc, "COGS", cogs)
    monkeypatch.setattr(bc, "OUT", out)
    monkeypatch.setattr(bc, "PACK_OVERRIDES", ov)
    monkeypatch.setattr(bc, "ROOT", tmp_path)
    bc.main()

    rows = [r for r in csv.DictReader(out.open()) if r["ingredient"] == iid]
    assert rows, "the confirmed ingredient must now have a cost observation"
    assert rows[0]["pack"] == "chef-confirmed"
    assert rows[0]["unit"] == "g"
    assert Decimal(rows[0]["cost_per_unit"]) == (Decimal("2.50") / Decimal("2000")).quantize(Decimal("0.000001"))
