// What each case here is: a thing that actually happened to the book, or the
// ordinary save that must keep working after we stopped it happening again.

import { assert, assertEquals } from "jsr:@std/assert@1";
import {
  blocksFor,
  checkRecipeSave,
  scalar,
  splitBlocks,
  stampEffectiveFrom,
} from "./recipe_guard.ts";

const CHIMI_ML = `# Chimichurri - entered by renan@stowawaybar.com on 2026-07-02
- product: "Chimichurri"
  yield_qty: 650
  yield_unit: "ml"
  ingredients:
    - id: "lightspeed:22524105"
      qty: 500
      unit: "ml"
`;

const BEEF = `# Jimmy Jury Beef [Batch] - entered by renan@stowawaybar.com on 2026-08-22
- product: "Jimmy Jury Beef [Batch]"
  yield_qty: 1210
  yield_unit: "g"
  ingredients:
    - subrecipe: "Chimichurri"
      qty: 200
      unit: "g"
`;

const FRIES = `# Rosemary Salted Fries - entered by zak@stowawaybar.com on 2026-08-01
- product: "Rosemary Salted Fries"
  ingredients:
    - id: "lightspeed:1"
      qty: 200
      unit: "g"
`;

const BOOK = CHIMI_ML + "\n" + BEEF;

Deno.test("a plain new recipe is accepted", () => {
  assert(checkRecipeSave(BOOK, FRIES, "Rosemary Salted Fries").ok);
});

Deno.test("a plated dish with no yield at all is accepted (31 of 56 records)", () => {
  const v = checkRecipeSave("", FRIES, "Rosemary Salted Fries");
  assertEquals(v.ok, true);
});

Deno.test("Romesco: an identical re-save of the same product is refused", () => {
  const v = checkRecipeSave(BOOK, CHIMI_ML, "Chimichurri");
  assertEquals(v.ok, false);
  assert(v.error!.includes("nothing would change"));
});

Deno.test("the dup check survives another product landing in between", () => {
  // appendCommit only dedupes the file TAIL. Here Chimichurri is not the tail,
  // and this is the gap that let Pizza Sauce pile up four deep.
  const later = BOOK + "\n" + FRIES;
  assertEquals(checkRecipeSave(later, CHIMI_ML, "Chimichurri").ok, false);
});

Deno.test("a genuine edit of the same product still appends", () => {
  const edited = CHIMI_ML.replace("yield_qty: 650", "yield_qty: 700");
  assert(checkRecipeSave(BOOK, edited, "Chimichurri").ok);
});

Deno.test("Chimichurri: ml -> g without unit_confirmed is refused", () => {
  const asGrams = CHIMI_ML.replace('yield_unit: "ml"', 'yield_unit: "g"');
  const v = checkRecipeSave(BOOK, asGrams, "Chimichurri");
  assertEquals(v.ok, false);
  assert(v.error!.includes("stop costing"));
});

Deno.test("ml -> g with unit_confirmed: true is a decision, and is allowed", () => {
  const asGrams = CHIMI_ML
    .replace('yield_unit: "ml"', 'yield_unit: "g"\n  unit_confirmed: true');
  assert(checkRecipeSave(BOOK, asGrams, "Chimichurri").ok);
});

Deno.test("Pizza Sauce: a yield that is not a positive number is refused", () => {
  for (const bad of ["0", "-5", "", "about 6kg"]) {
    const blk = FRIES.replace(
      "  ingredients:",
      `  yield_qty: ${bad}\n  yield_unit: "g"\n  ingredients:`,
    );
    assertEquals(checkRecipeSave("", blk, "Rosemary Salted Fries").ok, false, `accepted ${bad}`);
  }
});

Deno.test("half a yield is refused in both directions", () => {
  const noUnit = FRIES.replace("  ingredients:", "  yield_qty: 900\n  ingredients:");
  assertEquals(checkRecipeSave("", noUnit, "Rosemary Salted Fries").ok, false);
  const noQty = FRIES.replace("  ingredients:", '  yield_unit: "g"\n  ingredients:');
  assertEquals(checkRecipeSave("", noQty, "Rosemary Salted Fries").ok, false);
});

Deno.test("a unit the costing cannot read is refused", () => {
  const kg = FRIES.replace(
    "  ingredients:",
    '  yield_qty: 6\n  yield_unit: "kg"\n  ingredients:',
  );
  const v = checkRecipeSave("", kg, "Rosemary Salted Fries");
  assertEquals(v.ok, false);
  assert(v.error!.includes("g, ml, ea"));
});

Deno.test("a block saved under the wrong product name is refused", () => {
  assertEquals(checkRecipeSave(BOOK, CHIMI_ML, "Pizza Sauce").ok, false);
});

Deno.test("an empty body is refused", () => {
  assertEquals(checkRecipeSave(BOOK, "# just a comment\n", "Chimichurri").ok, false);
});

Deno.test("a yield carrying its reasoning as an inline comment still reads", () => {
  // Sugar Syrup, Lychee Lime Leaf, Passionfruit and Honey + Ginger all do this.
  const syrup = `- product: "Sugar Syrup"
  yield_qty: 1540            # (est) 1kg sugar dissolved 1:1 -> ~1.54L
  yield_unit: "ml"           # a syrup is poured, not weighed
`;
  assertEquals(scalar(syrup, "yield_qty"), "1540");
  assertEquals(scalar(syrup, "yield_unit"), "ml");
  assert(checkRecipeSave("", syrup, "Sugar Syrup").ok);
});

Deno.test("every save gets stamped with the day it was made", () => {
  const out = stampEffectiveFrom(FRIES, "2026-08-22");
  assertEquals(scalar(out, "effective_from"), "2026-08-22");
  // directly under the product line, at the block's own key indent
  const lines = out.split("\n");
  const i = lines.findIndex((l) => l.includes("- product:"));
  assertEquals(lines[i + 1], "  effective_from: 2026-08-22");
});

Deno.test("a stamp already on the block is left alone", () => {
  const once = stampEffectiveFrom(FRIES, "2026-08-01");
  assertEquals(stampEffectiveFrom(once, "2026-08-22"), once);
});

Deno.test("a block naming no product is not guessed at", () => {
  const orphan = "  yield_qty: 100\n  yield_unit: \"g\"\n";
  assertEquals(stampEffectiveFrom(orphan, "2026-08-22"), orphan);
});

Deno.test("Classic Margarita: a stamped correction supersedes the block it fixes", () => {
  // Zak saved this twice on 2026-08-19 correcting a lime garnish 1g -> 1.95g,
  // and recipe_as_of handed back the version he had just corrected because
  // neither block carried a date. Both now carry one, and the correction is
  // the later date rather than merely the later line.
  const first = stampEffectiveFrom(FRIES, "2026-08-19");
  const fixed = stampEffectiveFrom(
    FRIES.replace("qty: 200", "qty: 195"),
    "2026-08-22",
  );
  assert(checkRecipeSave(first, fixed, "Rosemary Salted Fries").ok);
  assertEquals(scalar(first, "effective_from"), "2026-08-19");
  assertEquals(scalar(fixed, "effective_from"), "2026-08-22");
});

Deno.test("a stamp that is not a date is refused", () => {
  for (const bad of ["yesterday", "22-08-2026", "2026-13-01"]) {
    const blk = FRIES.replace("  ingredients:", `  effective_from: ${bad}\n  ingredients:`);
    assertEquals(
      checkRecipeSave("", blk, "Rosemary Salted Fries").ok,
      false,
      `accepted ${bad}`,
    );
  }
});

Deno.test("splitBlocks and blocksFor read the real book shape", () => {
  assertEquals(splitBlocks(BOOK).length, 2);
  assertEquals(blocksFor(BOOK, "Chimichurri").length, 1);
  assertEquals(scalar(CHIMI_ML, "yield_unit"), "ml");
  assertEquals(scalar(FRIES, "yield_qty"), null);
});
