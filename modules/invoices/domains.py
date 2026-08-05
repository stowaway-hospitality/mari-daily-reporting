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
    "ilg.com.au": "ilg",
    "lionco.com": "lion",
    "paramountliquor.com.au": "paramount",
    # Andrews Meat: invoices are sent from accountsreceivable@andrewsmeat.com
    # (monthly statements come from the separate andrewsmeat.com.au domain and
    # are handled by the statement guard, not this parser). The coordinate
    # parser in parsers/andrews_meat.py was already registered on this domain;
    # this mapping wires it into the regression harness + corpus routing.
    "andrewsmeat.com": "andrews_meat",
}
