"""A forwarded invoice's sender is us, and "us" is not a supplier.

Zak and Nicola forward KegLand invoices (citric and malic acid — $24k of
four-week revenue stands on each) because KegLand has no direct email
relationship with the accounts mailbox. Routing by sender domain, those
forwards could only ever mean "no parser": the domain identifies the
FORWARDER. So parse_pdf now treats a sender in _OUR_DOMAINS as a forward and
tries every registered parser, returning the first that produces a stated
total — safe because the arithmetic validator downstream refuses anything
that does not reconcile to the printed total, the same gate that has guarded
the LLM path since INVOICES.md was written.

These tests pin the dispatch, with fakes rather than fixtures, because what
is under test is the ROUTING decision and not any parser's layout knowledge.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.invoices import parsers as P  # noqa: E402


class _Inv:
    def __init__(self, total):
        self.total_incl = total


def _with_fakes(monkeypatch, table):
    monkeypatch.setattr(P, "DOMAIN_TO_PARSER", table)
    monkeypatch.setattr(P.pdf_text, "has_text_layer", lambda b: True)


def test_a_forward_tries_every_parser_until_one_produces_a_total(monkeypatch):
    calls = []

    def broken(_b):
        calls.append("broken")
        raise ValueError("wrong layout")

    def silent(_b):
        calls.append("silent")
        return None

    def kegland_shaped(_b):
        calls.append("hit")
        return _Inv("214.50")

    _with_fakes(monkeypatch, {"a.com": broken, "b.com": silent, "c.com": kegland_shaped})
    inv = P.parse_pdf(b"%PDF", "stowawaybar.com")
    assert inv is not None and inv.total_incl == "214.50"
    assert calls == ["broken", "silent", "hit"], (
        "a forward must survive parsers that raise or return nothing — "
        "one supplier's layout crashing another's parser is the normal case")


def test_a_forward_with_no_matching_parser_goes_to_review(monkeypatch):
    _with_fakes(monkeypatch, {"a.com": lambda b: None})
    assert P.parse_pdf(b"%PDF", "stowawaybar.com") is None


def test_a_direct_sender_still_routes_only_to_its_own_parser(monkeypatch):
    """The forward path must not loosen normal routing: an unknown supplier
    domain still returns None (-> LLM or Review), and a known one still gets
    exactly its own parser rather than a free-for-all."""
    def only_for_a(_b):
        return _Inv("10.00")

    _with_fakes(monkeypatch, {"a.com": only_for_a})
    assert P.parse_pdf(b"%PDF", "a.com").total_incl == "10.00"
    assert P.parse_pdf(b"%PDF", "unknown-supplier.com") is None


def test_each_parser_is_tried_once_even_when_registered_under_two_domains(monkeypatch):
    """xero-style parsers register several domains; a forward must not run the
    same parser three times (some parsers are expensive coordinate readers)."""
    calls = []

    def multi(_b):
        calls.append(1)
        return None

    _with_fakes(monkeypatch, {"a.com": multi, "b.com": multi, "c.com": multi})
    P.parse_pdf(b"%PDF", "stowawaybar.com")
    assert len(calls) == 1
