"""
The domain core. Identity and time.

ARCHITECTURE.md decisions 1 and 2, as code. Everything above the fact layer
depends on this; this depends on nothing.

Two ideas, both load-bearing:

IDENTITY — two layers, not one
------------------------------
    Purchasable   (supplier, supplier_code)   what you BUY. The invoice gives it.
    Ingredient    canonical id                what a RECIPE says.
    map           Purchasable --many-to-one--> Ingredient

If recipes referenced supplier codes, changing supplier would break every recipe
that used the item and snap its cost history. That is exactly the hole
Lightspeed is in ("new suppliers since the food menu was updated"). With the
map, switching suppliers is one line of config: recipes keep working, and the
cost series stays continuous across the switch because both purchasables point
at the same ingredient.

TIME — everything effective-dated
---------------------------------
A cost is an OBSERVATION ON A DATE, never a current value. Ask for the cost
"as of" a day and you get what it cost then.

    cost_as_of(ing, d) = most recent observation on or before d

This exists to kill one specific bug: if recipes read a *current* cost, then
recomputing July's COGS in November prices July's dishes at November's costs,
and history silently rewrites itself. That is Average Cost Price's disease --
the thing this project exists to escape.

THE INVARIANT, and it is a test:

    Recomputing any past day gives the same answer. Forever.
"""

from __future__ import annotations

import csv
import re
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- identity ---

# Fresh Fruit Team (and occasionally others) leak the UNIT word out of its own
# column and onto the end of the supplier CODE during the PDF parse: "AH20T Tray",
# "ONBRKG Kilogram", "HCMB Market". The bleed is a property of the PARSE, not of
# the product, and it comes and goes between parser generations — so the same
# avocado arrives as "AH20T" one week and "AH20T Tray" the next.
_CODE_UNIT_SUFFIX = re.compile(
    r"\s+(tray|kilogram|kilo|kgs?|litres?|market|each|ea|punnet|box(?:es)?|"
    r"bunch|bch|bags?|dozen|doz|ctn|carton)$", re.I)


def normalize_code(code: str) -> str:
    """Strip a trailing unit word bled into the code. Idempotent, multi-pass
    ('X Kg Each' -> 'X'). Never returns empty — falls back to the original.

    THIS LIVES IN core BECAUSE IT IS PART OF IDENTITY. It used to live in
    build_ingredients and be applied only when collapsing the chef-facing picker,
    never to the cost key — so one product became two priced series:

        fresh-fruit-team:AH20T        2026-07-25  $30.80/tray  (n=7)
        fresh-fruit-team:AH20T TRAY   2026-08-04  $26.40/tray  (n=14)

    53 split identities in the cost book, 36 of them holding a DIFFERENT latest
    price on each half and 5 holding a different UNIT (EGL7BX $0.266667/ea vs
    $56.00/box; MSHB2 $0.008250/g vs $33.00/box; POTCOBX $0.002475/g vs
    $49.50/box). Half of every affected product's price history was invisible to
    the as-of lookup on whichever id a recipe happened to hold, and a chef-
    confirmed pack override keyed to one half did not reach the other.

    THE PACK PARSER MUST STILL SEE THE RAW CODE. resolve_pack reads that same
    trailing word to learn the sold unit ("KITOSPKG Kilogram" -> per kg), so the
    identity is normalised and the parse input is not.
    """
    c = (code or "").strip()
    prev = None
    while c != prev:
        prev, c = c, _CODE_UNIT_SUFFIX.sub("", c).strip()
    return c or (code or "").strip()


def purchasable_id(supplier: str, supplier_code: str) -> str:
    """
    The natural key of a thing you buy. Given by the invoice; never invented.

    This is what Back Office's SKU field was for ("Supplier item code. Enables
    future matching without name guesswork") and why its being 3.9% populated --
    0/144 for HG liquor -- is the whole problem.

    A unit word the PDF parse bled onto the end of the code is NOT part of the
    key -- see normalize_code. Two spellings of one supplier code are one
    purchasable, or the cost series splits in half.
    """
    code = (supplier_code or "").strip()
    if not code:
        raise ValueError(
            f"{supplier!r} line has no supplier_code. There is no natural key, so "
            f"there is no identity. Do NOT fall back to the description -- that is "
            f"how ALEHOUSE CRISP KEG becomes the wrong $27.50 keg."
        )
    return f"{_slug(supplier)}:{normalize_code(code).upper()}"


def canonical_purchasable(pid: str) -> str:
    """Re-key an ALREADY FORMED `supplier:CODE` id through normalize_code.

    Two callers need it and neither has the (supplier, code) pair to hand:

      * pack_overrides.yaml is keyed by purchasable_id and was written before the
        bleed was part of identity, so it carries both spellings
        ("fresh-fruit-team:TGL10BX BOX", "fresh-fruit-team:HCMB MARKET"). Those
        confirmations must keep landing on the product they were made for.
      * recipes SAVED BEFORE the split was fixed hold the bled id verbatim
        ("fresh-fruit-team:ONBRKG KILOGRAM" is in two live Stowaway recipes).
        CostSeries canonicalises on both sides so those keep costing instead of
        raising MissingCost.

    Anything without a colon, and any lightspeed:<ProductID>, is returned
    unchanged -- a numeric ProductID has no trailing unit word to strip.
    """
    s = (pid or "").strip()
    if ":" not in s:
        return s
    sup, code = s.split(":", 1)
    return f"{sup}:{normalize_code(code).upper()}"


def cogs_row_key(source_invoice: str, supplier_code: str,
                 invoice_description: str) -> tuple[str, str]:
    """Identity of ONE cost row in data/cogs_list.csv: one line per (invoice, product).

    modules/invoices/build_cogs_list.py has always used this shape to decide
    whether a validated invoice line is already present. It only ever guarded the
    WRITE, and a writer that appends without asking bypasses it. Three rows got
    in that way -- Paramount invoice 5441124, identical price, date, code and
    basis, differing only in the diagnostic `note`:

        10015926 CARPANO CLASSICO VERMOUTH  $23.1700  "...WET" / "...WET 4.74"
        44583    DE BORTOLI GOLD SEAL       $55.8950  "cask; WET" / "cask; WET 22.85"
        98541    SPRITE PET                 $ 4.2654  "LUC would be 10.9x high" /
                                                      "LUC per-CASE = 10.9x high"

    as_of is indifferent (same day, same price), but CostSeries.rolling counts the
    duplicated observation TWICE in the trailing-30-day mean -- the live
    menu-costing path -- so a duplicate silently reweights a real average.

    The definition lives here so READERS can apply it too. A check on the way in
    can be bypassed; a check on the way out cannot.
    """
    return ((source_invoice or "").strip(),
            ((supplier_code or "") or (invoice_description or "")).strip().upper())


def ingredient_id(name: str) -> str:
    """Canonical, ours, supplier-agnostic. What recipes reference."""
    return _slug(name)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


# -------------------------------------------------------------------- time ---

@dataclass(frozen=True)
class CostObservation:
    """
    One dated, evidenced price. A FACT. Append-only: never edited, never deleted.

    Every invoice line is one of these for free -- that is the gift the invoice
    pipeline hands the rest of the system, and why throwing invoice_date away
    (as data/ingredients.json currently does) is a mistake.

    A correction is a NEW observation, not an edit. History is not rewritten.
    """
    ingredient: str
    observed_on: date
    cost_per_unit: Decimal
    unit: str
    venue: Optional[str] = None
    source_invoice: str = ""
    purchasable: str = ""
    # How much was BOUGHT on this invoice line, in `unit`. Optional: the invoice
    # pipeline does not capture it yet, so it is None today and the rolling
    # average weights every observation equally (a plain mean). The day the
    # pipeline records quantities, this turns the same average volume-weighted
    # with no code change — a bulk buy will count more than a top-up, which is
    # what you actually paid. See CostSeries.rolling.
    qty: Optional[Decimal] = None

    def __post_init__(self):
        if not isinstance(self.cost_per_unit, Decimal):
            # float money is how you get 0.1 + 0.2 != 0.3 in a COGS subtraction
            raise TypeError(f"cost must be Decimal, got {type(self.cost_per_unit).__name__}")


class CostSeries:
    """
    As-of lookup over cost observations.

    Venue rule (ARCHITECTURE.md): observations carry a venue; lookup PREFERS the
    same venue and falls back to any. Stowaway and HG buy on separate accounts
    and can be quoted differently, but one venue's observation is far better
    evidence than none.

    Keys are canonicalised on BOTH sides (see canonical_purchasable), so a recipe
    saved before the bled-code split was fixed -- "fresh-fruit-team:ONBRKG
    KILOGRAM", live in two Stowaway recipes -- still finds the series that is now
    written as "fresh-fruit-team:ONBRKG". A rename must never snap a recipe.
    """

    def __init__(self, observations: Iterable[CostObservation],
                 purchasable_to_ingredient: Optional[dict[str, str]] = None):
        # Lookups translate through the ingredient map too. Observations were
        # re-keyed to their ingredient at load; a RECIPE may still reference the
        # purchasable ("foodlink:100464"). Both spellings must find the series —
        # the same both-sides rule canonical_purchasable already applies to
        # renames. Without this, populating the map broke Sugar Syrup: its
        # observations moved to the lightspeed anchor and the recipe's
        # supplier-id lookup found nothing (caught by the integration seam,
        # 2026-08-16).
        self._map = purchasable_to_ingredient if purchasable_to_ingredient is not None \
            else load_ingredient_map()
        self._by: dict[tuple[str, Optional[str]], list[CostObservation]] = {}
        for o in observations:
            k = canonical_purchasable(o.ingredient)
            k = self._map.get(k, k)      # store under the ingredient too
            self._by.setdefault((k, o.venue), []).append(o)
        for lst in self._by.values():
            lst.sort(key=lambda o: o.observed_on)

    def as_of(self, ingredient: str, on: date, venue: Optional[str] = None) -> CostObservation:
        """
        What it cost on `on`. The most recent observation on or before that day.

        Raises rather than guessing. An ingredient with no observation before the
        day being costed has no knowable cost -- inventing one (today's price,
        zero, an average) is how history starts lying. Fail toward review.
        """
        ingredient = canonical_purchasable(ingredient)
        ingredient = self._map.get(ingredient, ingredient)

        # 1. THE ASKED-FOR VENUE WINS if it has anything on or before the day.
        #    That is the documented preference and it stays: an observation from
        #    the account that actually bought it is the best evidence there is.
        if venue is not None:
            hit = self._latest((ingredient, venue), on)
            if hit:
                return hit

        # 2. OTHERWISE, THE MOST RECENT OBSERVATION ANYWHERE -- not the first
        #    bucket that happens to have one.
        #
        #    It used to walk the buckets in dict order and return the first hit,
        #    with the untagged bucket tried first. So an ingredient whose every
        #    real invoice is venue-tagged answered from whatever stale untagged
        #    seed existed, forever. Broccolini was being costed at a 2 January
        #    price of $4.61 while the 12 August invoice on the Harry Gatos
        #    account said $3.17. Fifteen ingredients across 63 dishes were doing
        #    this on 2026-08-16, and MOST WERE STALE LOW -- understating cost and
        #    overstating GP, the direction nobody investigates.
        #
        #    T6 (Zak, 15 Aug 2026) settles what the right answer is: "the whole
        #    group pays the same costs per ingredient." Venue on a cost row is
        #    PROVENANCE -- which account bought it -- never a cost dimension. So
        #    with no venue preference to honour, the newest price is the price.
        #
        #    Ties break on the venue label so the answer is deterministic; a
        #    non-deterministic cost would make costs.csv stop reproducing.
        best = None
        for (i, v), lst in self._by.items():
            if i != ingredient:
                continue
            hit = self._latest((i, v), on)
            if hit is None:
                continue
            if best is None or (hit.observed_on, str(v or "")) > (best.observed_on, str(best.venue or "")):
                best = hit
        if best is not None:
            return best

        raise LookupError(
            f"no cost observation for {ingredient!r} on or before {on}"
            + (f" (venue {venue})" if venue else "")
            + ". Cannot cost this day. Do not substitute a current price -- that "
              "rewrites history, which is the ACP bug this design exists to avoid."
        )

    def rolling(self, ingredient: str, on: date, window_days: int = 30,
                venue: Optional[str] = None) -> CostObservation:
        """
        The CURRENT working cost: a trailing `window_days` average as of `on`.

        Prices move (Zak, 2026-07-19: "prices move over time"). Pricing today's
        menu off a single latest invoice is noisy — one odd delivery sets your
        cost. So the live cost is the average of what you paid over the last
        month, weighted by how much you bought (volume-weighted) when the
        quantity is known, and a plain mean when it is not (which is the case
        today — see CostObservation.qty).

        This is the CURRENT view only. Historic reproducibility still runs
        through as_of: recomputing July's COGS must give July's answer forever,
        and an average that changes as new invoices land would rewrite it. So
        cost_on uses as_of for a past day and rolling for the live number.

        Degrades safely:
          * one observation in the window  -> that price
          * none in the window (but older exists) -> most recent (as_of)
          * mixed units in the window -> most recent, not a meaningless average
        """
        ingredient = canonical_purchasable(ingredient)
        ingredient = self._map.get(ingredient, ingredient)
        key = self._pick_key(ingredient, on, venue)
        if key is None:
            # Reuse as_of purely to raise the same, well-explained LookupError.
            return self.as_of(ingredient, on, venue=venue)

        lst = self._by[key]
        start = on - timedelta(days=window_days)
        window = [o for o in lst if start < o.observed_on <= on]
        if not window:
            return self.as_of(ingredient, on, venue=venue)   # fall back to latest

        units = {o.unit for o in window}
        if len(units) > 1:
            # Averaging g-prices with pack-prices is the $11,400/serve bug in a
            # different hat. Refuse the average; use the latest single fact.
            return self._latest(key, on)

        # Volume-weighted when every line knows its quantity; else equal weight.
        if all(o.qty is not None and o.qty > 0 for o in window):
            weight = {id(o): o.qty for o in window}
        else:
            weight = {id(o): Decimal("1") for o in window}
        wsum = sum(weight.values())
        avg = sum(o.cost_per_unit * weight[id(o)] for o in window) / wsum

        latest = max(window, key=lambda o: o.observed_on)
        return CostObservation(
            ingredient=ingredient,
            observed_on=latest.observed_on,
            cost_per_unit=avg,
            unit=window[0].unit,
            venue=key[1],
            source_invoice=f"avg of {len(window)} obs, {window_days}d",
        )

    def _pick_key(self, ingredient: str, on: date,
                  venue: Optional[str]) -> Optional[tuple[str, Optional[str]]]:
        """The venue bucket as_of would resolve to — same preference rule."""
        order: list[tuple[str, Optional[str]]] = []
        if venue is not None:
            order.append((ingredient, venue))
        for v in self._venues(ingredient):
            if (ingredient, v) not in order:
                order.append((ingredient, v))
        for key in order:
            if self._latest(key, on):
                return key
        return None

    def _venues(self, ingredient: str) -> list[Optional[str]]:
        return [v for (i, v) in self._by if i == ingredient]

    def _latest(self, key, on: date) -> Optional[CostObservation]:
        lst = self._by.get(key)
        if not lst:
            return None
        i = bisect_right([o.observed_on for o in lst], on)
        return lst[i - 1] if i else None

    def __len__(self) -> int:
        return sum(len(v) for v in self._by.values())


# ------------------------------------------------------------------- load ----

def load_ingredient_map(path: Path = ROOT / "data" / "ingredient_map.csv"
                        ) -> dict[str, str]:
    """
    purchasable_id -> canonical ingredient_id, ONLY where a human confirmed it.

    This is Decision 1's map (ARCHITECTURE.md): it lets "Select Fresh ONIBK" and
    "B&E onion" be declared the SAME ingredient, so switching supplier does not
    break a recipe or snap its cost history.

    Empty today, and correctly so: the current 55 observations have no
    cross-supplier duplicate, so there is nothing yet to merge. A purchasable
    with no row here maps to itself (see load_cost_observations). The file
    exists so that the day a second onion supplier appears, confirming they are
    one ingredient is a one-line edit — reviewed in a diff, attributed via
    confirmed_by — not a code change.
    """
    if not path.exists():
        return {}
    out = {}
    for r in csv.DictReader(path.open(encoding="utf-8-sig")):
        pid = (r.get("purchasable_id") or "").strip()
        ing = (r.get("ingredient_id") or "").strip()
        if pid and ing:
            out[pid] = ing
    return out


def load_cost_observations(path: Path = ROOT / "data" / "costs.csv",
                           purchasable_to_ingredient: Optional[dict[str, str]] = None
                           ) -> list[CostObservation]:
    """
    Read the cost fact table: data/costs.csv.

    Prices are IN THE UNIT A RECIPE USES (per g / ml / ea / bottle / keg),
    because that is the consumer. Built by
    modules/recipes/pipeline/build_costs.py, which converts pack prices and
    REFUSES rather than guessing when a pack can't be read.

    THIS USED TO READ data/cogs_list.csv DIRECTLY AND IT WAS WRONG. That file
    quotes per PACK ($57.00 for a 5kg box of squid, basis 'unit'). A recipe says
    "200 g". Multiplying gave $11,400 per serve -- arithmetically perfect,
    physically absurd, the same class of error the invoice validator exists to
    stop. A feed must publish the unit its consumer uses; no amount of care
    downstream fixes a pack price masquerading as a gram price.

    Until data/purchasable_map.csv exists, a purchasable maps to itself as its
    own ingredient. That is a placeholder, not the design: it means "switch
    supplier, break the recipe" is still true today. The map is Decision 1.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing — run: python3 modules/recipes/pipeline/build_costs.py"
        )
    # Default to the confirmed map on disk; caller may override for tests.
    mapping = purchasable_to_ingredient if purchasable_to_ingredient is not None \
        else load_ingredient_map()
    out = []
    for r in csv.DictReader(path.open(encoding="utf-8-sig")):
        pid = r["ingredient"]
        ing = mapping.get(pid, pid)     # unmapped purchasable = its own ingredient
        out.append(CostObservation(
            ingredient=ing,
            observed_on=date.fromisoformat(r["observed_on"]),
            cost_per_unit=Decimal(r["cost_per_unit"]),
            unit=r["unit"],
            venue=r.get("venue") or None,
            source_invoice=r.get("source_invoice", ""),
            purchasable=pid,
        ))
    return out
