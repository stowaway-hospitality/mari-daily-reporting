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

# 5. A refund cannot lift coverage above 100%. Marilyna's published 102.3% on a
#    day with a discount row: the negative row shrank the denominator only.
disc = [{"rev": 100, "cost": 25, "cost_source": "recipe"},
        {"rev": -20, "cost": 0,  "cost_source": "lightspeed"}]   # a discount
_, _, cov = blend_reported_cogs(disc, 25, 80)
check("a discount row cannot push coverage over 100%", cov <= 100.0 and approx(cov, 100.0))

# 6. A REJECTED blend publishes Lightspeed's number, so it must publish
#    Lightspeed's coverage too — which is none. It used to carry the recipe
#    coverage through unchanged and advertise it beside a number no recipe
#    touched.
rej = [{"rev": 100, "cost": 5000, "cost_source": "recipe"}]
cogs, src, cov = blend_reported_cogs(rej, cogs_lightspeed=22, revenue_net=100)
check("a rejected blend reports 0% coverage, not the recipe's",
      src == "lightspeed" and cov == 0.0)

# 7. THE LABEL MUST TELL THE TRUTH. With no recipe anywhere, cogs_recipe is just
#    the sum of Lightspeed's own per-product costs, the plausibility check passes,
#    and this shipped LS's figure labelled "recipe_blend" at 0.0% coverage. Every
#    published day on main looks exactly like this today.
none = [{"rev": 100, "cost": 30, "cost_source": "lightspeed"},
        {"rev": 50,  "cost": 10, "cost_source": "lightspeed"}]
cogs, src, cov = blend_reported_cogs(none, cogs_lightspeed=40, revenue_net=150)
check("a blend of nothing is labelled lightspeed, not recipe_blend",
      src == "lightspeed" and cov == 0.0 and approx(cogs, 40))

print("ALL PASS" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
