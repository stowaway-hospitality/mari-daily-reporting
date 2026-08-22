// Save guards for the recipe builder.
//
// data/recipes/<venue>.yaml is an append-only log and the save endpoint only
// ever appends. That is the right shape for an audit trail, but it means a bad
// block is permanent, and a second block for the same product turns "which one
// is live" into a guess. Three things went wrong that way; this file is the
// answer to each.
//
//   dup    Romesco Sauce sat in stowaway.yaml byte-for-byte twice (2026-08-03)
//          and Pizza Sauce four times (2026-08-15). appendCommit already makes
//          a repeat of the FILE TAIL idempotent. This adds the case it cannot
//          see: another product's block landed in between, so the repeat is no
//          longer the tail, but it is still a save that changes nothing.
//
//   yield  A mid-edit save of Pizza Sauce landed with the yield moved (6020 g
//          expected, 5900 g found) and nothing rejected it. A declared yield
//          must be a real positive number carrying a real unit.
//
//   unit   Chimichurri's yield_unit moved ml -> g on 2026-08-22 and Jimmy Jury
//          Aioli, which referenced it in ml, silently stopped costing. Changing
//          the unit of a product that already exists now needs
//          unit_confirmed: true, so it is a decision someone made rather than a
//          typo nobody saw.
//
// Only blocks that DECLARE a yield are checked on yield and unit: 31 of the 56
// records today are plated dishes and single-serve drinks that legitimately
// have none, and rejecting those would stop the kitchen working.

export const YIELD_UNITS = ["g", "ml", "ea"] as const;
const MAX_YIELD = 1_000_000;

export interface GuardVerdict {
  ok: boolean;
  /** Operator-facing reason. Present only when ok is false. */
  error?: string;
}

/** Drop comment lines; the block header carries a timestamp and must not count. */
export function stripComments(s: string): string {
  return s
    .split("\n")
    .filter((l) => !l.trimStart().startsWith("#"))
    .join("\n");
}

/** Normalise for comparison: no comments, no blank lines, no trailing space. */
export function bodyOf(s: string): string {
  return stripComments(s)
    .split("\n")
    .map((l) => l.trimEnd())
    .filter((l) => l.trim() !== "")
    .join("\n")
    .trim();
}

function unquote(v: string): string {
  const t = v.trim();
  const q = t.match(/^"((?:[^"\\]|\\.)*)"/) || t.match(/^'([^']*)'/);
  if (q) return q[1];
  // A bare scalar ends where an inline comment starts, which in YAML is a #
  // following whitespace. Four live syrup recipes carry their yield reasoning
  // exactly there -- `yield_qty: 1540            # (est) 1kg sugar dissolved
  // 1:1 -> ~1.54L` -- and reading the comment as part of the number would have
  // refused a save that has been fine for months. Found by replaying all 56
  // committed blocks through this guard before shipping it.
  return t.split(/\s+#/)[0].trim();
}

/** Read a top-level scalar out of one recipe block. Returns null if absent. */
export function scalar(block: string, key: string): string | null {
  for (const line of stripComments(block).split("\n")) {
    const m = line.match(new RegExp(`^\\s*-?\\s*${key}:\\s*(.*)$`));
    if (m) return unquote(m[1]);
  }
  return null;
}

/** Split a book into one string per `- product:` block, in file order. */
export function splitBlocks(book: string): string[] {
  const lines = stripComments(book).split("\n");
  const out: string[] = [];
  let cur: string[] | null = null;
  for (const line of lines) {
    if (/^\s*-\s*product:/.test(line)) {
      if (cur) out.push(cur.join("\n"));
      cur = [line];
    } else if (cur) {
      cur.push(line);
    }
  }
  if (cur) out.push(cur.join("\n"));
  return out;
}

/** Every block for one product, oldest first. Append-only makes order chronological. */
export function blocksFor(book: string, product: string): string[] {
  return splitBlocks(book).filter((b) => scalar(b, "product") === product);
}

/**
 * Decide whether one recipe-builder save may be appended.
 *
 * `current` is the book as it stands, `incoming` the single block about to be
 * appended, `product` the product name the endpoint was called with.
 */
export function checkRecipeSave(
  current: string,
  incoming: string,
  product: string,
): GuardVerdict {
  const body = bodyOf(incoming);
  if (!body) return { ok: false, error: "recipe body is empty" };

  const named = scalar(incoming, "product");
  if (named !== null && named !== product) {
    return {
      ok: false,
      error:
        `the block says product "${named}" but the save was for "${product}" — ` +
        `one of the two is wrong, and an append-only log cannot take it back`,
    };
  }

  // yield
  const qtyRaw = scalar(incoming, "yield_qty");
  const unitRaw = scalar(incoming, "yield_unit");

  if (qtyRaw !== null) {
    const qty = Number(qtyRaw);
    if (!Number.isFinite(qty) || qty <= 0) {
      return { ok: false, error: `yield_qty must be a positive number, got "${qtyRaw}"` };
    }
    if (qty > MAX_YIELD) {
      return { ok: false, error: `yield_qty ${qty} is past anything a kitchen makes in one batch` };
    }
    if (unitRaw === null) {
      return { ok: false, error: "yield_qty was given without a yield_unit — a number with no unit cannot be costed" };
    }
  }

  if (unitRaw !== null) {
    if (qtyRaw === null) {
      return { ok: false, error: "yield_unit was given without a yield_qty — a unit with no number cannot be costed" };
    }
    if (!(YIELD_UNITS as readonly string[]).includes(unitRaw)) {
      return {
        ok: false,
        error: `yield_unit must be one of ${YIELD_UNITS.join(", ")}, got "${unitRaw}"`,
      };
    }
  }

  const previous = blocksFor(current, product);
  const last = previous.length ? previous[previous.length - 1] : null;

  // dup
  if (last && bodyOf(last) === body) {
    return {
      ok: false,
      error:
        `this is identical to the last saved "${product}" — nothing would change, ` +
        `and a second copy makes which block is live a guess`,
    };
  }

  // unit
  if (last && unitRaw !== null) {
    const was = scalar(last, "yield_unit");
    const confirmed = (scalar(incoming, "unit_confirmed") || "").toLowerCase() === "true";
    if (was !== null && was !== unitRaw && !confirmed) {
      return {
        ok: false,
        error:
          `"${product}" is saved in ${was} and this save is in ${unitRaw}. ` +
          `Any recipe using it as a subrecipe still reads ${was} and would stop ` +
          `costing. Set unit_confirmed: true if the change is deliberate.`,
      };
    }
  }

  return { ok: true };
}
