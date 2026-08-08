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

# 5. THE LABEL MUST TELL THE TRUTH ABOUT WHERE THE NUMBER CAME FROM.
#
# Eight committed day files say `cogs_source: recipe_blend` beside
# `recipe_coverage_pct: 0.0`. Summing per-product LIGHTSPEED costs is not a
# recipe blend; calling it one claims a provenance the number does not have,
# which is a trust problem rather than a dollar one. The number is unchanged —
# only the label stops overstating.
none_on_recipe = [
    {"rev": 100, "cost": 30, "cost_source": "lightspeed"},
    {"rev": 60,  "cost": 20, "cost_source": "lightspeed"},
]
cogs, src, cov = blend_reported_cogs(none_on_recipe, cogs_lightspeed=50, revenue_net=160)
check("zero coverage is not called a recipe blend", src == "lightspeed")
check("...and the published number does not move", approx(cogs, 50))
check("...and coverage stays 0", approx(cov, 0.0))

# 6. A REJECTED blend must not publish its coverage.
# The blend was refused and none of it reaches the screen, so its coverage is
# not a fact about the number shown. This used to report the recipe share of a
# figure that had been thrown away.
rejected = [{"rev": 100, "cost": 5000, "cost_source": "recipe"}]
cogs, src, cov = blend_reported_cogs(rejected, cogs_lightspeed=22, revenue_net=100)
check("a rejected blend reports no coverage", src == "lightspeed" and approx(cov, 0.0))

# 7. Coverage cannot exceed 100%, whatever the rows do.
# A discount/void row carries NEGATIVE rev and never has a recipe, so it came
# off the denominator only — a share of a shrinking base. Marilyna's published
# 102.3% on exactly this shape.
discounted = [
    {"rev": 100, "cost": 25, "cost_source": "recipe"},
    {"rev": -8,  "cost": 0,  "cost_source": "lightspeed"},   # discount row
]
_, _, cov = blend_reported_cogs(discounted, cogs_lightspeed=25, revenue_net=92)
check("a discount row cannot push coverage over 100%", 0.0 <= cov <= 100.0)
check("...and it reads as full coverage, not 108%", approx(cov, 100.0))

print("ALL PASS" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
