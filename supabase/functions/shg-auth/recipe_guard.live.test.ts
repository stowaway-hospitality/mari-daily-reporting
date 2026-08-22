// The guard read against the REAL book, not fixtures.
//
// A save guard has two ways to be wrong and only one of them is loud. Refusing
// a bad save is the job; refusing a GOOD save stops the kitchen and looks like
// the site is broken. This replays every block already committed to
// data/recipes/*.yaml against the book as it stood just before that block
// landed, and fails if the guard would have turned any of them away.
//
// It earned its place immediately: the first run refused four live syrups whose
// yield carries its reasoning as an inline YAML comment. That bug shipped
// nowhere because this ran first.

import { assertEquals } from "jsr:@std/assert@1";
import { checkRecipeSave, scalar } from "./recipe_guard.ts";

const BOOKS = ["harry_gatos", "marilynas", "stowaway"];
const ROOT = new URL("../../../data/recipes/", import.meta.url);

Deno.test("every block already in the book would still be accepted", async () => {
  const refusals: string[] = [];
  let checked = 0;

  for (const book of BOOKS) {
    const text = await Deno.readTextFile(new URL(`${book}.yaml`, ROOT));
    const lines = text.split("\n");
    const starts: number[] = [];
    lines.forEach((l, i) => {
      if (/^\s*-\s*product:/.test(l)) starts.push(i);
    });

    for (let k = 0; k < starts.length; k++) {
      const from = starts[k];
      const to = k + 1 < starts.length ? starts[k + 1] : lines.length;
      const block = lines.slice(from, to).join("\n");
      const before = lines.slice(0, from).join("\n");
      const product = scalar(block, "product") || "";
      const v = checkRecipeSave(before, block, product);
      checked++;
      if (!v.ok) refusals.push(`${book}: ${product} — ${v.error}`);
    }
  }

  if (checked === 0) throw new Error("read no recipe blocks — the books moved");
  assertEquals(refusals, [], `the guard would refuse live recipes:\n${refusals.join("\n")}`);
});
