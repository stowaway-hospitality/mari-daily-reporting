#!/usr/bin/env python3
"""
Assemble _site/ — the thing GitHub Pages actually serves.

    python3 scripts/build_site.py            # build
    python3 scripts/build_site.py --serve    # build + preview on :8000

WHY THIS EXISTS
---------------
The repo layout and the deployed layout are DIFFERENT, and until now nothing
let you see the deployed one locally. That cost a real bug: recipes.html asked
for '../data/ingredients.json', which works when you serve the repo root and
404s in production, because on Pages the page sits at _site/ and data at
_site/data/. It was never going to show up until a chef opened it.

So the layout is defined ONCE, here, and used by both the workflow and local
preview. If it works locally it works deployed, because it is the same code.

URLS ARE A CONTRACT
-------------------
/ must keep serving the main dashboard. People have it bookmarked.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "_site"

# The site map. One line per thing served. This IS the deployment.
#   (source, destination in _site)
LAYOUT: list[tuple[str, str]] = [
    ("dashboard/root",         ""),          # CNAME, favicon, logos -> /
    ("dashboard/_shared",      "_shared"),   # shared JS/CSS -> /_shared/
    ("dashboard/home",         ""),          # the app HOME (sign in here) -> /
    ("dashboard/sales",        "sales"),     # the daily-reporting dashboard -> /sales/
    ("dashboard/admin",        "admin"),     # -> /admin/ (admin only)
    ("dashboard/invoices",     "invoices"),  # -> /invoices/ (admin only) — Xero review queue
    ("dashboard/pricing",      "pricing"),   # -> /pricing/ (admin only) — cross-supplier $/unit
    ("dashboard/recipes-book", "recipes-book"),  # -> /recipes-book/ — full costed recipe book + GP
    ("dashboard/bookings",     "bookings"),  # -> /bookings/ (admin only)
    ("modules/recipes/app",    "recipes"),   # -> /recipes/
    ("data",                   "data"),      # feeds -> /data/
    ("baselines",              "baselines"),
]


def build() -> int:
    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir(parents=True)

    # Rolling feeds are GENERATED at build time, never committed.
    #
    # data/ingredients.json is a 90-day window off date.today(). A committed
    # copy goes stale by the passage of time alone -- no code change, no commit,
    # just a Tuesday. Generating it here means the chef always sees a current
    # window and there is no stale-file class of bug at all.
    #
    # data/costs.csv is different: deterministic from cogs_list.csv, so it IS
    # committed and CI proves it reproduces. Two kinds of derived file; only one
    # of them can be checked byte-for-byte.
    for gen in ("modules/recipes/pipeline/build_ingredients.py",
                "scripts/convert_lightspeed_recipes.py",     # costed feed BEFORE recipe_feeds
                "modules/recipes/pipeline/build_recipe_feeds.py",  # reads it for sub-recipes
                # ...and the /recipes-book/ flags panel, which reads the costed
                # feed plus a 13-week and a 52-week sales window. Two moving
                # windows means a committed copy would go stale on a Tuesday with
                # no commit behind it — the ingredients.json trap — so it is
                # generated here and not committed.
                "scripts/build_cost_book_flags.py",
                "modules/invoices/build_price_compare.py"):  # /pricing/compare.json from cogs
        r = subprocess.run([sys.executable, str(ROOT / gen)], capture_output=True, text=True, cwd=ROOT)
        if r.returncode:
            print(f"  FAILED {gen}\n{r.stderr}")
            return 1
        print(f"  generated via {gen.split('/')[-1]}")

    for src_rel, dst_rel in LAYOUT:
        src = ROOT / src_rel
        if not src.exists():
            print(f"  skip {src_rel} (not present)")
            continue
        dst = SITE / dst_rel if dst_rel else SITE
        dst.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            if item.name.startswith("."):
                continue
            target = dst / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
        print(f"  {src_rel:<24} -> /{dst_rel}")

    inject_app_icons()
    stamp_versions()
    return check()


_REF = re.compile(
    r"""(?:href|src)=["']([^"'#?]+)["']"""      # <link> <script> <img> <a>
    # ES module imports. ABSOLUTE ones ('/_shared/x.js') count too: the recipe
    # module is four tabs' worth of modules importing each other that way, and
    # a typo in one of them is a blank page with a console error nobody sees.
    # Only relative forms were checked before, which is precisely the gap that
    # let '../data/ingredients.json' ship in the first place.
    r"""|from\s+["'](\./[^"']+|\.\./[^"']+|/[^"']+)["']"""
    r"""|Feed\.load\(\s*["']([^"'?]+)["']"""    # our shared feed loader
    r"""|fetch\(\s*["']([^"'?]+)["']"""         # hand-rolled fetches
)


def stamp_versions() -> None:
    """Append a content-hash ?v= to every LOCAL .js/.css reference in the built
    HTML. Without this the browser and Cloudflare cache /_shared/render.js etc.
    forever and shipped changes never reach anyone until the edge cache happens
    to expire. The hash is per-file content, so only files that actually changed
    re-download. External (https://) refs are left alone. The link-checker below
    already ignores query strings, so stamped refs still validate."""
    cache: dict = {}
    def digest(p: Path) -> str:
        if p not in cache:
            cache[p] = hashlib.md5(p.read_bytes()).hexdigest()[:8]
        return cache[p]
    # group 1 = src=/href=" ; group 2 = the ref ; group 3 = closing quote
    pat = re.compile(r"""((?:src|href)=["'])(/[^"'?#]+\.(?:js|css)|[^"'?#:]+\.(?:js|css))(["'])""")
    stamped = 0
    for html in SITE.rglob("*.html"):
        txt = html.read_text()
        def sub(m):
            nonlocal stamped
            ref = m.group(2)
            fp = (SITE / ref.lstrip("/")) if ref.startswith("/") else (html.parent / ref).resolve()
            if fp.is_file():
                stamped += 1
                return f"{m.group(1)}{ref}?v={digest(fp)}{m.group(3)}"
            return m.group(0)
        new = pat.sub(sub, txt)
        if new != txt:
            html.write_text(new)
    print(f"  cache-bust: stamped {stamped} local asset refs")



_APPICON_BLOCK = '<!--shg-appicons-->\n<link rel="icon" href="/favicon.ico" sizes="any">\n<link rel="icon" type="image/png" sizes="32x32" href="/logo_32.png">\n<link rel="icon" type="image/png" sizes="128x128" href="/logo_128.png">\n<link rel="apple-touch-icon" sizes="180x180" href="/appicon-180.png">\n<link rel="manifest" href="/manifest.webmanifest">\n<meta name="apple-mobile-web-app-capable" content="yes">\n<meta name="mobile-web-app-capable" content="yes">\n<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">\n<meta name="apple-mobile-web-app-title" content="Stowaway">\n<meta name="theme-color" content="#161512">\n'


def inject_app_icons() -> None:
    """Give every built page the home-screen icon and web manifest so Add to
    Home Screen from any page installs a clean Stowaway app with the logo, not
    a screenshot. Runs on the built copy, idempotent via a marker; every ref it
    adds ships from dashboard/root so the link-checker still resolves."""
    n = 0
    for html in SITE.rglob("*.html"):
        txt = html.read_text()
        if "shg-appicons" in txt or "</head>" not in txt:
            continue
        html.write_text(txt.replace("</head>", _APPICON_BLOCK + "</head>", 1))
        n += 1
    print(f"  app icons: injected into {n} pages")

def check() -> int:
    """
    Resolve EVERY local reference in every page and prove it exists in the build.

    The first version of this check was hand-written per-case and missed four
    real breakages in the first run: a page moved to /recipes/ and its
    './_shared/auth.js' silently became '/recipes/_shared/auth.js'. Specific
    checks only catch the bug you already thought of. So: don't guess — resolve
    the actual references.

    This is the whole reason the file exists. A broken path never fails a test,
    never fails a deploy, and shows up as a chef saying "the page is blank".
    """
    problems: list[str] = []
    site_root = SITE.resolve()

    if not (SITE / "index.html").exists():
        problems.append("no index.html at the site root — / would 404")

    # Every page AND every module they pull in. A merged module is mostly
    # module-to-module imports, so checking only .html would have validated the
    # one import in index.html and none of the twenty behind it.
    scanned = sorted(SITE.rglob("*.html")) + sorted(SITE.rglob("*.js"))
    for page in scanned:
        text = page.read_text(errors="ignore")
        rel = page.relative_to(SITE)
        for m in _REF.finditer(text):
            ref = next(g for g in m.groups() if g)
            if ref.startswith(("http://", "https://", "//", "data:", "mailto:", "#")):
                continue
            # A JS template expression (href="${t.href}") is resolved at runtime,
            # not a static path — nothing on disk to check, so skip it.
            if "${" in ref:
                continue
            # Inside a .js module, a RELATIVE href is a string this module writes
            # into some other page's HTML — render.js emits <a href="rg.html">
            # into /sales/ — so it resolves against that page, not against the
            # module. Only absolute refs and module imports can be checked here.
            if page.suffix == ".js" and not ref.startswith("/"):
                continue
            # '/x' is site-root-relative and valid — we serve at a domain root
            # (dashboard/CNAME -> app.stowawaybar.com), not a /repo/ subpath.
            target = (SITE / ref.lstrip("/")).resolve() if ref.startswith("/") \
                     else (page.parent / ref).resolve()
            if not str(target).startswith(str(site_root)):
                problems.append(f"{rel}: {ref!r} escapes the site root")
            elif not target.exists():
                problems.append(f"{rel}: {ref!r} -> {target.relative_to(site_root)} does not exist")

    if problems:
        print("\nBUILD PROBLEMS — these 404 in production:")
        for p in problems:
            print(f"  ✗ {p}")
        return 1

    n = sum(1 for _ in SITE.rglob("*") if _.is_file())
    print(f"\n  ok — {n} files, every reference resolves")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true", help="preview on :8000 after building")
    args = ap.parse_args()

    rc = build()
    if rc:
        return rc
    if args.serve:
        import functools, http.server, socketserver
        h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
        print("\n  http://localhost:8000  (this is exactly what Pages serves)")
        with socketserver.TCPServer(("", 8000), h) as s:
            try:
                s.serve_forever()
            except KeyboardInterrupt:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
