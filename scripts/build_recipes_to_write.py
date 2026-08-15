#!/usr/bin/env python3
"""
The no-recipe flags, turned into a worklist somebody can actually work.

    python3 scripts/build_recipes_to_write.py > RECIPES_TO_WRITE.md

41 of the cost-book flags are "sells well, no recipe" and they are the largest
single gap in the book — ~$39,500 of revenue it cannot cost. That is not an
engineering problem: it needs somebody who knows the menu. So this makes the ask
as small as possible.

TWO KINDS OF LINE, and the second is nearly free:

  DISHES need a recipe written. All this can do is rank them by 13-week revenue
  and name the closest costed dish, so a Roast Turkey can start from Pork Roast
  rather than a blank page.

  ADD-ONS AND SIDES are a single ingredient, and the book usually already prices
  that ingredient AND already knows what a plate of it looks like — "Add
  Pepperoni" against Large Little Italy's 76 g at $1.241. The only open question
  is the portion, which is a yes/no rather than a recipe.

It writes nothing into the book. Quoting a comparable portion is evidence for a
human, not a value to cost off — guessing a recipe is what
data/product_recipe_aliases.yaml records going wrong three times in one session.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / "data" / "lightspeed_recipes_costed.json"
FLAGS = ROOT / "data" / "cost_book_flags.json"
ADDON = re.compile(r"^(add|side|extra|swap|sub|1/2)\b", re.I)


def _toks(s: str):
    s = re.sub(r"\[[^\]]*\]", " ", (s or "").lower())
    s = ADDON.sub("", s.strip())
    return [w for w in re.findall(r"[a-z]+", s) if len(w) > 2]


def main() -> int:
    book = json.loads(BOOK.read_text(encoding="utf-8-sig"))["recipes"]
    costed = {n: r for n, r in book.items() if r.get("our_cost") is not None}
    flags = json.loads(FLAGS.read_text(encoding="utf-8-sig"))["flags"]

    usage: dict = {}
    for nm, r in costed.items():
        for i in r.get("ingredients") or []:
            usage.setdefault((i.get("name") or "").lower(), []).append(
                (nm, i.get("qty"), i.get("unit"), i.get("eff_cost")))

    def ingredient_for(name):
        t = set(_toks(name))
        if not t:
            return None
        best = None
        for ing, uses in usage.items():
            it = set(re.findall(r"[a-z]+", re.sub(r"\[[^\]]*\]", " ", ing)))
            sc = len(t & it)
            if sc and sc / max(1, len(t)) >= 0.6 and (not best or sc > best[0]):
                best = (sc, ing, uses)
        return best

    singles, groups = [], []
    for f in flags:
        if f.get("category") != "no_recipe":
            continue
        ev = f.get("evidence") or []
        blob = " ".join(str(x) for x in ev)
        if "uncosted lines" in f["subject"]:
            items = []
            for e in ev:
                m = re.match(r"^(.*?) — \$([\d,]+) in 13wk$", str(e))
                if m:
                    items.append((float(m.group(2).replace(",", "")), m.group(1).strip()))
            groups.append((f["subject"], items))
        else:
            m = re.search(r"\$([\d,]+) ex-GST", blob)
            g = re.search(r"reporting group (.+)$", blob)
            singles.append((float(m.group(1).replace(",", "")) if m else 0.0,
                            f["subject"], g.group(1) if g else ""))

    out = []
    out.append("## Dishes (write a recipe in Produce)\n")
    out.append("| 13wk $ | dish | group | nearest costed dish, for reference |")
    out.append("|---:|---|---|---|")
    for rev, name, grp in sorted(singles, key=lambda x: -x[0]):
        t = set(_toks(name))
        cands = [(len(t & set(_toks(c))), c) for c in costed if t & set(_toks(c))]
        near = ""
        if cands:
            sc, c = max(cands)
            if sc >= 1:
                near = f"{c} — ${costed[c]['our_cost']:.2f}"
        out.append(f"| {rev:,.0f} | **{name}** | {grp} | {near} |")

    out.append("\n## Add-ons and sides — the ingredient is already costed\n")
    out.append("Single-ingredient lines. The book already prices the ingredient and already")
    out.append("knows what a plate of it looks like, so the only open question is the PORTION.")
    out.append("Confirm the gram weight and the line is done.\n")
    out.append("| 13wk $ | add-on | the ingredient it adds | a parent dish's portion |")
    out.append("|---:|---|---|---|")
    seen = set()
    for _s, items in groups:
        for rev, name in sorted(items, key=lambda x: -x[0]):
            if not ADDON.match(name) or name in seen:
                continue
            seen.add(name)
            m = ingredient_for(name)
            if m:
                u = m[2][0]
                qty = f"{float(u[1]):g}" if u[1] not in (None, "") else "?"
                out.append(f"| {rev:,.0f} | **{name}** | {m[1]} | "
                           f"{u[0]} uses {qty}{u[2]} = ${u[3]:.3f} |")
            else:
                out.append(f"| {rev:,.0f} | **{name}** | — not in the book — | |")

    out.append("\n## Everything else in the grouped flags\n")
    for subj, items in groups:
        rest = [(r, n) for r, n in items if not ADDON.match(n)]
        if not rest:
            continue
        out.append(f"\n**{subj}**\n")
        for rev, name in sorted(rest, key=lambda x: -x[0]):
            out.append(f"- ${rev:,.0f} — {name}")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
