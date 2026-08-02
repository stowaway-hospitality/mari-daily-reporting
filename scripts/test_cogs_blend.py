#!/usr/bin/env python3
"""Guards on the reported-COGS blend. Script-shaped: exit 0 pass, 1 fail."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from cogs_blend import blend_reported_cogs

def approx(a, b, t=1e-6): return abs(a - b) <= t
fails = []
def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond: fails.append(name)

# 1. Blend sums per-product cost (recipe where present, LS cost elsewhere).
pb = [
    {"rev": 100, "cost": 25, "cost_source": "recipe"},      # 75% GP dish
    {"rev": 50,  "cost": 10, "cost_source": "lightspeed"},  # no recipe yet
]
cogs, src, cov = blend_reported_cogs(pb, cogs_lightspeed=40, revenue_net=150)
check("blend sums per-product cost", approx(cogs, 35))
check("source is recipe_blend when sane", src == "recipe_blend")
check("coverage = recipe rev / product rev", approx(cov, 100/150*100))

# 2. Fail toward review: an absurd recipe cost (GP < 0) falls back to LS.
bad = [{"rev": 100, "cost": 5000, "cost_source": "recipe"}]
cogs, src, cov = blend_reported_cogs(bad, cogs_lightspeed=22, revenue_net=100)
check("implausible blend falls back to Lightspeed", approx(cogs, 22) and src == "lightspeed")

# 3. Zero-revenue day never divides by zero.
cogs, src, cov = blend_reported_cogs([], cogs_lightspeed=0, revenue_net=0)
check("zero revenue is safe", cogs == 0 and cov == 0.0)

# 4. 100% recipe coverage reports 100.
full = [{"rev": 80, "cost": 20, "cost_source": "recipe"}]
_, _, cov = blend_reported_cogs(full, 20, 80)
check("full coverage reports 100%", approx(cov, 100.0))

print("ALL PASS" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
