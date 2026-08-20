"""
Deterministic, free per-supplier invoice parsers.

Try these FIRST; fall back to the LLM (extract.py) only when there's no parser
for the sender, the PDF is a scan (no text layer), or the parse doesn't
validate. Recurring suppliers have a fixed layout, so a parser reads them
exactly, for $0, and can't hallucinate a line — the validator still checks the
result reconciles, so a broken parser fails loudly, never silently.

Registered by the sender's email DOMAIN (stable; a supplier's billing domain
rarely changes, and an unknown domain simply routes to the LLM).
"""

from __future__ import annotations

from typing import Optional

from modules.invoices import pdf_text
from modules.invoices.models import Invoice

DOMAIN_TO_PARSER: dict[str, callable] = {}


def register(*domains):
    def deco(fn):
        for d in domains:
            DOMAIN_TO_PARSER[d.lower()] = fn
        return fn
    return deco


# Import parser modules so their @register decorators run.
from modules.invoices.parsers import select_fresh      # noqa: E402,F401
from modules.invoices.parsers import fresh_fruit_team   # noqa: E402,F401
from modules.invoices.parsers import foodlink           # noqa: E402,F401
from modules.invoices.parsers import be_foods           # noqa: E402,F401
from modules.invoices.parsers import ilg                # noqa: E402,F401
from modules.invoices.parsers import gulli              # noqa: E402,F401
from modules.invoices.parsers import jun_pacific        # noqa: E402,F401
from modules.invoices.parsers import jfc                # noqa: E402,F401
from modules.invoices.parsers import xero               # noqa: E402,F401
# AFTER xero: ordermentum imports vendor_from_abn from it.
from modules.invoices.parsers import ordermentum        # noqa: E402,F401
# AFTER xero: netsuite imports vendor_from_abn from it, same as ordermentum.
from modules.invoices.parsers import netsuite           # noqa: E402,F401
# AFTER xero: myob imports vendor_from_abn + SERVICE_SUPPLIERS from it, same as
# ordermentum and netsuite. apps.myob.com is another platform sender.
from modules.invoices.parsers import myob               # noqa: E402,F401
from modules.invoices.parsers import lion               # noqa: E402,F401
from modules.invoices.parsers import paramount          # noqa: E402,F401
from modules.invoices.parsers import nicholas_seafood   # noqa: E402,F401
from modules.invoices.parsers import andrews_meat        # noqa: E402,F401
from modules.invoices.parsers import farmer_joes         # noqa: E402,F401


#: Senders that are US, not a supplier. A FORWARDED invoice arrives with one of
#: these domains, so the sender says nothing about which parser fits — Zak and
#: Nicola forward KegLand invoices (citric and malic acid, no direct email
#: relationship with the mailbox), and routing those by sender could only ever
#: mean "no parser". For a forward, every parser gets a try, and the arithmetic
#: validator downstream is what keeps that safe: a wrong parser's output does
#: not reconcile to the printed total, so it cannot promote. Same gate that has
#: guarded the LLM path since INVOICES.md was written.
_OUR_DOMAINS = {"stowawaybar.com"}


def parse_pdf(pdf_bytes: bytes, sender_domain: Optional[str] = None) -> Optional[Invoice]:
    """
    Deterministic parse if we have one for this sender, else None (-> LLM).
    Returns None on any failure so the caller falls back cleanly.

    A sender in _OUR_DOMAINS is a FORWARD: the domain identifies the forwarder,
    not the supplier, so every registered parser is tried and the FIRST result
    that carries a stated total is returned — reconciliation to that total is
    judged by the caller, exactly as for a sender-routed parse. A parser that
    fires on another supplier's layout produces arithmetic that does not add
    up, and the validator refuses it; the cost of a wrong attempt is zero and
    the cost of not trying is a Review pile that can never shrink for any
    supplier who only reaches us by forward.
    """
    if not pdf_text.has_text_layer(pdf_bytes):
        return None                       # scanned image -> LLM
    dom = (sender_domain or "").lower()
    if dom in _OUR_DOMAINS:
        for fn in dict.fromkeys(DOMAIN_TO_PARSER.values()):
            try:
                inv = fn(pdf_bytes)
            except Exception:
                continue
            if inv is not None and getattr(inv, "total_incl", None):
                return inv
        return None
    fn = DOMAIN_TO_PARSER.get(dom)
    if not fn:
        return None
    try:
        return fn(pdf_bytes)              # parsers get the bytes; they pick text vs coordinates
    except Exception:
        return None                       # parse broke -> LLM
