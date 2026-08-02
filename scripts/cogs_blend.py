#!/usr/bin/env python3
"""Reported ("estimated") COGS = our own recipe cost where we have a recipe,
Lightspeed's cost elsewhere.

Kept as its own tiny, import-cheap module so it is unit-testable without importing
daily_aggregator (which runs a full pull on import). See COGS_ARCHITECTURE.md: the
dashboard's estimated COGS is what we ACTUALLY used (recipe x invoice cost), not
Lightspeed's stale Average-Cost figure; Xero purchases stay the separate ACTUAL
COGS feed. Products without a recipe keep LS's cost as a visible per-product
fallback, and recipe_coverage_pct reports how much of revenue is on a real recipe.
"""
from __future__ import annotations


def blend_reported_cogs(product_breakdown, cogs_lightspeed, revenue_net):
    """(cogs, source, coverage_pct) for the reported estimated COGS.

    product_breakdown: rows with 'cost' (already qty x unit cost), 'rev' and
    'cost_source' in {'recipe','lightspeed'}. cogs_lightspeed: the all-LS total,
    used as the fallback. Fails toward review: an implausible blend (implied GP
    outside 0-100%, or negative cost) falls back to Lightspeed rather than ship a
    broken/flattering GP to the board.
    """
    cogs_recipe = sum((p.get("cost") or 0) for p in product_breakdown)
    recipe_rev = sum((p.get("rev") or 0) for p in product_breakdown
                     if p.get("cost_source") == "recipe")
    prod_rev = sum((p.get("rev") or 0) for p in product_breakdown)
    coverage = (recipe_rev / prod_rev * 100) if prod_rev else 0.0

    ok = (bool(revenue_net) and cogs_recipe >= 0
          and 0.0 <= (revenue_net - cogs_recipe) / revenue_net <= 1.0)
    if ok:
        return cogs_recipe, "recipe_blend", coverage
    return cogs_lightspeed, "lightspeed", coverage
