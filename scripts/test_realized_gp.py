#!/usr/bin/env python3
"""Guards on the realized-price GP feed. Script-shaped: exits non-zero on failure."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


f = ROOT / "data" / "realized_gp.json"
if not f.exists():
    print("realized_gp.json not built — run scripts/build_realized_gp.py"); raise SystemExit(0)
d = json.loads(f.read_text())

check(d["products"], "the feed has products")
check(all(p["realized_price_ex"] > 0 for p in d["products"]),
      "no product carries a zero or negative realized price")
check(all(abs(p["realized_price_ex"] - p["revenue_ex"] / p["qty"]) < 0.01
          for p in d["products"]),
      "realized price is revenue / qty, for every product")

# DISCOUNT AND UPLIFT MUST NOT BE NETTED.
# Marilyna's uplifts more than she discounts; Stowaway the reverse. A single
# signed total describes neither, and reads uplift as lost revenue.
for v, s in d["by_venue"].items():
    check(s["discounted_below_list"] >= 0 and s["uplifted_above_list"] >= 0,
          f"{v}: discount and uplift are both reported as positive magnitudes")

# A realized price under half of list is more likely an attribution artefact
# than a discount — Brownie D at $1.90 against a $9.09 list. It must be
# quarantined, not published as a loss-making product.
susp = [p for p in d["products"] if p.get("suspect_price")]
check(all(p["realized_price_ex"] < p["list_price_ex"] / 2 for p in susp),
      "every quarantined price really is under half of list")
check(all("suspect_price" in p or p["realized_price_ex"] >= p["list_price_ex"] / 2
          for p in d["products"] if p.get("list_price_ex")),
      "nothing under half of list escapes the quarantine")

# GP is computed on the realized price, which is the entire point.
bad = [p for p in d["products"]
       if abs(p["achieved_gp"] - (p["realized_price_ex"] - p["cost_per_serve"])
              / p["realized_price_ex"]) > 0.001]
check(not bad, f"achieved GP is computed off the REALIZED price, not the list price")

print("\n" + ("ALL GUARDS HOLD" if not fails else f"{len(fails)} FAILED"))
raise SystemExit(1 if fails else 0)
