#!/usr/bin/env python3
"""Architecture guard for the sales dashboard — the guardrail that makes drift
IMPOSSIBLE, not just documented. Fails the build if business logic creeps back
into index.html or a module breaks the layering. Wire into CI + every deploy.

Enforced invariants:
  R1  index.html carries NO business logic — zero top-level function declarations
      in its inline script. All logic lives in /_shared/*.js modules.
  R2  index.html stays a shell — file size under the cap (logic creeping back
      shows up as growth).
  R3  the logic modules exist:      pnl.js util.js data.js render.js
  R4  every module + the inline script passes `node --check`.
  R5  pnl.js is PURE — no DOM / rendering tokens (the model never touches the page).
  R6  no logic function is defined twice across the modules.
  R7  the UI/behaviour markers exist in the bundle (day scrubber, leave toggle,
      Mari delivery KPI, global STATE, all module <script> tags).
  R8  every JS test suite passes — the P&L model, the render layer, the pure
      helpers, the recipe builder's line guard, and the five suites that hold
      the merged recipe module (tab routing, book view, page shell, flags
      panel, flag families).

Exit 0 = ok, 1 = architecture regression. This is what stops a future edit
(mine or anyone's) from bolting logic onto the HTML again.
"""
import re, subprocess, sys, tempfile, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SH = ROOT / "dashboard/_shared"
IDX = ROOT / "dashboard/sales/index.html"
MODULES = ["pnl.js", "util.js", "data.js", "render.js"]
SIZE_CAP = 100 * 1024

# ── R0 — the generated feeds the R8 suites read must be FRESH ───────────────
#
# The feeds below are gitignored: CI rebuilds them from scratch every run, so
# they are always current there. A long-lived working copy is the opposite —
# the feed sits on disk from whenever it was last built, and the R8 suites
# happily assert against a stale one.
#
# What that looks like is the problem. You do not get "your feed is old", you
# get "68 flags-family assertions, 3 failures" and a wall of produce lines, or
# "assert 'yield-lamb-roast' not in {...}" — a specific, plausible, completely
# fictional regression. It cost two separate debugging passes on 2026-08-15,
# both of which concluded "pre-existing failure on main" about code that was
# fine. Worse, it is exactly backwards: the guard is loudest when nothing is
# wrong, and silent about the one thing that is.
#
# So: check the feed against its own inputs FIRST, and if it is behind, say so
# and print the command. Cheap (a few stat calls), and it fails toward the
# truth instead of toward a ghost.
FEEDS = [
    ("data/ingredients.json",
     ["data/cogs_list.csv", "data/pack_overrides.yaml",
      "modules/recipes/pipeline/build_ingredients.py"],
     "python3 modules/recipes/pipeline/build_ingredients.py"),
    ("data/cost_book_flags.json",
     ["data/lightspeed_recipes_costed.json", "data/costs.csv", "data/cogs_list.csv",
      "scripts/build_cost_book_flags.py"],
     "python3 scripts/build_cost_book_flags.py"),
    ("data/recipes_index.json",
     ["data/lightspeed_recipes_costed.json", "data/costs.csv",
      "modules/recipes/pipeline/build_recipe_feeds.py"],
     "python3 modules/recipes/pipeline/build_recipe_feeds.py"),
    ("data/recipes_full.json",
     ["data/lightspeed_recipes_costed.json", "data/costs.csv",
      "modules/recipes/pipeline/build_recipe_feeds.py"],
     "python3 modules/recipes/pipeline/build_recipe_feeds.py"),
]

def stale_feeds():
    """(stale, absent) — feeds that exist but are behind their inputs, and feeds
    that are not there at all.

    ONLY `stale` is a failure. An ABSENT feed is normal and safe: on a clean
    checkout (which is every CI run) the feed does not exist yet at this point
    in the workflow, and every R8 suite that reads one exits 0 when it is
    missing — verified, not assumed. Failing on absence would red every CI run
    on the first step. The dangerous state is the one in between: a feed that
    is present, so the suites run, and old, so they assert against yesterday.
    """
    stale, absent = [], []
    for rel, inputs, cmd in FEEDS:
        feed = ROOT / rel
        if not feed.exists():
            absent.append(rel)
            continue
        f_mtime = feed.stat().st_mtime
        newer = [i for i in inputs
                 if (ROOT / i).exists() and (ROOT / i).stat().st_mtime > f_mtime]
        if newer:
            stale.append((rel, "older than " + ", ".join(newer), cmd))
    return stale, absent


FN_DECL = re.compile(r'(?m)^\s*function\s+([A-Za-z0-9_]+)\s*\(')
DOM_TOKENS = re.compile(r'document\.|getElementById|innerHTML|addEventListener|querySelector|\.style\b|location\.|\bwindow\.|\.classList|\.textContent')
problems = []

def strip_comments(src):
    # remove /* */ and // comments so prose like "cover the window." never trips
    # the DOM-token or function-declaration checks. Good enough for guard purposes.
    src = re.sub(r'/\*[\s\S]*?\*/', '', src)
    src = re.sub(r'(?m)//.*$', '', src)
    return src

html = IDX.read_text()
def inline_script(h):
    blocks = re.findall(r'<script(?![^>]*\bsrc=)(?![^>]*type="module")[^>]*>([\s\S]*?)</script>', h)
    return max(blocks, key=len) if blocks else ""
inline = strip_comments(inline_script(html))

# R1 — no function declarations in the HTML
idx_fns = FN_DECL.findall(inline)
if idx_fns:
    problems.append(f"R1: index.html inline script defines {len(idx_fns)} function(s) — logic must live in modules: {', '.join(idx_fns[:8])}")

# R2 — size cap
sz = IDX.stat().st_size
if sz > SIZE_CAP:
    problems.append(f"R2: index.html is {sz//1024}KB, over the {SIZE_CAP//1024}KB shell cap — logic likely creeping back")

# R3/R4/R5/R6 — modules
def node_check(text, name):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(text); p = f.name
    r = subprocess.run(["node", "--check", p], capture_output=True, text=True); os.unlink(p)
    if r.returncode:
        problems.append(f"R4: node --check failed for {name}: {r.stderr.strip()[:160]}")

defined = {}
for mod in MODULES:
    p = SH / mod
    if not p.exists():
        problems.append(f"R3: required module missing: {mod}"); continue
    txt = p.read_text()
    node_check(txt, mod)
    for fn in FN_DECL.findall(strip_comments(txt)):
        defined.setdefault(fn, []).append(mod)
node_check(inline, "index.html inline")

# R5 — pnl.js purity
pnl = SH / "pnl.js"
pnl_code = strip_comments(pnl.read_text()) if pnl.exists() else ""
if pnl.exists() and DOM_TOKENS.search(pnl_code):
    tok = DOM_TOKENS.search(pnl_code).group(0)
    problems.append(f"R5: pnl.js is impure — contains DOM token '{tok}'. The model must not touch the page.")

# R6 — no duplicate logic
for fn, where in defined.items():
    if len(where) > 1:
        problems.append(f"R6: function '{fn}' defined in multiple modules: {', '.join(where)}")

# R7 — behaviour markers somewhere in the bundle
bundle = html + "".join((SH / m).read_text() for m in MODULES if (SH / m).exists())
MARKERS = {"day scrubber": "tf-day", "leave toggle": "toggleLeave",
           "Mari delivery KPI": "Delivery cost", "global STATE": "var STATE =",
           "pnl.js tag": 'src="/_shared/pnl.js"', "util.js tag": 'src="/_shared/util.js"',
           "data.js tag": 'src="/_shared/data.js"', "render.js tag": 'src="/_shared/render.js"'}
for label, needle in MARKERS.items():
    if needle not in bundle:
        problems.append(f"R7: missing marker '{label}' ('{needle}')")

# R8 — the JS test suites: P&L conservation, render layer, pure-helper units,
#      and the recipe builder's line guard
SUITES = [("model conservation", "scripts/test_pnl_model.mjs"),
          ("render layer", "scripts/test_dashboard_render.mjs"),
          ("helper units", "scripts/test_dashboard_units.mjs"),
          # The recipe BUILDER's plausibility guard. Its rules are calibrated
          # against the real book INSIDE the suite, so a rule that starts
          # flagging recipes we already believe goes red here rather than
          # teaching chefs to click past a warning.
          ("recipe line guard", "scripts/test_recipe_line_guard.mjs"),
          # /recipes/ is now ONE module with four tabs (book, build, prep,
          # flags). Everything below is display, so nothing else in this repo
          # can see it fail: a bookmarked URL landing on the wrong tab, a row
          # that is mouse-only, a deleted summary card creeping back, a whole
          # family of open questions missing from the work queue, an element id
          # that one of four modules asks for and the merged shell no longer
          # has. All of them ship green and all of them are wrong on a screen.
          ("recipe tab routing", "scripts/test_recipe_tabs.mjs"),
          ("recipe book view", "scripts/test_recipe_book_view.mjs"),
          ("recipe page shell", "scripts/test_recipes_page_shell.mjs"),
          # Drives the REAL builder over the REAL feeds with a stub document and
          # reads what it drew. It exists for one number: the American Standard
          # Burger's lettuce line must load as 0.083 of a twin pack at $0.23,
          # not as 1 whole pack at $2.75. A load path that rounded a fractional
          # countable would make a correct recipe look wrong on a screen and
          # fail nothing else in this repo.
          ("recipe builder load", "scripts/test_recipe_builder_load.mjs"),
          # The flags panel: that it words a number honestly, and that every
          # family of question is actually on it. Zak has asked twice.
          ("cost book flags panel", "scripts/test_recipe_book_flags.mjs"),
          ("cost book flag families", "scripts/test_recipe_flags_families.mjs")]
# R0 runs BEFORE R8, and stops here if it trips. Letting the suites run against
# a stale feed is what produces the fictional regression this check exists to
# prevent — reporting both would just bury the real cause under the ghost.
_stale, _absent = stale_feeds()
if _stale:
    print("GENERATED FEEDS ARE STALE — not an architecture regression.\n")
    for rel, why, cmd in _stale:
        print(f"  ✗ {rel}  ({why})")
    print("\nThese are gitignored, so CI always builds them fresh and only a "
          "long-lived\nworking copy can drift. The R8 suites were NOT run: "
          "against a stale feed they\nreport confident, specific, wrong "
          "failures. Rebuild, then re-run this guard:\n")
    for cmd in dict.fromkeys(c for _, _, c in _stale):
        print(f"    {cmd}")
    sys.exit(1)
if _absent:
    # Not a failure — but say it out loud, because the summary line below would
    # otherwise claim all N suites "pass" when some of them quietly did nothing.
    print(f"note: {len(_absent)} generated feed(s) not built yet "
          f"({', '.join(_absent)});\n      the R8 suites that read them will "
          f"skip. Normal on a clean checkout.")

for label, rel in SUITES:
    t = ROOT / rel
    if not t.exists():
        problems.append(f"R8: test suite missing: {rel}"); continue
    r = subprocess.run(["node", str(t)], capture_output=True, text=True, cwd=ROOT)
    if r.returncode:
        tail = (r.stdout + r.stderr).strip().splitlines()[-5:]
        problems.append(f"R8: {label} test FAILED:\n    " + "\n    ".join(tail))

if problems:
    print("ARCHITECTURE GUARD FAILED — the dashboard drifted from its module structure:")
    for p in problems:
        print(f"  ✗ {p}")
    print("\nLogic belongs in dashboard/_shared/{pnl,util,data,render}.js — never in index.html.")
    sys.exit(1)
print(f"architecture guard: ok — index.html is a {sz//1024}KB shell, 0 logic fns; "
      f"{len(defined)} module fns, pnl.js pure, all {len(SUITES)} test suites pass.")
