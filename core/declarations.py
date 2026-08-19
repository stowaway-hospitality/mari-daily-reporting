"""
The declaration registry. One place that knows every ruling and who must read it.

WHY THIS EXISTS
---------------
This repo holds its hard-won rulings as DECLARATIONS -- a pack size, a unit
relabel, a weighed yield, a portion size -- each written down once with the
arithmetic that established it. `scripts/check_declarations_bind.py` already asks
the first question about them:

    does this declaration still match a record that exists?

It does not ask the second one, and the second one is the one that keeps costing
days:

    does everyone who SHOULD read it, read it?

Four times in the session of 2026-08-19 a correction was found that was already
written down, with a worked proof, and that one reader was not consulting:

    Tandoori line relabel        staged materialiser read it; the LIVE converter did not
    ILG account codes            nobody read it; 173 invoices went to the wrong venue
    Back Office DefaultSize      nobody read it; container_sizes.csv did not either
    Garlic Oil / Mint Yoghurt    staged book read it; converter AND feed builder did not

Each was wired up on its own and the next session found the next one. Zak, that
day: "i feel like we're going around in circles." He was right, and the reason is
structural: ~20 declaration files, ~6 readers, and NOTHING that says which pairs
are supposed to be connected. A disconnected pair is invisible until somebody
trips over a number.

WHAT THIS FILE IS
-----------------
The missing statement of intent. For every declaration: where it lives, what it
rules on, and -- the load-bearing part -- WHICH MODULES MUST CONSULT IT. That
last field is a claim about the system, not a description of it, so it can be
checked. `scripts/check_declaration_readers.py` checks it on every commit.

A declaration whose `readers` list is wrong is a bug report waiting to be
written. A declaration with an empty `readers` list is one already.

HOW TO ADD A DECLARATION
------------------------
Add an entry here FIRST, naming every module that must honour it, then write the
readers. The guard will tell you which ones you have not written yet. Do not add
the file and wire one reader -- that is the failure this file exists to end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Declaration:
    """One hand-maintained ruling, and the readers contractually bound to it."""

    name: str
    path: Path
    rules_on: str
    readers: tuple[str, ...] = ()
    #: Readers that demonstrably do not consult it yet, each with the reason.
    #: A gap here is a FINDING, pinned so CI stays honest without going red on
    #: a defect that predates the guard. Emptying this list is the work.
    known_gaps: dict[str, str] = field(default_factory=dict)

    @property
    def const(self) -> str:
        """The module-level name this declaration is exported under.

        The guard looks for it: once a declaration is behind a loader the
        FILENAME stops appearing outside this module, so "does anyone name the
        file" would score correct wiring as a gap.
        """
        return self.name.upper()

    def load(self) -> Any:
        """The parsed declaration, or an empty document when absent.

        ONE PARSE, so `encoding="utf-8-sig"` is decided once. A BOM on a file a
        chef opened in Excel used to be a per-reader problem.
        """
        if not self.path.exists():
            return {}
        return yaml.safe_load(self.path.read_text(encoding="utf-8-sig")) or {}


def _d(rel: str) -> Path:
    return ROOT / rel


# ---------------------------------------------------------------------------
# THE REGISTRY
#
# `readers` is dotted module path as it appears in an import, or the script path
# for scripts/*.py. The guard resolves both.
# ---------------------------------------------------------------------------

REGISTRY: dict[str, Declaration] = {}


def _reg(d: Declaration) -> Declaration:
    REGISTRY[d.name] = d
    return d


BATCH_YIELD_UNITS = _reg(Declaration(
    name="batch_yield_units",
    path=_d("data/batch_yield_units.yaml"),
    rules_on="a batch yield or recipe line whose UNIT nobody measured",
    readers=(
        "modules.recipes.units",                    # the shared relabel loader
        "scripts/convert_lightspeed_recipes.py",    # LIVE cost book
        "scripts/materialise_recipes.py",           # staged book
        "modules/recipes/pipeline/build_recipe_feeds.py",
    ),
))

MEASURED_YIELDS = _reg(Declaration(
    name="measured_yields",
    path=_d("data/measured_yields.yaml"),
    rules_on="a batch yield somebody put on a scale — outranks every inference",
    readers=(
        "modules/recipes/pipeline/build_recipe_feeds.py",   # builder feed
        "scripts/convert_lightspeed_recipes.py",            # LIVE cost book
        "scripts/yield_worklist.py",
    ),
))

PREP_YIELDS = _reg(Declaration(
    name="prep_yields",
    path=_d("data/prep_yields.yaml"),
    rules_on="a batch yield with a written basis, or a scrape of Produce",
    readers=(
        "modules/recipes/pipeline/build_recipe_feeds.py",
        "modules/recipes/cost.py",
        "modules/recipes/book_reconcile.py",
        "scripts/convert_lightspeed_recipes.py",
        "scripts/materialise_recipes.py",
        "scripts/audit_book.py",
    ),
))

RECIPE_YIELDS = _reg(Declaration(
    name="recipe_yields",
    path=_d("data/recipe_yields.yaml"),
    rules_on="Produce's own 'Expected yield' field, harvested 2026-08-09",
    readers=(
        "modules/recipes/book_reconcile.py",
        "scripts/convert_lightspeed_recipes.py",
    ),
    known_gaps={
        "scripts/convert_lightspeed_recipes.py":
            "27 of 34 entries disagree with prep_yields.yaml and 12 disagree by "
            "MAGNITUDE, not spelling — Pizza Sauce 6,028 g here against 9,338 g "
            "there (1.55x, and Pizza Sauce carries $1.06M across 146 dishes), "
            "Demerara Syrup 560 ml against 1,160 ml (2.07x), Cooked Beef Brisket "
            "10.5 kg against 6,000 g. Zak has named Lightspeed the source of "
            "truth for yields, which would make this file outrank prep_yields "
            "for the seven that carry no measured basis — but three prep_yields "
            "entries deliberately override Produce with a documented cook loss "
            "(Achiote Chicken, and the brisket that cost $8.53 on every "
            "Meatlovers). Wiring this reader blind would silently reverse those. "
            "Needs a per-batch ruling from Zak, not a bulk import.",
    },
))

SERVE_PORTIONS = _reg(Declaration(
    name="serve_portions",
    path=_d("data/serve_portions.yaml"),
    rules_on="how much of a batch-shaped recipe is actually one serve",
    readers=(
        "scripts/audit_book.py",
        "scripts/convert_lightspeed_recipes.py",
    ),
    known_gaps={
        "scripts/convert_lightspeed_recipes.py":
            "THE AUDIT DIVIDES; THE BOOK DOES NOT. audit_book computes the serve "
            "cost from the declared portion, prints it, and downgrades its own "
            "SEVERE finding to INFO — while `our_cost` keeps the whole tray. "
            "Potato Salad stands at $9.87 against a $7.00 menu price (-55.1% GP) "
            "with '130 g of 1,780 g = $0.72 a serve' declared and evidenced since "
            "2026-08-17. This is the Tandoori pattern with a sting: the "
            "declaration RETIRES THE ALARM without moving the number, so the one "
            "signal that would have found it is the thing it switched off.",
    },
))

COOK_YIELDS = _reg(Declaration(
    name="cook_yields",
    path=_d("data/cook_yields.yaml"),
    rules_on="weight lost between raw in and cooked out",
    readers=(
        "scripts/convert_lightspeed_recipes.py",
        "scripts/materialise_recipes.py",
    ),
))

PIZZA_PORTIONS = _reg(Declaration(
    name="pizza_portions",
    path=_d("data/pizza_portions.yaml"),
    rules_on="grams of each topping on Regular / Large / Family, as weighed",
    readers=(
        "scripts/convert_lightspeed_recipes.py",
        "scripts/audit_book.py",
        "scripts/materialise_recipes.py",
    ),
    known_gaps={
        "scripts/materialise_recipes.py":
            "Costs are inherited correctly — the materialiser reads the "
            "converter's OUTPUT, so the weighings are in the number. What it "
            "misses is PROVENANCE: 805 weighed lines land in the staged book "
            "tagged `scrape` ('nobody has checked it') instead of `weighed`. "
            "The staged book's whole point is that 'lines nobody has checked' is "
            "a number that can only shrink, and this holds it 805 too high.",
    },
))

PIZZA_REGULAR_GRAMS = _reg(Declaration(
    name="pizza_regular_grams",
    path=_d("data/pizza_regular_grams.yaml"),
    rules_on="the weighed Regular base a scaled variant derives from",
    readers=(
        "scripts/audit_book.py",
        "scripts/materialise_recipes.py",
    ),
))

RECIPE_LINE_UNIT_FIXES = _reg(Declaration(
    name="recipe_line_unit_fixes",
    path=_d("data/recipe_line_unit_fixes.yaml"),
    rules_on="a recipe line whose unit was typed wrong",
    readers=(
        "scripts/convert_lightspeed_recipes.py",
        "scripts/materialise_recipes.py",
    ),
))

RECIPE_MISSING_LINES = _reg(Declaration(
    name="recipe_missing_lines",
    path=_d("data/recipe_missing_lines.yaml"),
    rules_on="an ingredient the recipe obviously uses and does not list",
    readers=(
        "scripts/convert_lightspeed_recipes.py",
        "scripts/materialise_recipes.py",
    ),
))

RECIPE_INGREDIENT_SWAPS = _reg(Declaration(
    name="recipe_ingredient_swaps",
    path=_d("data/recipe_ingredient_swaps.yaml"),
    rules_on="an ingredient the recipe names that the kitchen no longer uses",
    readers=(
        "scripts/convert_lightspeed_recipes.py",
        "scripts/materialise_recipes.py",
    ),
))

PRODUCT_RECIPE_ALIASES = _reg(Declaration(
    name="product_recipe_aliases",
    path=_d("data/product_recipe_aliases.yaml"),
    rules_on="a sold product and the differently-named recipe that costs it",
    readers=(
        "scripts/convert_lightspeed_recipes.py",
    ),
))

PACK_OVERRIDES = _reg(Declaration(
    name="pack_overrides",
    path=_d("data/pack_overrides.yaml"),
    rules_on="a pack size the invoice did not state and a chef confirmed",
    readers=(
        "core/pack_overrides.py",                   # the shared loader
        "modules/recipes/pipeline/build_costs.py",
        "modules/recipes/pipeline/build_ingredients.py",
        "modules/recipes/pipeline/build_recipe_feeds.py",
    ),
))

DECLARED_CONVERSIONS = _reg(Declaration(
    name="declared_conversions",
    path=_d("data/declared_conversions.yaml"),
    rules_on="a pack unit restated in base units, with cited evidence",
    readers=(
        "core/conversions.py",                      # the shared loader
        "modules/recipes/pipeline/build_costs.py",
    ),
))

DECLARED_PURCHASES = _reg(Declaration(
    name="declared_purchases",
    path=_d("data/declared_purchases.yaml"),
    rules_on="a hand-entered invoice line for a supplier the mailbox never delivers",
    readers=(
        "modules/invoices/build_cogs_list.py",
    ),
))

ADJUDICATED_PRICES = _reg(Declaration(
    name="adjudicated_prices",
    path=_d("data/adjudicated_prices.yaml"),
    rules_on="a price question that has been answered, so the queue stops asking",
    readers=(
        "scripts/build_cost_book_flags.py",
    ),
))

COST_BOOK_FLAGS = _reg(Declaration(
    name="cost_book_flags",
    path=_d("data/cost_book_flags.yaml"),
    rules_on="questions only a human can settle, and permanent exemptions",
    readers=(
        "scripts/build_cost_book_flags.py",
        "scripts/audit_book.py",
    ),
))

RECIPE_VENUE_MIRRORS = _reg(Declaration(
    name="recipe_venue_mirrors",
    path=_d("data/recipe_venue_mirrors.yaml"),
    rules_on="a recipe deliberately identical across two venues",
    readers=(
        "scripts/merge_venue_scrape.py",
    ),
))

PRODUCT_RENAMES = _reg(Declaration(
    name="product_renames",
    path=_d("data/product_renames.yaml"),
    rules_on="a product that changed name and must keep its history",
    readers=(
        "scripts/product_identity.py",
        "scripts/detect_product_renames.py",
    ),
))


def all_declarations() -> list[Declaration]:
    return [REGISTRY[k] for k in sorted(REGISTRY)]
