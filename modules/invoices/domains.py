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
    # Ordermentum is a PLATFORM sender (many vendors), like post.xero.com. The
    # key here is only what build_corpus/parser_regression file it under; the
    # real vendor comes from the ABN on the page. See parsers/ordermentum.py.
    "ordermentum.com": "ordermentum",
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
    # Xero mails on behalf of MANY suppliers, so this key names the PLATFORM, not
    # a vendor — parsers/xero.py identifies the vendor by the ABN on the page that
    # is not ours. The entry exists so build_corpus collects these and the
    # regression harness scores the parser like any other; a parser the harness
    # cannot see is exactly how Foodlink and FFT rotted.
    "post.xero.com": "xero",
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
    # NetSuite is a PLATFORM sender like post.xero.com and ordermentum.com: the
    # From address is system@sent-via.netsuite.com for every vendor that bills
    # through it, so this key names the platform and parsers/netsuite.py
    # identifies the real vendor from the ABN on the page. Registered because
    # the 2026-08-19 Review sweep found 14 stuck documents on this sender —
    # 12 Bacchus Wine Merchant invoices (a live supplier with an MOQ we order
    # against weekly) and 2 Dext subscription bills — and NONE of them were
    # visible to the regression harness, because a domain absent from this
    # mapping is a supplier build_corpus never collects. That is the item-4
    # survivor-sample failure in its other form: the corpus could not be wrong
    # about Bacchus, it had simply never heard of it.
    "sent-via.netsuite.com": "netsuite",
    # MYOB is a PLATFORM sender like post.xero.com, ordermentum.com and
    # sent-via.netsuite.com: every vendor that bills through MYOB mails from
    # noreply@apps.myob.com, so this key names the platform and
    # parsers/myob.py identifies the real vendor from the ABN on the page.
    # Registered because the 2026-08-19 Review sweep counted 8 stuck documents
    # on this sender (triage log item 40) — VMA, Cork And Co and AQUARIUS
    # FISHERIES, the last of which is KITCHEN FOOD and is already in
    # KITCHEN_SUPPLIERS from item 12, so it feeds recipe costs. A domain absent
    # from this mapping is a supplier build_corpus never collects and
    # parser_regression never scores: the item-4/item-40 blind spot again.
    "apps.myob.com": "myob",
    # Inalca Food & Beverage Australia and Deni Foods are the LAST TWO
    # kitchen-food senders left in the Review pile with no parser (triage log
    # item 46). Registered here BEFORE either has a parser, deliberately: a
    # domain absent from this mapping is a supplier build_corpus never collects
    # and parser_regression never scores, so the harness reports 98% while a
    # food supplier sits at zero — the item-4 / item-12 / item-40 blind spot in
    # its fifth form. With the entry present they show up in the table at 0/N,
    # which is the honest number and the one that names tomorrow's work.
    "inalcafb.com.au": "inalca",
    "denifoods.com.au": "deni_foods",
}
