"""
Email-domain → supplier_key mapping. Static config, deliberately dependency-free.

This used to live in build_corpus.py, which imports pull_mailbox → graph_auth →
msal. That meant anything wanting this tiny dict (the parser regression harness,
the battletest) dragged in the whole Microsoft-Graph mail stack and failed to
import without msal installed. The mapping is just data, so it lives here where
tests and tooling can read it with no heavy imports.
"""

from __future__ import annotations

DOMAIN_KEY: dict[str, str] = {
    "selectprovidores.com.au": "select_fresh",
    "foodlinkaustralia.com.au": "foodlink",
    "befoods.com.au": "be_foods",
    "tfft.com.au": "fresh_fruit_team",
    "gullifood.com.au": "gulli",
    "suncircle.com.au": "sun_circle",
    "junpacific.com": "jun_pacific",
    # JFC Australia is a SEPARATE company from Jun Pacific — ABN 36 003 080 260 vs
    # 71 054 434 061, and a completely different invoice system — despite every
    # JFC invoice already in data/invoices carrying supplier_key "jun_pacific".
    # Those were LLM-extracted before either had a parser and nothing checked the
    # ABN. Their product codes do not collide (JFC numeric, Jun Pacific
    # alphanumeric), so separating them costs nothing; build_cogs_list re-labels
    # the historical rows so the cost series stays continuous.
    "jfcaust.com.au": "jfc",
    "ilg.com.au": "ilg",
    "lionco.com": "lion",
    "paramountliquor.com.au": "paramount",
    # Andrews Meat: invoices are sent from accountsreceivable@andrewsmeat.com
    # (monthly statements come from the separate andrewsmeat.com.au domain and
    # are handled by the statement guard, not this parser). The coordinate
    # parser in parsers/andrews_meat.py was already registered on this domain;
    # this mapping wires it into the regression harness + corpus routing.
    "andrewsmeat.com": "andrews_meat",
    # Farmer Joes (F J Chickens) and Nicholas Seafood both HAVE working
    # coordinate parsers (parsers/farmer_joes.py, parsers/nicholas_seafood.py,
    # registered on these domains), but they were never added here. Production
    # was fine — run.py passes the real sender domain straight to parse_pdf — yet
    # everything that reads this mapping was blind to them:
    #   * parser_regression.py reverses DOMAIN_KEY to find each corpus dir's
    #     domain, got "", and scored both suppliers 0% (they are 8/15 and 7/10),
    #     which sent the daily triage task chasing parsers that already worked;
    #   * build_corpus.py routes by DOMAIN_KEY, so neither supplier's corpus
    #     could ever grow — they are the only two dirs it never touches.
    "farmerjoes.com.au": "farmer_joes",
    "nicholasseafood.com.au": "nicholas_seafood",
}
