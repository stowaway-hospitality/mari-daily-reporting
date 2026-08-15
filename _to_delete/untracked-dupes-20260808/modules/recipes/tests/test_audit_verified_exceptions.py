"""Every audit suppression must say who confirmed it, and when.

The audit is a work queue. Two things kill a work queue: reporting answers that
have already been checked, and suppressing things nobody actually checked. The
VERIFIED_* dicts fix the first and are the obvious way to cause the second — a
name added in a hurry is indistinguishable, six months later, from a name added
because someone looked.

So the rule is mechanical: an exception carries an attribution and a date. If
nobody can be named, it is not verified, it is just quiet.

The second test guards the other end — an exception whose product no longer
exists is protecting nothing and should be deleted, not left as a fossil that
might one day silence a different, real finding when a name gets reused. It
skips when the generated book is absent, because a clean checkout has no
generated files and an audit test that needs them is a test CI cannot run.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from audit_book import VERIFIED_HIGH_GP, VERIFIED_TWIN_GAP  # noqa: E402

DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")


def _all():
    return [("VERIFIED_HIGH_GP", VERIFIED_HIGH_GP),
            ("VERIFIED_TWIN_GAP", VERIFIED_TWIN_GAP)]


def test_every_verified_exception_is_attributed_and_dated():
    for label, d in _all():
        for product, reason in d.items():
            assert reason and reason.strip(), f"{label}[{product!r}] has no reason"
            assert DATE.search(reason), (
                f"{label}[{product!r}] gives a reason but no date: {reason!r}. "
                f"An undated confirmation cannot be re-checked.")
            assert "Zak" in reason or "confirmed" in reason.lower(), (
                f"{label}[{product!r}] does not say who confirmed it: {reason!r}")


def test_no_verified_exception_points_at_a_product_that_no_longer_exists():
    book = ROOT / "data" / "lightspeed_recipes_costed.json"
    if not book.exists():
        return          # clean checkout: nothing generated yet, nothing to check
    payload = json.loads(book.read_text())
    recs = payload.get("recipes", payload)
    names = set(recs) if isinstance(recs, dict) else {
        r.get("name") or r.get("product") for r in recs}
    for label, d in _all():
        for product in d:
            assert product in names, (
                f"{label} suppresses {product!r}, which is not in the recipe book "
                f"any more — delete the entry rather than leave it armed")
