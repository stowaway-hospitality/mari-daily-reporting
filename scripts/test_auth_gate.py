#!/usr/bin/env python3
"""
Guard the shared auth gate's invariants. auth.js decides who reaches every page
and who can write recipes — a quiet weakening (dropping the token check, letting a
null role through a role-gated page) would open the app without failing anything
visible. This asserts the invariants directly in the source, script-shaped so CI
runs it. Not a substitute for Supabase RLS (the real server-side gate) — a second
line so the client gate can't silently regress.

    python3 scripts/test_auth_gate.py     # exit 0 = ok, 1 = a gate weakened
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

AUTH = Path(__file__).resolve().parent.parent / "dashboard/_shared/auth.js"
src = AUTH.read_text()
fails: list[str] = []


def want(name: str, ok: bool, hint: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {hint}" if hint and not ok else ""))
    if not ok:
        fails.append(name)


# KITCHEN_ROLES is the write allowlist — admin + the three venue food roles.
m = re.search(r"KITCHEN_ROLES\s*=\s*\[([^\]]*)\]", src)
roles = set(re.findall(r'"([^"]+)"', m.group(1))) if m else set()
want("KITCHEN_ROLES = admin + venue food roles",
     roles == {"admin", "bigchef", "stowfood", "hgfood", "pizza"}, str(roles))

# canWrite REQUIRES a session token AND a kitchen role — never role alone.
cw = re.search(r"canWrite\s*=\s*\(\)\s*=>\s*(.+)", src)
cwbody = cw.group(1) if cw else ""
want("canWrite requires a token", "CACHE.token" in cwbody, cwbody[:70])
want("canWrite requires a kitchen role", "KITCHEN_ROLES.includes(CACHE.role)" in cwbody, cwbody[:70])

# admit() blocks a role-gated page when the role isn't in the allowed set — and
# roles=null (the falsy short-circuit) admits any signed-in user by design.
want("admit blocks when role not in the page's roles",
     re.search(r"if\s*\(\s*roles\s*&&\s*!roles\.includes\(\s*c\.role\s*\)\s*\)", src) is not None)

# requireToken throws rather than returning a blank token (a blank would POST an
# unauthenticated write that the worker must then reject).
rt = src[src.find("function requireToken"): src.find("function requireToken") + 220]
want("requireToken throws without a session", "throw" in rt and "CACHE.token" in rt)

# The admin invite dropdown, the worker's role allow-list, and the kitchen-writer
# set must agree. A role offered in the UI but not accepted by the worker fails
# invites silently; a kitchen writer the worker doesn't recognise can't write.
ROOT = AUTH.parent.parent.parent


def _roles(text: str, name: str) -> set[str]:
    m = re.search(name + r"\s*=\s*\[([^\]]*)\]", text)
    return set(re.findall(r'["\']([a-z_]+)["\']', m.group(1))) if m else set()


admin_roles = _roles((ROOT / "dashboard/admin/index.html").read_text(), "ROLES")
worker_src = (ROOT / "supabase/functions/shg-auth/index.ts").read_text()
worker_roles = _roles(worker_src, "ROLES")
worker_kitchen = _roles(worker_src, "KITCHEN")
want("admin invite dropdown == worker role allow-list",
     admin_roles and admin_roles == worker_roles, f"{sorted(admin_roles)} vs {sorted(worker_roles)}")
want("kitchen writers are a subset of valid roles", worker_kitchen <= worker_roles, str(sorted(worker_kitchen)))
want("auth.js kitchen roles are all valid roles", roles <= worker_roles, str(sorted(roles - worker_roles)))

print()
print("ALL PASS" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
