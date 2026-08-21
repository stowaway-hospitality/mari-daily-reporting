#!/usr/bin/env python3
"""
Score the deterministic parsers against the local corpus.

    python3 modules/invoices/parser_regression.py [supplier_key ...]

For each supplier with invoices in data/invoice_corpus/ (built by
build_corpus.py), parse every PDF and validate it. Reports, per supplier:

    PASS         parsed AND reconciled to the printed total  -> free & correct
    review       parsed but didn't reconcile                 -> falls to the LLM
    parse-fail   parser errored / returned nothing            -> falls to the LLM
    not-inv      a statement / remittance / direct-debit form -> skipped upstream
    scan         no text layer                                -> LLM (needs OCR)

The PASS rate is the number to drive up, and it is now computed over PARSEABLE
INVOICES ONLY — the not-inv and scan columns come out of the denominator.

That matters, because lumping them in was actively misleading. It reported
andrews_meat at 71% when all 10 "failures" were monthly STATEMENTS the parser is
right to refuse, and sun_circle at 0% when all 15 of its PDFs are scans with no
text layer for any parser to read. A previous run of the daily triage task could
spend its day building a parser for a supplier whose invoices are images.

The harness now applies run.py's own looks_like_statement first, exactly as
production does before it ever reaches a parser — so a supplier's PASS rate here
means what it says: of the real, readable invoices, how many parse for free.

TRIAGE LOG — 2026-08-08: every remaining non-PASS was opened and identified.
None of them is a parser defect, so do NOT spend a day writing a parser for one.
The corpus is at 407/415 (98%), and the 8 shortfalls are:

  * be_foods d02385290774 — a $0.00 "PICK UP RETURN FOR CREDIT" docket.
  * ilg      e23ce69fe899 — a $0.00 WOS invoice (qty column literally "WOS").
  * ilg      b46bfb0a542a — a "TAX ADJUST" buy-back note, not a tax invoice;
                            its only line is "Stock / Quantity - 4 / Buy at -
                            $65.89" with no product code.
  * paramount 670685f29215 — the NSW April 2026 craft-beer PRICE LIST.
  * farmer_joes 4444676 — parses fine; SANITY_BOUNDS correctly fires because
                            CHICKEN BONES at $0.80/kg is under the $1.00/kg
                            per_kg floor. Real price. Fixing it means raising a
                            GLOBAL floor downward, which weakens the net for
                            every supplier in the too-cheap direction. Left.
  * reward_dist (2), vanguard (1) — no parser, but both are dormant: 0 invoices
                            in data/invoices, corpus copies date from 2020-2024,
                            and neither domain is in domains.py. Not worth one.

TRIAGE LOG — 2026-08-09: paramount was found at 13/20 (65%), six invoices below
the reading above. Not drift, and not a new supplier layout: all six parsed and
reconciled TO THE CENT, then failed SANITY_BOUNDS on a single line — WHITE LIGHT
VODKA ORIGINAL "1/20000 ml" at $1,012.78. That is one 20 L drum, and the price
is right; it was being bound-checked against per_unit's $500 ceiling because the
parser called every proved line PER_UNIT. Fixed by giving a single-unit pack of
>= 4 L its own basis (CostBasis.PER_BULK) and its own bounds, so per_unit keeps
its $500 for the other ~40 stock lines. paramount 13/20 -> 19/20, TOTAL
401/415 -> 407/415, and paramount's only remaining shortfall is the price list
above. Note the platform had already diagnosed this itself: the cost-book
"config" flag named the exact product and price and said it "goes to review on
every delivery". Closing the gap emptied that flag.

TRIAGE LOG — 2026-08-14: corpus grew by one (ilg +1) to 416 readable invoices and
the new one PASSED, so 407/415 -> 408/416 (98%). Every one of the 8 shortfalls was
re-opened and is byte-for-byte the SAME document named in the 08-08 entry above —
be_foods d02385290774, ilg b46bfb0a542a + e23ce69fe899, paramount 670685f29215,
farmer_joes 4444676, reward_dist x2, vanguard x1. No drift, no new failure mode,
no parser defect. NO parser change was made: there is nothing left to win here
that does not need Zak's decision first (see the two shared gates below).

  Still blocked on a decision, not on effort:
  * sun_circle — 15 PDFs in the last 4 months, ALL of them image scans with a
    zero-length text layer (verified: word_rows() returns 0 rows on every one).
    No deterministic parser is possible; these need OCR. Worth Zak's attention
    because only 1 Sun Circle invoice exists in data/invoices against those 15
    arrivals, so a kitchen supplier's costs are largely not reaching the DB.
  * the three $0.00 documents — unchanged, still need the run.py::looks_like_
    statement gate described below, which is cross-supplier and wants review.

TRIAGE LOG — 2026-08-15: the first REAL parser defect this log has found, and it
was invisible to this harness by construction. FOODLINK re-templated: the whole
line table moved right by ~25pt (Qty. 251 -> 277.5, UOM 272.5 -> 306.4, Weight
327 -> 345). parsers/foodlink.py bucketed on hard-coded x-starts, so the Qty.
VALUE at x=290 landed past the uom boundary (270); c["qty"] came back empty, the
`qty is None` guard skipped every row, and parse() raised "no line items parsed"
on EVERY Foodlink invoice from 2026-07-29 onward. Ten invoices had been sitting
in Review for 2.5 weeks and the last Foodlink invoice to reach data/invoices was
2026-07-28 — a kitchen supplier's costs simply stopped arriving.

  Why this table said 100% the whole time — worth fixing, still open:
  build_corpus.py scans "/mailFolders/inbox/messages" ONLY. pull_mailbox has
  already MOVED every failing invoice to the Invoices Review folder by the time
  the corpus is built, so a document can only enter the corpus if it SUCCEEDED.
  The corpus is therefore a survivor sample and the PASS rate is blind to exactly
  the failures it exists to catch. Compounding it, --per caps a supplier's corpus
  (foodlink was at the 60 cap since July), so `have >= per` skipped foodlink
  entirely and no new layout could ever enter. Two cheap fixes for a future run,
  both needing Zak's nod because they change what the corpus IS: (a) also page
  the Invoices Review folder in build_corpus, (b) make --per keep the NEWEST N
  rather than the first N found. Until then, a parser can rot silently.

  Fix: boundaries are now DERIVED from the header row's own word x-positions
  (foodlink.py::_cols_from_header); COLS remains only as a fallback. Verified on
  all 10 stuck invoices — every one now parses AND reconciles to its printed
  total (e.g. SI4525247: 124.00 + 158.00 + 3.00 Fuel Levy + 0.30 GST = $285.30,
  the stated total, to the cent). The 10 PDFs were copied into the corpus by hand
  (gitignored) so this cannot regress unseen: foodlink 59/59 -> 69/69, TOTAL
  408/416 -> 418/426 (98%). No other supplier moved. Five fixture tests added in
  tests/test_parsers.py, including one that asserts the OLD hard-coded COLS would
  have mis-bucketed the new qty, so the diagnosis itself is pinned.

  The 11th Foodlink document in Review is a Statement of Account. It is correctly
  refused, but it scores as parse-fail rather than not-inv because
  looks_like_statement misses it, so it was deliberately NOT added to the corpus.
  Same shared-gate question as the $0.00 documents below — left for Zak.

  The 8 pre-existing shortfalls were re-confirmed unchanged and are still the
  same documents named in the 08-08 entry: be_foods d02385290774, ilg b46bfb0a542a
  + e23ce69fe899, paramount 670685f29215, farmer_joes 4444676, reward_dist x2,
  vanguard x1. No change made to any of them.

  Also note: build_corpus.py WEDGED on the network for 1h45m this run (2 open
  sockets, 1.75s CPU, zero files written) and had to be killed, exactly like the
  stale pull_mailbox.py found at start-up. It has no timeout. Worth one.

TRIAGE LOG — 2026-08-15 (second pass, same day). Zak: "fix it all and improve it".
Everything the morning entry had parked as "needs Zak's nod" is now done, plus the
sibling defect that entry's own evidence pointed at.

  1. FRESH FRUIT TEAM had the SAME bug as Foodlink, and this table could not see
     it either. FFT's header does not sit still: across the corpus the ITEM anchor
     ranges 181.7 -> 201.3. The hard-coded desc boundary was 198 — the TOP of that
     range — so on every invoice whose ITEM anchor sat left of 198 the
     description's FIRST WORD fell into the unit bucket: raw_uom "Carrot",
     description "Large". 274 lines and 51 of 119 codes affected, while FFT scored
     52/52 (100%) throughout, because a wrong UNIT still reconciles to the cent.
     This is the actual source of the "Large"/"Ruby Red"/"Baby Gem" fragments the
     product triage has been flagging for weeks — they were never a naming problem
     downstream, they were a column boundary here. Fixed with the same
     header-derived boundaries as foodlink: 274 bad units -> 32, and 31 of those
     32 are FFT's REAL "Market" (head of "Market Bunch"). Split codes 51 -> 6.
     Money unchanged: FFT 52/52, TOTAL 418/426.

  2. Order notes are no longer product names. FFT reprints our own picking
     instructions inside the ITEM cell ("Zucchini Green 0.5Kg please", "please
     make sure all product are"). Cut at the courtesy word. Deliberately NOT
     stripping the trailing size a note leaves behind ("Chillies Red Long 200g"):
     measured, and pack size comes from raw_uom, never the description, so those
     are cosmetic and cost-neutral — and a greedy size regex would eat the REAL
     size in "Mushroom King Brown (200G Punnet)", the exact mistake that once
     booked a 200 g punnet as 800 g.

  3. THIS HARNESS NOW CHECKS UNITS, not just codes. The identity audit caught a
     column bleeding into the CODE cell; it was blind to the same drift bleeding
     into the UNIT cell. New audit: a raw_uom that does not name a unit is another
     column's text, which means the description lost a word. Allowlist carries the
     five supplier-printed abbreviations verified today (be_foods DRU/EAC/PK/ROL,
     andrews_meat PK, ilg 1xKEG49./1xKEG50, paramount MISC, fft Market/400gm) so
     the alarm stays quiet enough to be worth reading. Both audits are clean now.

  4. THE CORPUS IS NO LONGER A SURVIVOR SAMPLE. build_corpus.py scanned the inbox
     only, but pull_mailbox MOVES everything it handles out of the inbox, so a
     document could only enter the corpus if it had already PARSED — the one
     population this harness exists to measure was structurally excluded. It now
     pages Review and Processed too, NEWEST first, and --per is a per-run budget
     rather than a hard ceiling (foodlink sat at the 60 cap since July, so no new
     layout could ever get in no matter how often it ran).

  5. THE WEDGE HAD ONE CAUSE: urlopen() with no timeout, at the bottom of every
     mail path. That is why a pull_mailbox was found hung for ~21h and a
     build_corpus for 1h45m. GRAPH_TIMEOUT=60s with a clear error; a stuck socket
     now fails loudly and the next run retries.

  6. The Review retry pass was starving its own backlog — both passes took the
     newest 20, so in a ~60-message folder anything older was never re-tried
     (which is why the Foodlink fix recovered only 3 of 10 until a manual
     --max 60). The retry pass now sweeps the whole folder, but ONLY under
     --no-llm: a billed retry stays on the small batch so this cannot turn a
     20-message run into a 200-message bill.

  7. Foodlink's monthly Statement of Account is now classified. It has no masthead
     in the text layer at all — no "statement", no "tax invoice" — so titled+strong
     never fired and it cycled through Review forever. Ageing buckets ("Current |
     7 Days | 14 Days | 21 Days") are now sufficient alone: an invoice states ONE
     set of terms, never a spread. Verified against the whole corpus first — of
     418 PASSing invoices, ZERO match the rule, so it cannot swallow a real bill.

TRIAGE LOG — 2026-08-15 (third pass). Zak: "go and fix it all". sun_circle is now
CLOSED as a question, and the product-name flags are fixed at their source.

  8. SUN CIRCLE IS NOT AN OCR PROBLEM AND NEVER WAS. Every previous entry logged
     "15 image scans, needs OCR" without opening one. They were opened. Sun Circle
     invoices are a PRE-PRINTED ORDER FORM FILLED IN BY HAND: the product names
     and pack sizes are printed (bilingual EN/ZH), but the qty, unit price, line
     amount, date and total are all written in pen ("48 x 4.50  216", total
     "540.-"). The handwriting is the only data we need.
     Tesseract reads print, not handwriting — it would return confident nonsense
     for exactly the fields that matter, so an OCR pipeline could not have worked
     and buying one would have been wasted. DO NOT re-open this as an OCR task.
     The real fix is upstream and commercial: ask Sun Circle to email a digital
     invoice. Failing that it is ~3-4 lines a week to key by hand.

  9. IMAGE-ONLY PDFs NOW STOP BEFORE THE EXTRACTOR (run.py, new exit code 4).
     Previously a scan with no parser fell through to the LLM, which meant the
     extractor was being asked to read handwritten money. That is worse than
     useless: it guesses the line amounts AND the total from the same strokes, so
     it can guess CONSISTENTLY and still reconcile — a wrong number that passes
     validation. Exit 4 keeps them in Review labelled "manual entry", distinct
     from exit 2 "no parser yet", so future triage stops re-investigating them.
     All 17 scans in the corpus were checked and NOT ONE is a printed invoice:
     15 Sun Circle handwritten forms, 1 blank ILG Direct Debit Request, 1 blank
     B&E Credit Card Authorisation form (which also should never have been going
     to an extractor). So this costs zero automation.

 10. THE TRUNCATED PRODUCT NAMES ARE FIXED, INCLUDING THE HISTORY. The parser fix
     (item 1) stops new ones, but data/ already held both spellings and could not
     be re-parsed — the source PDFs sit behind the Supabase service key, which
     this pipeline must never hold. build_ingredients keyed names by (identity,
     RAW code) and took each code's LATEST name, so where one code carried both
     "Carrot Large" and "Large" the most recent invoice decided what the chef saw.
     New rule (_undo_dropped_prefix): a fragment that is a WORD-BOUNDARY SUFFIX of
     a longer spelling of the SAME code is a dropped leading word, not a rename —
     a real rename shares no suffix. Measured on the live cost book: repairs 44 of
     the 52 FFT codes carrying more than one description, touches nothing else.
     Side effect, and the point: with real names restored, four duplicate picker
     entries finally collapsed onto their siblings (CLKG into CL20KGBX "Carrot
     Large", OSKG into OS10BG "Onion Spanish", HCMB, HCDRMB). The chef's list no
     longer shows the same carrot twice.

 11. THE MORNING RUN'S "PACKAGING COSTED AS FOOD" FLAGS WERE WRONG — WITHDRAWN.
     Packaging is in the ingredient picker BY DESIGN: that is how Marilyna's
     per-pizza box cost is tracked, and the Gulli boxes are correctly costed per
     each ($0.48 / $0.64 / $0.79). _NON_FOOD deliberately omits "box" and says so.
     No change was made to it. What the review DID turn up is a real pack defect
     hiding next to them: B&E "Pizza Box Liner Brown 9\"" is costed $21.56 per
     CARTON with needs_pack_review FALSE, while every sibling is per each — a
     recipe adding one liner books ~100x its true cost. Flagged, not guessed: the
     liner count is not stated on the invoice.

TRIAGE LOG — 2026-08-15 (fourth pass). Zak, on Sun Circle: "what matters is if
they update their pricing ... I just care about updating our cost prices." That
reframing found a much bigger miss than Sun Circle, and a bug I nearly shipped.

 12. WHOLE SUPPLIERS WERE MISSING FROM THE CHEF'S PICKER, and nothing reported it
     because an ingredient that never appears raises no flag. build_cogs_list's
     SUPPLIER_ALIAS maps supplier_key -> the short name build_ingredients
     recognises; a supplier with no entry falls back to the LEGAL name on the
     invoice and then fails the KITCHEN_SUPPLIERS test. Missing entries were
     sun_circle, jun_pacific, farmer_joes, the_berry_man, nicholas_seafood.
     Effect: 128 Jun Pacific cost rows (23 SKUs of Asian pantry goods, invoices
     headed "JFC AUSTRALIA CO PTY LTD"), every Sun Circle dumpling, the Berry Man
     purees and JFC's ramen bases had NEVER reached a recipe. Feed 1088 -> 1208.
     Aquarius Fisheries added to KITCHEN_SUPPLIERS as well (seafood).

     Two further fixes so it cannot recur silently:
       * is_kitchen_supplier() matches the normalised TRADING name (legal-suffix
         and leading-"The" stripped, prefix match), so "Jun Pacific",
         "Jun Pacific Corporation Pty Ltd" and "JUN PACIFIC CORP" are one
         supplier. A test pins both the inclusions AND the liquor exclusions —
         a prefix rule that swallowed ILG or Paramount would put bottles in a
         food picker.
       * `supplier` added to build_cogs_list.DERIVED. It is SUPPLIER_ALIAS applied
         to supplier_key, not a fact off the page, so an alias fix must reach rows
         already written — otherwise purchasable_id keeps slugging the legal name
         and ONE product holds TWO identities. It did:
         "jun-pacific-corporation-pty-ltd:HA8204612" carried 8 price observations
         while "jun-pacific:..." carried the rest, so that curry's price history
         was split in half. 150 rows re-derived; the row key is
         (invoice, code, description), so relabelling cannot move a row.

 13. A 6x UNDERSTATEMENT I INTRODUCED AND CAUGHT BEFORE SHIPPING. Fresh Fruit Team
     sells the same herb as a single bunch AND as a MARKET bunch and calls both
     "Herb Chives" on the invoice — only the code and the price separate them
     (HCBCH $2.42 vs HCMB $15.40; HCB $2.64 vs HCDRMB $7.70). They had survived
     the (supplier, name) collapse ONLY because the old parser bug truncated one
     of each pair to "Chives"/"Coriander". Repairing those names in item 10 made
     the names match, and that collapse — whose tiebreak prefers the CHEAPER row —
     silently deleted both market bunches. Zak had personally confirmed both packs
     in pack_overrides.
     Found by rebuilding the feed at the pre-change commit and diffing, which is
     now the standard check for any change to this pipeline. Fixed: the collapse
     merges only when the canonical $/unit agree within 10% AND the pack units
     match; otherwise both rows are kept and the dearer is disambiguated with the
     supplier's own code ("Herb Chives (HCMB)"). Legitimate merges still happen —
     CLKG per-kg still folds into CL20KGBX, same carrots, same $/kg. As a bonus
     BRL (Broccolini box, $3.36/ea) came back; it had been silently absent before
     today for the same reason.

 14. One override was orphaned by the alias fix and it cost money for exactly as
     long as it took to notice: pack_overrides "the-berry-man-nsw-pty-ltd:PJ1"
     stopped matching once the id canonicalised to "the-berry-man:PJ1", and
     Passionfruit Puree fell from $9.50/kg to $0.79/kg — the precise 12x-too-cheap
     reading that override's own comment exists to prevent. Re-keyed. An audit of
     all 72 override keys against the live feed now runs as part of this work; the
     only remaining misses are four lightspeed ids with no ingredient at all
     (out-of-window, pre-existing).

  ON SUN CIRCLE'S PRICES SPECIFICALLY (Zak's actual question): there are NO
  printed prices on that form to watch. The Unit Price column is blank in print
  and filled in by hand on every one of the 15 scans. What CAN be watched is the
  cost book: Sun Circle's four SKUs now appear (SC-BEEFCABBAGE-DUMP,
  SC-CHICKENCORN-DUMP, SC-PORKPARSLEY-DUMP at $4.50 a 600g pack = $7.50/kg, and
  SC-PRAWNHARGAO-LG at $32.00 = $32/kg), sourced from the one invoice that was
  keyed. Any future keyed invoice moves those numbers and the existing price-move
  reporting picks it up. Nothing else is possible without a digital invoice.

 15. THE REVIEW PILE, worked by category rather than by supplier. 22 documents in
     it were never bills at all and no parser could ever have reconciled them:
     13 CartonCloud "Proof Of Delivery" dockets (quantities, no prices, emailed
     per consignment on behalf of the brewers) and 9 Xero statement ledgers that
     slipped the guard because they print neither "amount enclosed" nor an ageing
     spread. An "Invoice Amount" COLUMN beside a "Balance Due" is now sufficient:
     an invoice states its own total, it does not tabulate other invoices against
     a running balance. Verified the same way as the ageing rule — of the 418
     PASSing invoices, ZERO are swallowed, and a real Xero TAX INVOICE from the
     same sender still classifies as an invoice.

 16. A GENERIC XERO PARSER WAS INVESTIGATED AND DELIBERATELY NOT WRITTEN. It looks
     like the obvious next win — 21 real invoices from post.xero.com, one shared
     template, and it would keep catching new small suppliers. The table itself is
     easy (Description | Quantity | Unit Price | [Discount|GST] | Amount AUD,
     reconciling to "TOTAL AUD"). The blocker is SUPPLIER ATTRIBUTION, and it is
     the same identity-corruption class as everything else in this log:

       * post.xero.com is the SENDER for every one of them, so the domain -> parser
         registry cannot name the supplier. The supplier differs per document
         (Urbun Bakery, Canton Group, Grifter, Philter, Speed Gas, Sigurd Wines,
         Cordless Filter, Beerline Cleaning, SYMSAFE, Twin Fin Studio, MODA).
       * The obvious in-PDF key does NOT work. Reading the first ABN off the page
         returns 17606243921 for Urbun Bakery, Canton Group, Twin Fin Studio, MODA
         AND Philter — because that is STOWAWAY'S OWN ABN; the Xero template
         prints the customer's ABN in the block above the supplier's. A parser
         built on that would file five different suppliers under one identity and
         merge their price histories. Exactly the failure items 12 and 13 were.

     The reliable name is in the email SUBJECT, which Xero generates consistently
     ("Invoice <ref> from <SUPPLIER> for <VENUE>") and which pull_mailbox already
     passes to run.py as --source. Wiring that through to parse_pdf (which today
     takes only bytes + sender domain) is the real piece of work, and it changes a
     shared interface, so it wants a deliberate change rather than the tail of a
     long session. Note also that the value is smaller than the count suggests:
     of the 21, only Urbun Bakery and Canton Group are kitchen food — the rest are
     liquor and services, which do not feed recipe costs.

 17. THE XERO PARSER GOT WRITTEN AFTER ALL, and item 16 above was wrong about the
     hard part. Threading the email subject through parse_pdf was the wrong fix:
     it changes a shared interface, and it leaves the parser UNCOVERABLE, because
     the corpus stores PDFs by content hash and has no subject to replay. A
     parser the harness cannot see is precisely how Foodlink and FFT rotted, so
     that was not an acceptable shape.

     The vendor IS on the page: it is the ABN THAT IS NOT OURS. Reading "the
     first ABN" is the trap (Stowaway's own 17 606 243 921 is printed above the
     supplier's on half these templates, so it appears on invoices from five
     different vendors), but taking every ABN and dropping our own leaves exactly
     one candidate on every invoice bar a single SYMSAFE credit note that
     references a second party — and that one returns None and stays in Review.
     Explicit ABN -> supplier_key registry; an unregistered vendor is never
     guessed. No interface change, and post.xero.com now has a DOMAIN_KEY entry
     so build_corpus collects it and this table scores it like any other parser.

     Three tax conventions had to be told apart, and NOT by keyword:
       Grifter    Subtotal 270.50 + "TOTAL GST 10% 27.05" -> TOTAL AUD 297.55
       Speed Gas  one line at 64.40, "INCLUDES GST 10% 5.85", TOTAL AUD 64.40
     The same-looking Amount column is EX on one and INC on the other. The parser
     decides by ARITHMETIC against the printed total — whichever hypothesis
     reconciles wins, and if neither does it changes nothing and lets the
     validator refuse the invoice.

     xero 17/22. The 5 remaining: 3 unhandled template variants, 1 credit note
     with an ambiguous vendor, and Twin Fin Studio's $4,400 design invoice, which
     parses fine and is held by SANITY_BOUNDS — a non-food service line hitting a
     food bound, correctly going to a human. TOTAL 422/430 -> 439/452 (97%); the
     percentage went DOWN because the unhandled Xero templates are now counted
     rather than invisible, which is the honest direction.

     Two things worth recording about how this went:
       * The identity audit added this morning flagged the new parser within
         minutes ("XPA 200"). It turned out to be Philter's real item code,
         printed as two tokens inside the Item column — allowlisted with the
         evidence. The check earned its keep on the day it was written, and the
         answer was to read the invoice rather than widen the rule.
       * A fixture test written from a header I had labelled "Speed Gas" was
         actually Cordless Filter's "Quantity(L)", and it failed — revealing that
         stripping non-letters turned that into "QuantityL" and matched nothing.
         Brackets now come off before letters do, and two more invoices parse.

  STILL DELIBERATELY OPEN, with reasons:
  * The three $0.00 documents (be_foods d02385290774, ilg e23ce69fe899 +
    b46bfb0a542a). The 08-14 entry wanted a $0.00 gate because they "cost an LLM
    call on every retry pass forever" — but the daily task always runs --no-llm,
    where they cost NOTHING, and all three are credit/adjustment dockets that a
    human genuinely should see. A text-level "stated $0.00" detector is fragile
    for no benefit, so it was NOT written. Correctly parked, not stuck.
  * sun_circle — SUPERSEDED by item 8 above. This entry (and the 08-14 one) said
    "needs OCR". That was wrong, and it was wrong because nobody had opened the
    documents: they are handwritten order forms, so no OCR engine can read the
    numbers. Left here only so the correction is visible next to the claim.
    The open item is commercial — ask Sun Circle for a digital invoice — not
    technical. Still the biggest coverage gap: ~1 invoice in data/invoices
    against 15 arrivals, and every one of those has to be keyed by hand.

TRIAGE LOG — 2026-08-15 (fifth pass, unattended daily run). Corpus 452 -> 732
readable invoices, because item 4's build_corpus fix ran for the first time on a
folder set that had never been paged. That is the entry's own prediction coming
true: a bigger, less flattering sample. TOTAL 440/452 (97%) -> 717/732 (97%).

 18. GULLI'S ONE FAILURE IS NOT A PARSER DEFECT — do not write a parser for it.
     CI-437314 (corpus b381fb197ab6) raises "no line items parsed" and it is a
     FOURTH zero-total document, not a fifth column-drift. Its single line reads
     "Barbaro-Soppressata Hot (Zig Zag) r/w 2.5kg / 1.400 kg / 31.19000 / DISC.%
     100.00 / 0% / $0.00", the Customer Reference field literally says "sample",
     and the footer Total is $0.00. It is a free sample docket. It joins the
     three documents parked at the foot of this docstring and is refused by the
     same validator rule for the same correct reason. Gulli's other 31 pass.
     Checked specifically because Gulli's corpus PDFs all dated 23 July and the
     Foodlink/FFT pattern (hard-coded x-boundaries, silent rot) fits it exactly —
     gulli.py still buckets on literal 125/335/330-360. It has not drifted YET,
     but it is the last parser in the tree still doing that, and it is kitchen
     food. Worth a header-derived rewrite on a day with nothing more urgent.

 19. XERO 66/82 (80%) -> 77/82 (93%), eleven invoices, two causes.

     (a) OUR SECOND ABN WAS NOT ON THE CUSTOMER LIST. Item 17 recorded the
         SYMSAFE credit note as "an ambiguous vendor ... references a second
         party" and item 16's test pinned that reading. It was wrong. The second
         ABN is 38 760 949 765 — OURS. It appears in 33 corpus documents and in
         every single one it sits inside the ship-to block under "STOWAWAY
         FRESHWATER / SHOP 18, 1-3 MOORE ROAD", never on a letterhead. Added to
         CUSTOMER_ABNS. Worth noting how the error survived: the claim was made
         from ONE document, and it was plausible, so the next four passes reused
         it instead of re-checking. The fix was checked corpus-wide before it
         shipped, and the old test now pins the CORRECTION rather than the claim.

     (b) Four vendors were unregistered and one template variant was unhandled:
           98610948813  Wine Enterprises Pty Ltd          4 invoices
           98146579053  Australian Wine Company           1
           26681889154  Australia Wine & Spirits Pty Ltd  1
           48540665321  Prime Catering Repairs            2  (SERVICE_SUPPLIERS)
         Every name is PRINTED ON THE PAGE of the invoice carrying that ABN —
         none inferred from the product range or the suburb, which for the
         Massenez one was tempting and would have been a guess. Its masthead
         reads "IA WINE & SPIRITS PTY LTD" (the logo covers the first letters);
         the name used is the one in its own bank block.

         The variant is the Beerline Cleaning Company's fixed monthly fee:
         "Description | GST | Amount AUD", no Quantity and no Unit Price,
         because the invoice sells an agreement and there is no unit to count.
         A missing quantity is not a reason to refuse a bill — it is one implied
         unit — so _cols_from_header now accepts that shape, but ONLY when the
         header has no Quantity, no Unit and no Price label, and parse() looks
         for a full header FIRST and falls back to the reduced one only if no
         full header exists anywhere on the page. Without that ordering a stray
         "Description ... Amount" row could outrank the real header on a normal
         invoice and every line would read as one unit at the line total.

     THE DANGEROUS NEIGHBOUR, and why the Description column is the gate: Xero's
     payment RECEIPT ("Total AUD paid", "Amount Paid", "Still Owing") ALSO has no
     Quantity column, also states a total, and would reconcile — and it is a
     record of an invoice ALREADY BOOKED, so parsing one double-counts the money.
     Three SYMSAFE receipts sit in this corpus. A receipt tabulates OTHER
     invoices and therefore never has a Description column, so requiring one
     keeps all three out; verified after the change, and pinned by a test.

     The 5 that remain are all correct refusals: 3 SYMSAFE payment receipts (see
     above — they should be classified not-inv, see item 20), the SYMSAFE credit
     note (now names its vendor, then stops at "invoice total not found" because
     a CREDIT ADVICE states a "Credit Amount" and no total), and 164542cc0a23,
     a "Tax Invoice / Bill to / Attn: OLIVER" template unrelated to the others.

 20. FOR ZAK — TWO CLASSIFICATION GAPS DELIBERATELY NOT SHIPPED, both because
     they change run.py::looks_like_statement, which is cross-supplier:
       * Xero payment RECEIPTS (3 in corpus, more in the Review folder). Refused
         today only as a side effect of the header gate, which is thinner
         protection than it deserves given the failure mode is double-counting.
       * B&E "CUSTOMER STATEMENT - 21 JUL 26 - C200028467" reached a parser at
         all — it says CUSTOMER STATEMENT in its own subject line and is not
         caught by the ageing-bucket or Invoice-Amount-column rules.

 21. OPERATIONAL, and it cost this run most of the backlog: pull_mailbox's Review
     sweep DIED PART-WAY on a single transient "Graph 504 UnknownError" while
     fetching one message's attachments, at message 120 of 200. One flaky call
     aborts the whole sweep and the remaining 80 are never re-tried. _req has a
     timeout now (item 5) but no retry. A 5xx from Graph is not exceptional and
     one bounded retry would have finished the pass. Left for a deliberate
     change rather than an unattended run, but it is the reason today's Review
     numbers below cover 120 documents and not 200.

TRIAGE LOG — 2026-08-16 (unattended daily run). Corpus unchanged at 732 readable
invoices, TOTAL 717/732 (97%) before and after. All 15 shortfalls re-opened and
every one is byte-for-byte the SAME document named above — be_foods x2,
farmer_joes 4444676, gulli CI-437314, ilg x2, paramount price list, reward_dist
x2, vanguard x1, xero x5. No drift, no new failure mode among them.

 22. GULLI WAS THE LAST HARD-CODED PARSER AND THE DEFECT HAD ALREADY FIRED. Item
     18 flagged gulli.py as "the last parser in the tree still doing that ...
     has not drifted YET". It had. The mistake in that reading was the word
     "yet": Foodlink and FFT drifted ONCE, at a re-template, so the risk looked
     like a future event. Gulli's table is laid out to fit its CONTENT, so the
     columns move INVOICE TO INVOICE — across 33 corpus invoices the DESCRIPTION
     anchor ranges 122.8 -> 166.5 and QUANTITY 336.3 -> 394.5. The literal
     boundaries were 125 and 335, inside BOTH ranges. Which side of the split a
     word landed on was therefore decided per invoice by how wide that invoice's
     content happened to be, and it had gone wrong in both directions on real
     documents already in the corpus:

       narrow (DESCRIPTION 122.8, left of the 125 split) — the description's
         FIRST word falls in the code cell and is dropped:
           "Barbaro- Soppressata Hot (Zig Zag) r/w 2.5kg" -> "Soppressata Hot..."
       wide (QUANTITY 394.5, so the text runs past the 335 split) — the LAST
         words fall in the numeric cell and are dropped:
           "Sweet Baby Rays ... Barbeque Sauce" -> "... Barbeque"
           "Sapore- ... Polpa Fine Bib 10kg (4915)" -> "... 10kg"

     Fixed with the same header-derived boundaries as foodlink and fft
     (gulli.py::_cols_from_header); the literals survive as FALLBACK_* only.
     Measured margins the derivation rests on: a description's first word starts
     EXACTLY at the DESCRIPTION anchor (0.0 on all 309 line rows) with the
     nearest code token 91-135pt to its left, and the earliest quantity value
     sits at QUANTITY - 1.2. Boundaries are DESCRIPTION-2 and QUANTITY-8.
     3 of 309 line rows repaired, money unchanged, gulli 31/32 and TOTAL 717/732
     identical before and after, both audits still clean. Six fixture tests
     added, including one asserting the OLD constants eat both words, so the
     diagnosis is pinned the way Foodlink's and FFT's are.

     Note the footer "Total" token was checked too and NOT changed: it sits at a
     fixed x=345.4 on all 33 invoices, because the footer block is not
     content-laid-out. The 330..360 window is safe; only the line table moves.

     WHY THE PASS RATE COULD NOT MOVE, stated plainly because the daily task
     asks for a rate improvement: gulli's only non-PASS is CI-437314, the $0.00
     sample docket, which can never PASS by construction (see item 18 and the
     validator note below). A description is not part of reconciliation, so this
     entire class of defect is invisible to the PASS column by design — that is
     precisely how FFT scored 52/52 while corrupting 274 lines. The change was
     kept on the evidence of the 3 repaired rows plus zero regression anywhere,
     not on a rate.

 23. ONE TRUNCATED NAME IS LIVE IN THE COST BOOK and will not self-heal by the
     item 10 rule. data/cogs_list.csv carries
     "Gulli,SAUCEHICKORYBBQ-,Sweet Baby Rays Hickory & Brown Sugar Barbeque"
     (invoice CI-366411, 2026-03-26) — missing the trailing word "Sauce".
     _undo_dropped_prefix repairs a dropped LEADING word (it matches a fragment
     that is a word-boundary SUFFIX of a longer spelling); this is a dropped
     TRAILING word, which that rule does not see. The fullest-name consolidation
     should still prefer the complete spelling once any future Gulli invoice for
     that code parses under the fix, so it is expected to correct itself on the
     next delivery rather than needing an edit. Recorded so that if it is still
     truncated in a week, the reason to look is here. NOT edited by hand — the
     source PDFs sit behind the Supabase key this pipeline must never hold, and
     data/ facts are append-only. Same for "Sapore- ... 10kg", which lost only
     the trailing lot number "(4915)" and is cosmetic.

 24. STILL NOT DONE, and deliberately: gulli.py throws its UOM away. The UNIT
     column is read and discarded (raw_uom=None, cost_basis hard-set PER_UNIT),
     while the corpus shows real values there — "Unit", "Box", "kg". Capturing
     it would let the per-kg lines cost correctly instead of as units. NOT done
     in an unattended run because it moves cost_basis, and this run's own
     pull_mailbox output shows seven re-derives already HELD for exactly that
     reason ("basis per_kg -> per_unit while the price moves ... the row keeps
     its basis, so it would hold half of each reading"). A basis change wants
     Zak's eyes and a before/after feed diff, which is the item 13 standard.

TRIAGE LOG — 2026-08-16 (second pass). Zak: "we need to fix everything you've
flagged". Everything the morning entry parked is now done or answered, and two
of the answers are that the morning entry was WRONG about what needed doing.

 25. ITEM 24 WAS BUILT ON A FALSE PREMISE, and measuring it first is what caught
     that. It claimed capturing Gulli's UOM "would let the per-kg lines cost
     correctly instead of as units". There are no per-kg lines. Across the whole
     corpus — 309 line rows, 33 invoices — the UNIT column holds exactly four
     values: "Unit" 274, "Box" 33, blank 1, "kg" 1. Both real values are COUNTS,
     which is why hard-coded PER_UNIT was right on every promoted line for four
     months. The single "kg" row sits on CI-437314, the $0.00 sample docket that
     can never be promoted, so the weight path has never once fired on real data.

     Captured anyway, but for a different and honest reason: that one row proves
     the template CAN express a weight, and the old code would have taken such a
     line as PER_UNIT, reconciled it to the cent, and written a wrong $/ea into
     the cost book with nothing anywhere to show for it. Now a weight UOM yields
     PER_KG and an UNRECOGNISED UOM RAISES rather than being assumed — fail
     toward review. Proof it changes nothing today: all 33 corpus PDFs re-parsed
     and diffed line-by-line against the 276 lines stored in data/invoices —
     ZERO moved on basis, unit price or line total — and data/cogs_list.csv
     rebuilt byte-identical. Pack sizes were checked separately and were never
     at risk: all 20 Gulli picker items resolve their "x N" hint correctly
     (garlic bread 40, pizza boxes 50, inserts 100) from the description.

 26. pack_overrides.yaml DID NOTHING FOR ANY lightspeed:* ROW, which means every
     wrong-unit flag this review has ever raised against a cost-book row was
     unfixable by the one mechanism built to fix it. build_ingredients' cost-book
     branch hard-coded pack_qty "1" and never consulted `overrides`. That is the
     population that needs it most — the Back Office seed writes "can" for
     anything it cannot size — so the ~30 spirit "can" flags of 2026-08-15 and
     both of this morning's flags were unfixable: Zak could confirm a pack and
     the feed would ignore him. Now applied, with two guards.

     THE OBVIOUS FIX UNDERSTATED NINE PRODUCTS BY 40x-288x AND WAS CAUGHT BY THE
     FEED DIFF, NOT BY A TEST. Applying every override in that branch re-divided
     rows that were ALREADY per-piece: Garlic Bread $1.4953 -> $0.0374 (40x),
     Large Pizza Box 13" $0.6426 -> $0.0129 (50x), Flour Tortillas 6" $0.1167 ->
     $0.0004 (288x). costs.csv states why in its own pack column ("x40 (count)
     (via gulli:AGBGARBRE-B)", "chef-confirmed") — the upstream bridge had
     already divided the carton. Every one of those is the FLATTERING direction
     and not one would have tripped a bound. This is the item 13 lesson exactly,
     and the before/after feed rebuild is again the only thing that saw it.

     The distinction that resolves it: a cost-book row is priced per ONE
     PURCHASABLE UNIT. An override in ml/g says "one of those CONTAINS this
     much" — a real conversion, and the only route to costing by mass. An
     override in "ea" says "a CARTON holds N" — a fact about the carton, already
     applied upstream. So: apply measures, never counts, and never to a row that
     is already a rate. After both guards the diff is exactly the 3 intended
     rows and nothing else.

 27. THE A12 TIN'S WEIGHT WAS FOUND, NOT GUESSED. Item 24 and this morning's
     product triage both recorded that the weight is nowhere in our data — true,
     the Gulli invoice states no UOM. It is on the SUPPLIER'S CATALOGUE:
     shop.gullifood.com.au, product sappepstripa12-uc3, Documents ->
     "Specifications for Red Roasted Peppers Whole & Strips.pdf". The PDF is an
     image scan (31 characters of text layer across 2 pages) and had to be
     rendered and read; page 2, "ROASTED PEPPERS RED strips", states
     Packing Type "A 12 (5/1) TIN BOX", NET WEIGHT 4200 G, DRAINED WEIGHT 2500 G.
     The same page states "Outer Quantity CTN (3)", corroborating the "-UC3".
     DRAINED is the divisor: a recipe uses the peppers, not the brine, and it is
     also the conservative direction — costing on the 4200 g net would read
     1.68x cheap, and CLAUDE.md is explicit that errors flattering GP are the
     dangerous ones. $14.00 / 2500 g = $0.0056/g. Both spellings of the tin
     (gulli:SAPPEPSTRIPA12-UC3 and the bridged lightspeed:22874436) now agree on
     that rate, which is itself the fix for a duplicate-identity flag.

 28. SAN PELLEGRINO: pinned at 500 mL from OUR OWN invoices (ILG 450-1293,
     raw_uom "24x500ML", four invoices May-Jul 2026), not from the label. Worth
     noting where it landed, because it is not where I expected: build_costs
     applies the override FIRST, so costs.csv now holds a per-ml rate and the
     cost-book branch correctly declines to divide again. The row reads "1 ml @
     $0.004640" rather than "500 ml @ $0.004640" and both are correct — the test
     that first asserted pack_qty == 500 was pinned to an intermediate
     representation and failed the moment the better route won. It now asserts
     the RATE (x500 = $2.32) and that the unit is a measure, not "can". Also
     visible now: the live ILG invoices have superseded the January seed on the
     Stowaway id, $0.004640 -> $0.004712 as of 2026-08-04, which is the staleness
     this morning's flag predicted resolving itself.

 29. THE RE-DERIVE OSCILLATION IS REAL AND IS NOW FIXED. The 08-16 poller log
     shows one JFC ramen line moving 11.00 -> 11.0000 AND 11.0000 -> 11.00 inside
     a SINGLE run, with Foodlink's schnitzel doing the same at 56.00. DERIVED
     fields were compared as TEXT, so a re-formatting counted as a change and the
     re-derive wrote on it, churning cogs_list.csv on every poll. The cost is not
     the churn, it is the CAMOUFLAGE: every run printed several "the parser now
     reads them differently" lines that meant nothing, which is exactly the noise
     a genuine repricing has to be spotted in. Now compared as Decimals. The
     _cheaper hold is untouched and still refuses a drop.

 30. THE MAILBOX SWEEP NO LONGER DIES ON ONE FLAKY CALL — the defect item 21
     left "for a deliberate change", which then cost this morning's run ~124 of
     ~200 messages to a Broken pipe at message 76. Two layers: _req retries
     transient failures (429 honouring Retry-After, 5xx, timeouts, connection
     errors) up to 3 attempts, for GET and PATCH only — POST is excluded because
     the only POST creates a mail folder and a duplicate "Invoices Review" would
     split the backlog; and the sweep isolates each message, so an unhandled
     error names its subject, leaves it in place, and the run continues and still
     aggregates. A 401/404 is still fast-failed: retrying an expired token just
     buries the one error worth reading.

 31. THE REBASE CONFLICT HAD A SECOND WRITER, not a merge-strategy problem.
     data/system_health.json is authored by ops/publish_health.py, which builds
     it from a main-pinned clone and PUTs it to main via the Contents API, with
     its own change detection. pull_mailbox ALSO ran scripts/health_monitor.py
     every 30-minute cycle with cwd=ROOT, writing the file into the shared
     working tree and never committing it — so every `git pull --rebase
     --autostash` collided on it (twice during the 08-16 triage, once as a
     leftover unresolved UU). Both sides of every conflict were stale
     regenerations of a file authored elsewhere. The comment justifying the local
     write claimed it was "the monitor's clock"; it is not — health_monitor
     measures this poller by _log_age_min("invoice_poller.log"). Write removed;
     poller liveness unaffected.

 32. NOT DONE, and this is the right call: the trailing-word truncation from
     item 23 ("Sweet Baby Rays ... Barbeque", missing "Sauce"). It is NOT in the
     picker — its invoice is 2026-03-26, outside the 90-day window — and it will
     re-read correctly on the next Gulli delivery under the fixed boundaries. A
     general prefix-repair rule was considered and rejected: _undo_dropped_prefix
     is safe because it matches a SUFFIX, and dropped leading words are a layout
     artefact rather than a naming pattern. The mirror rule would merge products
     that legitimately share a prefix ("Chicken Thigh" / "Chicken Thigh Dice"),
     which is precisely how item 13 silently deleted two market bunches. One
     cosmetic name out of window is not worth that risk.

TRIAGE LOG — 2026-08-17 (unattended daily run). The parser change below was
measured at 732 readable invoices (TOTAL 717/732, 97% before AND after it). A
build_corpus run was still paging in the background and finished the day at 800
(foodlink 129 -> 138, select_fresh 116 -> 150, xero 82 -> 107); on that larger
sample TOTAL 783/800, still 97%. All 15 original shortfalls were re-opened by
hash and every one is the SAME document named in the 08-16 entry — be_foods
3b34ec060c06 + d02385290774, farmer_joes 4444676, gulli b381fb197ab6 (CI-437314),
ilg b46bfb0a542a + e23ce69fe899, paramount 670685f29215, reward_dist x2,
vanguard x1, xero 164542cc0a23 + 4. No drift, no new failure mode among them.

 33. FOODLINK WAS THROWING AWAY EVERY WRAPPED DESCRIPTION — 435 rows across 129
     invoices, two thirds of all line items, and the table read 129/129 (100%)
     the whole time. This is the third instance of the item-1/item-22 class (FFT,
     then Gulli, now Foodlink) and it was found from the OTHER end: not by this
     harness, but by STEP 3's product review, which asked why a brand-new
     ingredient had no pack size.

     A Foodlink description wraps onto its own row, and that row carries no qty
     and no total — so `if qty is None or total is None: continue` dropped it.
     The parser never joined continuation cells at all; the docstring pattern
     every other parser in the tree follows was simply missing here.

     WHY IT IS MONEY AND NOT COSMETICS. Foodlink's UOM column only ever says
     "EA" or "CTN", so the PACK SIZE is stated ONLY in the description, and the
     size is usually what wrapped:
         "GRAVY MIX RICH BROWN G/FREE " + "7KG Executive Chef"
     Today's invoice SI4527225 promoted code 101239 as "GRAVY MIX RICH BROWN",
     pack_qty null, needs_pack_review TRUE, $57.61 per "ea". The SAME code on
     SI4483241 (2026-07-24), where the words happened not to wrap, reads
     "... G/FREE 7KG Executive Chef" and costs $8.23/kg. A recipe costing gravy
     off the wrapped spelling books ~7x. The same rows recovered 1KG, 2LT, 5KG,
     20KG, 500GM, 3.8L, 2.35KG, A10, 55X200GM and 15KG on other products.

     It also splits identity, the item-10 failure: code 100710 carried both
     "CHOCOLATE DARK 1KG" and "CHOCOLATE DARK 1KG Natures Secret" depending on
     where that invoice's line happened to break. _undo_dropped_prefix cannot
     repair these — it matches a dropped LEADING word by suffix, and this is a
     dropped TRAILING word (the item 23/32 asymmetry).

     THE FIX, and the two things that make it safe. A continuation is identified
     by its INDENT, derived per invoice from the first line item's own
     description x (foodlink.py::_desc_x), never hard-coded — the corpus holds
     TWO description left edges, 70.9 and 73.6, so a literal would have been the
     2026-08-15 time-bomb all over again. Three conditions, all necessary:
       * the desc bucket is the ONLY one with text. Foodlink's delivery note
         ("**Enter via Moore Lane, up wheelchair ramp ...") spills across every
         column and the repeated page header fills them too, so both are
         excluded structurally rather than by keyword.
       * the row starts within 2.0pt of that indent. THE DANGEROUS NEIGHBOUR is
         Foodlink's own footer boilerplate — "no." and "MSC Certification code:
         MSC-C-52372" are also desc-only rows, on all 129 invoices, and they sit
         at x=90.6, ~17pt clear of either indent. Measured across the whole
         corpus: 435 rows match the indent, 268 do not, and 266 of those 268 are
         exactly those two boilerplate lines (133 invoices x 2).
       * an item exists to attach to. The remaining 2 of 268 are real
         continuations orphaned ABOVE the first line item by a page break; they
         are skipped rather than attached to the wrong product, and nothing is
         lost because the parent's own tail repeats after its money row.

     MEASURED, to the item-13/item-25 standard rather than on a rate. Every
     Foodlink corpus PDF was re-parsed and diffed line-by-line against the 425
     matching lines already in data/invoices: 254 descriptions lengthened, ZERO
     rewritten (every change is a strict prefix-extension of the stored name),
     and ZERO money or cost_basis movements attributable to the change — the
     same diff run against the UNMODIFIED parser produces an identical 2-row
     difference (101239 unit price stored 57.6087 vs recomputed 57.6100 on
     SI4310704 and SI4334071), so that pre-dates this work and is a stored-vs-
     recompute rounding artefact, not a regression. foodlink 129/129 and TOTAL
     717/732 identical before and after; no other supplier moved; both audits
     still clean. Five fixture tests added, built from REAL corpus coordinates
     for both layouts (a hand-invented row does not even reach the qty column
     under the new header — the item-19 fixture lesson), including one that pins
     the diagnosis by asserting the tail row has no qty and no total.

     AND THE RATE COULD NOT MOVE, stated plainly because the daily task asks for
     one: foodlink was ALREADY 129/129. A description plays no part in
     reconciliation, so this entire defect class is invisible to the PASS column
     by construction — precisely how FFT scored 52/52 while corrupting 274 lines
     (item 1) and Gulli 31/32 while eating words in both directions (item 22).
     Refusing a fix because the rate cannot rise would mean never repairing a
     description at any supplier already at 100%. Kept on the evidence above.

 34. THE REVIEW FOLDER IS HALF NON-INVOICES, AND THAT IS WHY IT ONLY GROWS. Of
     the 200 messages the retry sweep read today, 102 (51%) were classified
     "statement / not an invoice" — correctly — but a refused document is left
     where it is, so it is re-fetched and re-scanned tomorrow and forever. They
     are permanent residents consuming a RETRY_BATCH=200 budget, which means the
     sweep hit its cap on residents and real stuck invoices below them were never
     reached. The dashboard queue has risen 283 -> 326 since 2026-08-04 (~+3/day)
     and this is the mechanism. The obvious fix — move a classified non-invoice
     to a "Not Invoices" folder instead of leaving it — is a MAILBOX WRITE on 102
     of Zak's messages, which is not an unattended run's call. FOR ZAK.

     Today's sweep, for the record: 200 read, 3 promoted (all Foodlink, recovered
     by the 08-15 fix), 102 statements, 4 image-only (manual entry), 89 still
     stuck. The 89 by sender: 27 are stowawaybar.com — invoices FORWARDED by Zak
     and Bryony, where the domain->parser registry can only ever see our own
     domain. That is the single largest bucket in the pile and no supplier parser
     can reach it; it is the same shape as the Xero problem of item 16/17 and
     wants the same kind of answer (identify the vendor from the page, not the
     sender). Recorded, not attempted.

 35. A NEW DOCUMENT CLASS SURFACED WHEN THE CORPUS GREW, and it is NOT a
     regression: foodlink 68e7027fd460 is a CREDIT MEMO (SC338338, 2026-05-21,
     one line, CORN FLOUR MAIZE GLUTEN FREE 5KG Edlyn, $23.00, Reason Code
     MISSING). It fails identically with AND without today's change — checked
     both ways explicitly, because a new parse-fail appearing in the same run as
     a parser edit is exactly the thing to attribute before believing it.
     Its header is a different shape from the tax-invoice template: "Qty UOM" is
     one token and there is no "Qty." and no "Disc", so the header search does
     not fire at all. It is a real credit we are not capturing, and there will be
     more like it. Worth a day: either widen the header match to the credit-memo
     shape, or classify it explicitly rather than letting it read as a failure.
     Deliberately not attempted today — one supplier change per run, and this one
     wants its own before/after on the money sign (a credit is negative, and
     getting that wrong is the flattering direction).

 36. THE FORWARDED INVOICES ARE CLOSED AS A QUESTION — Zak, 2026-08-17: "the
     forwarded invoices won't be supplier items that feed food and bev cogs so
     ignore it." DO NOT propose a vendor-from-the-page parser for them. Item 34
     called the 27 stowawaybar.com forwards "the single largest bucket in the
     pile" and pointed at the item 16/17 Xero answer; that was a correct reading
     of the COUNT and the wrong reading of the VALUE, because this table cannot
     see what a document is FOR.

     The evidence, from the 2026-08-17 sweep, so the ruling is checkable rather
     than just asserted — the 27 are: Freshwater Locksmiths x2, Hybrid Signs,
     Sydney Upholstery, Applause Entertainment, Uber Direct billing, Frymate
     (fryer equipment), Trivialicious trivia packs, JAMAC cleaning supply x2,
     SEEK, barware receipts x2, Render (hosting), Singa (karaoke), Amazon x2,
     printing/flyers x2, and assorted receipts. Services, equipment, marketing
     and software. None of it reaches a recipe, which is what this pipeline
     exists to feed. They are still real payables and are NOT filed away like
     the statements and credit notes of item 34 — a service bill still has to be
     paid, so it stays in Review where a human sees it. Consequence, stated
     plainly: those 27 keep occupying the retry sweep's budget. That is now a
     deliberate cost, not an unnoticed one.

     ONE EXCEPTION IS WORTH A HUMAN'S EYE, and it is named here rather than
     quietly folded into the ruling: "Fwd: Invoice - Barrel One Coffee Roasters
     #OMI7874". Coffee IS beverage COGS. It is the only one of the 27 whose
     product could feed a recipe, so if Barrel One becomes a regular supplier it
     wants its own domain entry and parser like any other — the ruling above is
     about forwarded ADMIN, not about a coffee roaster that happens to have been
     forwarded once. Not acted on: one invoice is not a supplier relationship,
     and Zak's instruction was to ignore the bucket.

TRIAGE LOG — 2026-08-18 (unattended daily run). Corpus 820 PDFs / 783 readable.
All 11 corpus shortfalls were re-opened BY HASH and named; ten are the same
documents this log already carries, and the eleventh turned out to be a real,
recurring, kitchen-food defect that four previous entries had walked past
because it was recorded as "an unrelated template" rather than opened.

 37. CANTON GROUP HAD NEVER PARSED, AND IT IS KITCHEN FOOD. The 2026-08-15
     (item 19) entry closed out xero's five shortfalls as "3 SYMSAFE payment
     receipts, 1 credit note, and 164542cc0a23, a 'Tax Invoice / Bill to /
     Attn: OLIVER' template unrelated to the others". Two of those three claims
     have now moved: the credit note is caught by looks_like_credit_note and no
     longer scores, there are FOUR receipts rather than three as the corpus
     grew, and 164542cc0a23 is not an unrelated template — it is CANTON GROUP
     (INV-5096, Davidson Plum BBQ pork buns and Peking duck spring rolls for
     Harry Gatos). Canton Group was registered in ABN_SUPPLIER on the day the
     Xero parser was written and is listed there under "# kitchen food". Not one
     of its invoices has ever reached data/invoices.

     THE DEFECT IS ONE LINE AND IT IS NOT A LAYOUT PROBLEM. Canton's header is
     an ordinary full header, _cols_from_header resolves it correctly, every
     word buckets where it should, and the money reconciles to the cent
     (120.00 + 200.00 = the stated 320.00). The parser read the QUANTITY column
     with _m, the MONEY reader, which requires exactly two decimal places. Every
     other vendor in this corpus happens to type "1.00" into Xero. Canton types
     "3". _m("3") is None, the `qty is None` guard skipped every line, and
     parse() raised "no line items parsed" on the whole document.

     Fixed with a separate _q() for the quantity column; _m keeps its strictness
     for money, which is load-bearing — it is what keeps "0%", "10%" and stray
     date fragments out of the AMOUNT column. Loosening the quantity is safe in
     a way that loosening _m would not be: the bucket is bounded on both sides
     by the header's own Quantity anchor, a line still needs a strict two-decimal
     AMOUNT to be emitted at all, and the totals block still ends the table.

     A SECOND, QUIETER MISS ON THE SAME DOCUMENT. Canton labels the issue date
     "Issue date", not "Invoice Date", so even once the lines parsed the invoice
     came out with invoice_date=None — a cost with no date to order its price
     history by. Added as a second LABEL, deliberately not as "the first date on
     the page": Canton prints "Due date 20 Aug 2026" IMMEDIATELY ABOVE
     "Issue date 13 Aug 2026", so position would have booked the cost in the
     wrong week. Extracted as xero.invoice_date() so it is testable without a
     PDF, and pinned including the both-labels-present ordering.

     MEASURED to the item-13/25/33 standard rather than on a rate: all 114 xero
     corpus PDFs parsed with and without the change and diffed field by field —
     1 newly parsing, 0 lost, 0 MOVED (not one description, quantity, unit
     price, line total, cost basis, tax treatment or supplier code changed on
     any invoice that already parsed). xero 108/113 (95%) -> 109/113 (96%),
     TOTAL 772/783 -> 773/783; no other supplier moved; both audits still clean.
     500 tests pass. Five fixture tests added from the REAL coordinates of
     164542cc0a23 (a hand-invented row does not reach the right buckets under a
     derived header — the item-19 lesson), including one asserting _m STILL
     refuses "3", so the diagnosis is pinned rather than just the fix.

     WHY IT SURVIVED FOUR PASSES, worth recording because it is the same shape
     as item 19(a): the shortfall was described from its masthead ("Tax Invoice /
     Bill to / Attn: OLIVER") instead of from its CONTENT, the description was
     plausible, and every later pass re-used it. The document was never opened.
     A one-line note saying which SUPPLIER a failure belongs to would have made
     this obvious immediately — it is registered kitchen food sitting at 0%.

     Live confirmation, same day: Canton Group INV-5110 arrived in the inbox
     during this run and failed for exactly this reason before the fix. It and
     INV-5096 should both promote on the next Review sweep.

 38. THE OTHER TEN CORPUS SHORTFALLS ARE UNCHANGED, re-opened by hash: be_foods
     3b34ec060c06 + d02385290774, farmer_joes 4444676 (now out of the corpus
     window), gulli b381fb197ab6 (CI-437314), ilg b46bfb0a542a + e23ce69fe899,
     paramount 670685f29215 (the price list), and FOUR xero SYMSAFE payment
     RECEIPTS (3d3c2698cce5, 98cf4cb45aa6, c7e2d27409b6, e702e84538d3 — item 19
     said three; the fourth arrived with the corpus growth, which is the shape
     item 20 predicted). No drift and no new failure mode among them. NO change
     was made to any of them. reward_dist and vanguard have aged out of the
     4-month window entirely and no longer appear.

 39. OPERATIONAL, and it cost this run about 40 minutes: a pull_mailbox.py from
     the 30-minute poller was found WEDGED — 6h07m elapsed, 1.04s of CPU, its
     log last written at 14:47 — and had to be killed before this run could
     work. Item 5 added GRAPH_TIMEOUT=60s and item 30 added retries, so this is
     a hang the existing guards did not catch; the timeout is per-REQUEST and
     something is blocking outside a request. Separately, this run's first inbox
     pass died on "Graph 401 ... token is expired" halfway through, having spent
     several minutes in 60s socket timeouts first — i.e. the ACCESS TOKEN
     EXPIRED MID-RUN and _req's retry ladder (correctly) fast-fails a 401 rather
     than refreshing it. Re-running the pass from a clean start fixed it and
     both messages were picked up. FOR ZAK: a token refresh on 401-once, and a
     whole-run watchdog, are the two things that would have made this run
     unattended. NOT attempted here — it is auth-path code and this task must
     never handle credentials.

TRIAGE LOG — 2026-08-19 (unattended daily run). Corpus 783 -> 807 readable. The
ten pre-existing shortfalls were re-opened BY HASH and every one is the SAME
document already named here: be_foods 3b34ec060c06 + d02385290774, gulli
b381fb197ab6, ilg b46bfb0a542a + e23ce69fe899, paramount 670685f29215, and four
xero SYMSAFE payment receipts. No drift and no new failure mode among them, and
NO change was made to any of them. Worth recording because it nearly sent this
run down a blind alley: the Review sweep reported "no reconciling parser for
befoods.com.au" on invoice 6848920 and a B&E parse-fail on a kitchen supplier
looks exactly like the Foodlink/FFT/Gulli rot. It was opened rather than
assumed — 6848920 IS corpus hash 3b34ec060c06, the $0.00 credit docket this log
has carried since 2026-08-08. The invoice NUMBER was new to the log; the
document was not.

 40. THE HARNESS CANNOT SCORE A SUPPLIER IT HAS NEVER HEARD OF, and that — not
     any parser defect — is where the whole remaining opportunity was. The
     corpus said 98% and had said 98% for four days. Meanwhile the Review sweep
     read 200 messages and left 143 stuck, and the stuck pile is not a long tail:
     it is a handful of senders with NO DOMAIN_KEY entry, so build_corpus never
     collects them, parser_regression never scores them, and they are invisible
     to the number this task is asked to drive up. Items 4 and 12 are the same
     lesson twice (a corpus that only sampled survivors; suppliers missing from
     SUPPLIER_ALIAS); this is the third face of it. The stuck-by-sender count,
     recorded so the next run can start from evidence rather than re-derive it:

       38  stowawaybar.com        forwarded admin — CLOSED by Zak, see item 36
       14  sent-via.netsuite.com  12 Bacchus + 2 Dext   <- taken today
       11  nelsonwineco.com.au    liquor
        8  post.dearsystems.com   Viticult (platform sender, like Xero)
        8  apps.myob.com          VMA + Cork And Co + AQUARIUS FISHERIES (kitchen)
        6  mountainculture.com.au / 6 combinedwines / 5 youngandrashleigh /
        5  vinsight.net           liquor
        3  denifoods.com.au       KITCHEN FOOD, no parser
        2  inalcafb.com.au        KITCHEN FOOD, no parser

     apps.myob.com and post.dearsystems.com are the next two worth a day, and
     both are PLATFORM senders that take the item 16/17 ABN answer this parser
     just reused. MYOB carries Aquarius Fisheries, which is kitchen food and is
     already in KITCHEN_SUPPLIERS from item 12, so it feeds recipes.

 41. BACCHUS NOW PARSES: netsuite 0/24 -> 20/24 (83%), TOTAL 773/783 -> 793/807.
     All 21 readable Bacchus invoices parse AND reconcile TO THE CENT; the 4
     "failures" are Dext subscription bills that share the sender and are
     correctly refused (see below). No other supplier moved by a single
     document, and both audits stayed clean. Bacchus is a live wine supplier we
     order against weekly with an MOQ, and NOT ONE of its invoices had ever
     reached data/invoices by parser — the only two there were LLM-extracted
     back when this task still spent credit.

     The template, and the three things that were not what they looked like:

     (a) THE MONEY COLUMN IS THE LAST ONE, not "Amount". This is wine: Amount is
         ex-tax, WET (29%) is added, then GST on ex+WET, giving Gross Amt. The
         validator's load-bearing check is sum(line_total_incl) == total_incl,
         so lines carry GROSS. Verified to the cent on 3379f8e9af9e:
         136.00 -> WET 39.44 -> GST 17.54 -> Gross 192.98. Freight is NOT a line
         — it sits in the totals block, on some invoices only — so it is emitted
         as an EXTRA at freight x 1.1, which that invoice's own GST Total
         corroborates (51.36 printed, 46.86 from the lines, difference 4.50 =
         10% of 45.00).

     (b) THE DISC COLUMN IS LEFT-ALIGNED AND IT STOLE A WORD. Every money column
         on this template is right-aligned, so the first cut gave all of them a
         20pt margin. Disc is not money — its values are "15%", "07.5%", "List",
         "Custom", all starting exactly at the Disc label's own x — and that
         20pt margin reached back into the description and ate its last word:
         the vintage off "Trentham River Retreat Pinot Grigio 2025" and the "on"
         off "Putting on". A stolen word that still reconciles to the cent is
         the item 1 / item 22 / item 33 defect class exactly, and it was
         reproduced from scratch in a brand-new parser on day one. Caught only
         by reading the parsed output against the PDF rather than trusting the
         PASS column, which by construction cannot see it.

     (c) THE ITEM CODE WRAPS, MID-WORD. On 55aaa9803359 the code cell reads
         "PETDETMEDRO" on the row ABOVE the money row, nothing on the money row
         itself, and "SE 24" on the row BELOW — one code, PETDETMEDROSE24, split
         across three rows. The first cut collected only the DESCRIPTION off
         continuation rows, so that line came out with supplier_code=None, and
         core/domain.py::purchasable_id RAISES on a code-less line: the invoice
         would have reconciled perfectly and fed the cost book nothing. Three
         invoices carried it.

     WHY word_rows_with_y HAD TO EXIST. NetSuite renders a multi-line cell
     VERTICALLY CENTRED on its money row, so a three-line description puts one
     line above the money row and one below it. Row INDEX cannot separate that:
     on 3379f8e9af9e the row above and the row below are each exactly one row
     from two DIFFERENT money rows, so "nearest row, ties go to X" is wrong for
     one of them whichever way X is set. The vertical gap separates them with a
     wide margin — measured across the corpus, a continuation sits 4.4-8.8pt
     from its own money row and two separate items 14.9-17.6pt apart, with
     nothing in between. pdf_text.word_rows_with_y is ADDITIVE: word_rows() is
     untouched and every existing parser keeps its exact behaviour.

     THE VENUE IS READ FROM THE BILL TO BLOCK ONLY, and this is the one to keep
     if anything here is reused. Every other parser in this tree matches venue
     keywords against the WHOLE PAGE. On Bacchus that is wrong in both
     directions, because all three venues bill to the same legal entity
     ("Stowaway Freshwater Pty Ltd") and it is the trading name above it that
     decides: THREE invoices are genuinely billed to Harry Gatos (INV493375,
     INV492625, INV494398), and TWO that are billed to STOWAWAY say "Harry
     Gatos" elsewhere on the page inside a free-text note ("TRE1RRPG is for
     Harry Gatos. Putting on same invoice to keep urgent fees to a minimum").
     A flat page match books those two to the wrong venue, and venue picks the
     product NAMESPACE — the two venues have different Lightspeed ProductIDs —
     so it would write a cost against a product that was not bought.

     DEXT SHARES THE SENDER AND IS REFUSED, on purpose and twice over: its
     invoices carry no ABN but our own, so vendor_from_abn returns None, and
     their template has no header row this parser recognises. They stay in
     Review rather than being forced into Bacchus's buckets. That is what the
     4/24 shortfall is, and it should not be "fixed".

     Six fixture tests added from the REAL coordinates of 3379f8e9af9e and
     55aaa9803359 (item 19's lesson: an invented row does not even reach the
     right buckets under a derived header), including ones that pin the
     diagnoses — that the money row's code cell is EMPTY on the wrapped-code
     invoice, and that SAME_CELL_PT sits strictly between the two measured gap
     ranges. All parser tests pass.

 42. IDENTITY IS ALREADY SPLIT THREE WAYS FOR BACCHUS, LIVE IN THE COST BOOK,
     and a parser cannot repair it. data/cogs_list.csv holds ONE wine under
     THREE codes — "PETDETROSE 24" (with a space), "PETDETROSE24" and
     "PETDETROSE" — from three separate LLM extractions, so Petit Detour Rose's
     price history is in three pieces. Same for FD2MOTHER 23 / FD2MOTHER23.
     This parser emits the JOINED form: it is the only one of the three that
     passes this harness's own no-whitespace identity audit, it keeps the
     vintage so a 2023 and a 2024 are not merged at different prices, and it
     matches the most recent extraction so the series continues rather than
     forking a fourth time. Consolidating the three historical spellings is a
     build_cogs_list DERIVED question (the item 12 mechanism) and it moves money
     between price series, so it is FOR ZAK, not for an unattended run.

 43. CI WAS ALREADY RED WHEN THIS RUN STARTED, and not because of anything here.
     modules/recipes/tests/test_sold_as_bought_rescue.py::test_peroni_costs_
     what_ilg_invoiced asserts Peroni's latest invoiced cost is EXACTLY $2.5217.
     ILG invoice 03748652 (2026-08-18), ingested by the 6am pipeline hours
     before this session touched a file, moved it to $2.4233 — a real price
     DROP, correctly captured. The test hard-codes a live supplier price, so it
     fails CI and blocks the deploy every time that supplier repricess. Its own
     preceding assertion ("Peroni is still priced from Back Office — the bridge
     is not landing") is the check that carries the meaning; the exact figure
     was incidental to the day it was written. NOT changed here: relaxing an
     assertion changes what a test means, and doing that unattended is how a
     guard quietly stops guarding. FOR ZAK.

TRIAGE LOG — 2026-08-20 (unattended daily run). Corpus 807 -> 836 readable, and
29 of that growth is a supplier this table had never heard of. Measured
BEFORE/AFTER ON THE SAME CORPUS from a /tmp clone pinned at 196fba4f, which is
the only way the comparison means anything: myob 0/12 (0%) -> 11/12 (91%),
TOTAL 810/836 (96%) -> 821/836 (98%). NOT ONE other supplier moved by a single
document, and both audits stayed clean.

 44. AQUARIUS FISHERIES HAD NEVER PARSED, AND IT IS KITCHEN FOOD. Item 40 named
     apps.myob.com as one of the two next worth a day ("MYOB carries Aquarius
     Fisheries, which is kitchen food and is already in KITCHEN_SUPPLIERS from
     item 12, so it feeds recipes"). Taken today. The scale of the miss is on
     Aquarius's own STATEMENT, which is in the corpus: it lists TEN invoices
     between 2026-01-08 and 2026-04-30 — roughly fortnightly — while
     data/invoices held exactly TWO, both LLM-extracted back when this task
     still spent credit. A seafood supplier's costs were essentially not
     reaching the cost book.

     apps.myob.com is a PLATFORM sender like post.xero.com, ordermentum.com and
     sent-via.netsuite.com, so it takes the same answer: the vendor is the ABN
     that is not ours, from an explicit registry, and an unregistered vendor is
     refused rather than guessed. CUSTOMER_ABNS, ABN_SUPPLIER and
     SERVICE_SUPPLIERS are imported from parsers/xero.py rather than copied —
     our own two ABNs are one fact and a second copy is a second thing to forget
     (item 19(a) is what forgetting costs).

     TWO TEMPLATES ARRIVE ON THIS DOMAIN and they are told apart by their
     HEADER, not by the vendor, because a vendor can re-template (item 15):
       AQUARIUS  Quantity | Item Code | Description | Unit Price (ex-GST) |
                 CARTON COUNT | Total (ex-GST), on MYOB's customised print form
                 with a tear-off remittance slip.
       MODERN    Item ID | Description | Qty | Unit price | Tax | Amount ($) —
                 MYOB's current hosted invoice, which VMA Ventilation uses.

     FOUR THINGS WERE NOT WHAT THEY LOOKED LIKE, each caught by reading the
     parsed output against the PDF rather than by the PASS column:

     (a) THE DESCRIPTION CELL IS CENTRED, NOT LEFT-ALIGNED. On 318421f41e14 the
         label "Description" starts at x=224.5 and its value starts at 184.6 —
         40pt to the LEFT of its own label, and only 34pt right of the end of
         the Item Code column. A boundary at the label puts "White Prawn" in the
         code cell; the obvious alternative, the midpoint of the label gap
         (187.7), still eats "White". Either one reconciles to the cent and
         would read 100% here forever. That is the item 1 / 22 / 33 defect class
         reproduced in a brand-new parser on day one, exactly as item 41(b)
         warned. The boundary used is the midpoint between the ITEM and
         DESCRIPTION anchors (165.3), clear of the code value's end (131.1) and
         of the description's start (184.6). Pinned by a test that asserts both
         naive boundaries DO eat the word.

     (b) THE TAX ROW SITS ABOVE THE TOTAL ON ONE VARIANT. VMA #4403 is an
         INCLUDING-tax invoice: no Subtotal at all, "Tax $4.55" printed above
         "Total Amount (inc. tax) $50.00". A totals scan that starts at the
         first subtotal/total/balance row skips the tax entirely and the invoice
         comes out GST-FREE when it is not. But the mirror mistake is worse and
         is why the scan is bounded at all: the modern template prints "GST" in
         the Tax column of EVERY LINE ROW, so a whole-page keyword scan reads a
         $50.00 line AMOUNT as $50.00 of GST. Resolved structurally rather than
         by keyword — a LINE is a row that fills both the quantity and the money
         column, a TOTALS row is anything below the header that does not.

     (c) THE FLAT TEXT LIES ABOUT BOTH THE REF AND THE DATE. pdftotext's reading
         order on the Aquarius form runs "... Tax Invoice / 7/05/2026 / 185244",
         so a regex for "Invoice <number>" returns the DATE as the invoice
         reference — it did, on the first cut. And that form prints its DUE date
         top-left ABOVE the letterhead (30/06/2026), while the modern template
         repeats "Due date:" in its footer, so "the first Date: on the page" is
         a due date. Both are now read BY COORDINATE: the label's own row to the
         right, or the row below within 8pt of the label's x-span, which is what
         keeps "Invoice number | Issue date | Due date" apart. Verified against
         the statement, which lists 185222 on 30/04/2026 and 185244 after it —
         both now agree.

     (d) THE ABN IS DOTTED. "A.B.N. 46 003 857 618", which xero._ABN
         (r"ABN[:\s]*...") does not match at all. The shared pattern was
         deliberately NOT widened: vendor_from_abn refuses whenever more than
         one non-customer ABN is present, so making that regex match MORE
         strings can only ADD candidates, and adding a candidate to an invoice
         that today names exactly one vendor turns a PASS into a refusal. Xero,
         Ordermentum and NetSuite are 140+ passing corpus documents between
         them. myob.py carries its own dotted-tolerant pattern over the same
         CUSTOMER_ABNS and the same refuse-unless-exactly-one rule. The A.C.N.
         printed directly above (9 digits) cannot match an 11-digit pattern, and
         a test pins that too.

     VMA is in SERVICE_SUPPLIERS: it sells kitchen hood filter exchanges, not
     goods, so its lines are EXTRA. That is not cosmetic — its "Item ID" column
     holds a LINE NUMBER ("1"), and emitting that as a supplier_code would mint
     the identity "vma:1" and merge every service ever billed under it.

     supplier_key "aquarius" matches the two invoices already in data/invoices
     so the price series continues rather than forking (items 12/42). Both
     "aquarius" -> "Aquarius" in SUPPLIER_ALIAS and "Aquarius" in
     KITCHEN_SUPPLIERS were already present from item 12 and needed no change —
     checked rather than assumed. Ten fixture tests added from the REAL
     coordinates of 318421f41e14 and be56bbcd354b.

 45. CORK AND CO IS THE ONE MYOB SHORTFALL AND IT IS NOT A DEFECT — it is next.
     91513b04a9dd (INV 0086598, 10/08/2026, $266.42) is a WINE invoice on a
     third template: "QTY | ITEM NO. | DESCRIPTION | PRICE | UNIT | DISC% |
     EXTENDED", with WINE EQUALISATION TAX $52.40, GST (WINE) $23.31, GST
     (GENERAL) $0.91, a 7.5% line discount and a FREIGHT - SYD line. Its ABN
     (63 604 036 035, printed on its own footer) is deliberately NOT registered
     today: the header is unrecognised so it is refused either way, and the WET
     arithmetic wants its own before/after on the money the way Bacchus's did
     (item 41(a)). Liquor, not kitchen food, so it does not feed recipes. One
     supplier change per run.

 46. THE REVIEW SWEEP'S REMAINING BUCKETS, unchanged in shape from item 40 and
     recorded so tomorrow starts from evidence: nelsonwineco.com.au,
     youngandrashleigh.com, vinsight.net, post.dearsystems.com (Viticult),
     mountainculture.com.au, combinedwines — all liquor — plus denifoods.com.au
     and inalcafb.com.au, which are KITCHEN FOOD with no parser and no
     DOMAIN_KEY entry, so they are still invisible to this table. Those two are
     small (3 and 2 documents) but they are the only remaining kitchen-food
     senders in the pile. post.dearsystems.com is another PLATFORM sender and
     takes the same ABN answer this parser just reused for the third time.

 47. THE REVIEW FOLDER HOLDS 472 MESSAGES AND THE RETRY SWEEP READS 200, so 272
     of them are structurally unreachable — item 34 measured, with a number.
     This was found the hard way: after the parser shipped, a Review sweep
     promoted six VMA invoices and NOT ONE Aquarius invoice, which made no sense
     until the folder was counted. Graph's own search finds Aquarius invoices
     185277, 185306 and 185327 sitting in Review dated 15/22/28 May — older than
     the newest 200, so the sweep has never once looked at them. This is the
     item-4 / item-12 / item-40 lesson in its fourth form: the thing that is
     wrong is not the parser, it is the SAMPLE the machinery can see.

     Worked around today rather than fixed — a second sweep with
     `--oldest-first --max 300`, which is free (--no-llm) and reaches the tail.
     NOT changed in code: RETRY_BATCH is a shared cost/time control and raising
     it is Zak's call, especially while ~half the folder is permanent residents
     (item 34's statements and item 36's forwarded admin). FOR ZAK — the durable
     fix is the one item 34 named: move a classified non-invoice out of Review,
     which is a mailbox WRITE on Zak's messages and not an unattended run's call.
     Note the "Not Bills" folder now exists and this run's sweeps moved 22 + 22
     documents into it, so that fix is partly in place already; the residue is
     the forwarded-admin bucket, which is deliberately kept in Review because a
     service bill still has to be paid.

 48. OPERATIONAL: the run started with an UNRESOLVED REBASE left in the shared
     tree by the poller — pull_mailbox's own commit hit a conflict in
     dashboard/pricing/compare.json and stopped mid-rebase, so the working tree
     was mid-`interactive rebase in progress` before this session touched
     anything. Resolved by taking the incoming generation of that file (it is a
     DERIVED dashboard feed, regenerated on every poll, so neither side is a
     fact) and continuing; the autostash re-applied cleanly and the commit is on
     main as 196fba4f. This is the item-31 shape again but on a different
     derived file: compare.json is written by pull_mailbox in the shared tree
     and never treated as generated, so every concurrent pull collides on it.
     FOR ZAK — the same fix as item 31 would apply (author it where it is
     published, or gitignore it and rebuild on deploy).

 49. CI IS RED AGAIN ON A TEST THAT PINS LIVE DATA, and it is NOT this change.
     tests/test_recipe_costing.py::test_a_recipe_that_is_one_of_itself_is_flagged
     asserts `len(taut) == 4` — "what remains is the four wine glasses, which
     still say 'one of me'". There are now ZERO tautological flags in
     data/cost_book_flags.json, so somebody fixed the wine glasses and the test
     that pinned the defect expired, exactly as its own comment says happened
     twice before ("a test that pins a defect expires the day someone fixes
     it"). Verified it is not this work: the same test PASSES on a /tmp clone
     pinned at 196fba4f and fails only against the regenerated feed. NOT changed
     — inverting or relaxing an assertion changes what a test MEANS, and doing
     that unattended is how a guard quietly stops guarding (the item-43 ruling,
     verbatim). FOR ZAK. The same file's Peroni assertion from item 43 is a
     second instance of the same design problem: two tests in this suite fail
     whenever reality improves.

TRIAGE LOG — 2026-08-21 (unattended daily run). Corpus 846 -> 864 readable. The
15 pre-existing shortfalls were re-opened BY HASH before anything else was
touched and every one is the SAME document this log already carries: be_foods
3b34ec060c06 + d02385290774, gulli b381fb197ab6, ilg b46bfb0a542a +
e23ce69fe899, myob 91513b04a9dd (Cork And Co, item 45), paramount 670685f29215,
netsuite x4 (the Dext subscription bills of item 41, correct refusals) and xero
x4 (the SYMSAFE payment receipts of item 38). No drift, no new failure mode,
and NO change was made to any of them.

 50. DENI FOODS HAD NEVER PARSED AND IT IS THE ARANCINI ON THE MENU. Item 46
     named denifoods.com.au and inalcafb.com.au as "the only remaining
     kitchen-food senders in the pile". Both are now in DOMAIN_KEY and both were
     OPENED rather than described — which is the whole point, because one of
     those two labels was wrong:

       * DENI FOODS is kitchen food, and it is a live menu item. Every invoice
         carries one product: GASTAM4NA, "SMOKED MOZZARELLA & BASIL ARANICNI
         65G (7.8KG)", 15.6 kg at $19.89/kg ex on a roughly weekly cycle. ZERO
         Deni invoices had ever reached data/invoices, so that arancini's cost
         has never been in the cost book. (The only "arancini" in cogs_list.csv
         is foodlink 103742, the TRUFFLED one — a different product.)
       * INALCA IS NOT KITCHEN FOOD. Item 46 called it so from the company name
         ("Inalca Food & Beverage Australia"). All six readable corpus invoices
         are ITALIAN WINE billed under the "IWI - ITALIAN WINE IMPORTERS"
         masthead — Casa Gheller Prosecco, Sibiliana Nero D'Avola — with WET on
         the totals block, and two of the six are $4.40 fuel-levy-only dockets.
         It is liquor, it does not feed recipes, and it is NOT the next day's
         work on a kitchen-first rule. Recorded rather than acted on. This is
         item 19(a) / item 37 for the third time: a document class named from
         its masthead, believed for days, and wrong the moment it was read.

     THE PARSER, and the three things that were not what they looked like:

     (a) THE HEADER SAYS "Unit" TWICE — once as the UOM column at x=59.2 and
         once as the first word of "Unit Price" at x=427.2. Resolving it by
         label (the setdefault every other parser here uses) takes the first,
         which puts the price boundary LEFT of the item code and collapses the
         entire right-hand side of the table onto the UOM column. Separated by
         ORDER of appearance, with the bare "Price" label as the fallback if a
         future template drops the word.

     (b) THE TAX CODE IS THE FIRST WORD OF THE DESCRIPTION CELL. The stock line
         reads "GST SMOKED MOZZARELLA & BASIL ARANICNI" with "GST" at x=158.2 —
         inside the description cell, at the same x as the wrapped tail "65G
         (7.8KG)" and as the levy line's own first word "FUEL". So geometry
         cannot separate it, and leaving it in ships "GST Smoked Mozzarella..."
         to the chef and splits the identity the day a line arrives without one.
         Stripped, but ONLY the three codes that cannot begin a food name (GST,
         FRE, EXP). "FREE" is deliberately excluded — "FREE RANGE EGGS" is a
         real product and a greedy rule that eats a real word is exactly the
         item 1 / 22 / 33 defect class. It is cost-neutral either way: the tax
         treatment is decided by the GST column's own arithmetic, never by this
         token.

     (c) THE ACCOUNT BALANCE IS PRINTED BELOW THE INVOICE TOTAL AND IS LABELLED
         "Total". "Total Amount Outstanding  $1,029.49" is what Deni owes us
         across the account; the bill is $341.33. Any total-reader that scans
         for the word "Total" from the top, or takes the largest money figure,
         books a payable three times the real one. The parser reads the
         structural "TOTALS: <Sale Amount> <GST> <Total>" row by POSITION and
         stops the line table there, so the balance row is never scanned at all.

     Money shape: the "Total" column is INC GST and "Unit Price" is EX (15.6 x
     19.89 = 310.30 ex, GST 31.03, Total column 341.33), and sum(Total column)
     equals the printed TOTALS row to the cent on all three invoices. Wrapped
     descriptions are joined by the derived-indent rule (foodlink item 33); the
     dangerous neighbours here are "CARTON QTY: 0" and "GOODS DELIVERED BY
     MARIO", both description-only rows, both ~62pt right of the indent.

     MEASURED before/after on the same corpus: deni_foods 0/3 -> 3/3 (100%),
     all three reconciling to the cent, and NOT ONE other supplier moved by a
     single document. Both audits stayed clean; the parser test suite passes.
     TOTAL reads 843/864 (97%) where the same corpus with neither new domain
     registered reads 840/855 (98%) — the percentage went DOWN because Inalca's
     six invoices are
     now COUNTED rather than invisible, which is the honest direction and the
     same movement item 17 recorded when the Xero templates first appeared.

 51. THE REVIEW SWEEP WEDGED TWICE AND IS THE REASON NOTHING WAS PROMOTED FROM
     THE BACKLOG TODAY. pull_mailbox --source-folder "Invoices Review" stopped
     making progress after ~40 messages (1.99s CPU over 66 minutes), was killed,
     and the retry stopped again after ~20 (0.67s CPU over 8 minutes). This is
     item 39's hang, not item 21's 5xx and not item 30's retry gap: the process
     is alive, burning no CPU, and the per-request GRAPH_TIMEOUT never fires, so
     something is blocking outside a request. The inbox pass (5 messages) ran
     clean, so it is not auth and not the network being down. FOR ZAK — item 39
     already asked for a whole-run watchdog; today is the third run it would
     have saved. NOT attempted here: it is in the auth/mail path and this task
     must never handle credentials.

     What the ~60 messages the two passes did read say about the pile, so
     tomorrow starts from evidence: 0 promoted, and the stuck senders are
     stowawaybar.com 17 (forwarded admin, CLOSED by Zak — item 36),
     nelsonwineco.com.au 5, vinsight.net 5, post.dearsystems.com 4,
     youngandrashleigh.com 3, inalcafb.com.au 3, combinedwines.com.au 3,
     mountainculture.com.au 2, plus singles. Every one of them is liquor or
     admin. With Deni parsed, THERE IS NO KITCHEN-FOOD SENDER LEFT IN THE
     REVIEW PILE WITHOUT A PARSER — the first time that has been true.

 52. B&E IS DROPPING WRAPPED DESCRIPTIONS — 1,145 ROWS — AND IT IS TOMORROW'S
     WORK. Found the same way item 33 found Foodlink: not by this harness, but
     by STEP 3's product review asking why a brand-new ingredient had no pack
     size. b_e reads 130/132 (98%) and has read ~98% for two weeks, because a
     description plays no part in reconciliation. This is the FOURTH instance of
     the item 1 / 22 / 33 class (FFT, Gulli, Foodlink, now B&E) and it is the
     biggest: B&E is the largest kitchen supplier in the file at $16,611 of
     30-day spend.

     THE EVIDENCE, opened rather than inferred. Corpus 5b2a07b73c15 = invoice
     7153386, ingested TODAY. Its word rows read:

       row 28  18304  BEKSUL BLACK (DARK   2.00 2.00 UNIT 0.13 CTN $2.90 ... $5.80
       row 29         BROWN) SUGAR 1KG(16) CJ
       row 30         FOODS KRN #186167

       row 45  13003  FZ PIPI CLAM - WHOLE IN  10.00 10.00 KG 1.00 CTN $8.10 ... $81.00
       row 46         SHELL COOKED 40/60 1KG
       row 47         (10) (I)

     Rows 29/30 and 46/47 carry no qty and no total, so parsers/be_foods.py
     skips them exactly as foodlink.py did before item 33. data/invoices for
     7153386 stores the descriptions "BEKSUL BLACK (DARK" and "FZ PIPI CLAM -
     WHOLE IN" — both truncated mid-phrase, one with an unbalanced bracket.

     IT IS MONEY, NOT COSMETICS, AND THE NUMBER IS BIG. B&E's UOM column says
     UNIT / KG / CTN, so on a UNIT line the PACK SIZE is stated ONLY in the
     description — and the size is usually what wrapped ("1KG(16)" above). The
     live feed shows it: of 141 ingredients in the whole picker with
     needs_pack_review TRUE, 48 are B&E — 37% of B&E's 129 ingredients and the
     largest single bucket, ahead of foodlink's 45 (which is the residue of the
     same defect before item 33 fixed it). The continuation-shaped rows across
     the corpus number 1,145 over 133 invoices, against foodlink's 435 over 129.

     NOT ATTEMPTED TODAY, deliberately: one supplier change per run (the item 45
     rule), and Deni Foods was today's. The fix is known and already written
     twice — derive the indent per invoice from the first line item's own
     description x, require the desc bucket to be the only one with text, and
     require an item to attach to. The dangerous neighbours here will be B&E's
     own footer boilerplate and the delivery note; measure their x before
     trusting the indent, the way item 33 measured foodlink's 435-vs-268 split.
     Do the before/after line-by-line diff against data/invoices (the item
     13/25/33 standard) — a description repair should lengthen names and move
     NO money.

 53. THE COVERAGE RATCHET ROSE BY 2 AND WAS DELIBERATELY NOT RE-PINNED, so CI
     stays red on scripts/check_invoice_coverage.py --strict until Zak rules.
     67 vs baseline 65. Both new arrivals are liquor that went on sale standing
     on a January Back Office seed, and NEITHER CAN BE BRIDGED: a search of
     every file in data/invoices for "glendronach", "scout" and "pinot gris"
     returns ZERO lines, so there is no invoice to bridge to and none was
     invented. Both were the "UNCLASSIFIED — nobody has looked at these yet"
     state, which is the one state the guard forbids; they now carry classes
     with the evidence (Glendronach awaiting_next_purchase — ILG and Paramount
     both parse, so the next delivery prices it; Scout Pinot Gris question —
     its plausible suppliers are the boutique wine senders still stuck in
     Review with no parser, so "the next invoice fixes it" would be a promise
     the pipeline cannot keep). Re-pinning the baseline upward to turn a red
     guard green is the item 43/49 failure — a guard quietly stopping guarding —
     and doing it unattended is worse. FOR ZAK: name Scout's supplier, or
     re-pin deliberately.

TRIAGE LOG — 2026-08-22 (unattended daily run). Corpus 864 readable, TOTAL
843/864 (97%) BEFORE and AFTER the change below, measured on the same corpus
from a /tmp clone pinned at 27693125. The 21 shortfalls were re-opened and
every one is a document this log already carries: be_foods x2, gulli
b381fb197ab6, ilg x2, inalca x6 (item 50 — liquor, deliberately unregistered),
myob 91513b04a9dd (Cork And Co, item 45), paramount 670685f29215, netsuite x4
(the Dext subscription bills of item 41) and xero x4 (the SYMSAFE payment
receipts of item 38). No drift, no new failure mode, and NO change was made to
any of them.

 54. B&E'S WRAPPED DESCRIPTIONS ARE FIXED — item 52's work, taken today. It is
     the FOURTH instance of the item 1 / 22 / 33 class (FFT, Gulli, Foodlink,
     now B&E) and the biggest: 1,142 continuation rows across 132 corpus
     invoices, against Foodlink's 435. b_e read 130/132 (98%) throughout,
     because a description takes no part in reconciliation.

     THE FIX IS THE ONE ITEM 52 SPECIFIED and it is the third time this tree has
     written it, so it was written the same way deliberately rather than
     re-invented: the indent is DERIVED per invoice from the first line item's
     own description x (be_foods.py::_desc_x), a continuation must be the ONLY
     bucket with text, and an item must exist to attach to.

     THE DANGEROUS NEIGHBOUR IS DIFFERENT HERE, AND THE INDENT ALONE WOULD NOT
     HAVE HELD. Foodlink's boilerplate sits ~17pt clear of its indent, so there
     the indent test carries the weight. B&E prints a fuel-levy notice under the
     table whose third line — "MONDAY. Thank you for your understanding and
     continued" — starts at x=75.2, which is 1.3pt from the 76.5 indent and
     INSIDE CONT_INDENT_TOL. The only thing keeping that sentence out of the
     last product's name is that it spills into the ordered/shipped/uom buckets.
     Measured, not assumed: across all 132 invoices 1,142 desc-only rows match
     the indent and 153 do not, and all 153 of those are exactly two lines —
     "Receiver Name:" at x=164.3 and a wrapped depot address at x=134.3. A prose
     scan over the 227 distinct joined tails returns ZERO sentence-like strings;
     the most common are "10MM 2.5KG (4) FARM FRITES", "1KG (8) PENDLE",
     "5X3KG CTN #KAU04-4" — pack sizes, which is the whole point.

     MEASURED to the item-13/25/33 standard rather than on a rate. All 125
     corpus invoices that also exist in data/invoices were re-parsed and diffed
     line by line against the 880 stored lines: 745 descriptions LENGTHENED,
     ZERO rewritten (every change is a strict prefix-extension of the stored
     name), and ZERO movements in qty, line_total_incl, unit_price_incl or
     cost_basis. be_foods 130/132 and TOTAL 843/864 identical before and after;
     not one other supplier moved by a single document; both audits still clean.
     Five fixture tests added from the REAL coordinates of corpus 5b2a07b73c15
     (invoice 7153386), including one that pins the DIAGNOSIS — that both tails
     of the sugar line have no qty and no total, so the old guard dropped them —
     and one that pins the levy notice as passing the indent test and being
     excluded by the columns, because that is the assertion which will fail
     first if anyone ever loosens the desc-only rule.

     AND THE RATE COULD NOT MOVE, stated plainly because the daily task asks for
     one: b_e was already at its ceiling, and its two remaining shortfalls are
     the $0.00 credit dockets this log has carried since 2026-08-08. Refusing a
     description repair because the PASS column cannot see it would mean never
     repairing a name at any supplier already near 100% — the item 33 ruling,
     verbatim. Expect the 48 B&E needs_pack_review ingredients to fall as future
     deliveries re-read under this fix; the HISTORY does not self-heal (source
     PDFs sit behind the Supabase key this pipeline must never hold), and
     _undo_dropped_prefix cannot repair it because these are dropped TRAILING
     words (the item 23/32 asymmetry).

 55. BE_FOODS IS NOW THE LAST PARSER IN THE TREE STILL BUCKETING ON HARD-CODED
     X-POSITIONS, and it is next. COLS is literal (desc 70, ordered 215, shipped
     260 ...) with no _cols_from_header and no fallback path. Measured today:
     the header anchors are IDENTICAL on all 132 corpus invoices (Item 27.8,
     Description 130.3, Ordered 221.8, Shipped 266.9, UOM 315.0, Item Price
     403.5, GST 466.2, Line Total 517.3), so it has not drifted — but that is
     exactly what item 18 said about Gulli four days before item 22 found it had
     drifted in both directions on documents already in the corpus. B&E is the
     largest kitchen supplier in the file ($16,611 of 30-day spend), so a silent
     re-template here costs the most. NOT done today: one supplier change per
     run (the item 45 rule), and a boundary rewrite wants its own before/after
     diff separate from a description repair, or neither result can be
     attributed.

 56. OPERATIONAL — THE SHARED TREE WAS MID-REBASE BEFORE THIS RUN TOUCHED IT,
     the item-48 shape for the second time, and it had been that way long enough
     to matter. `git status` read "interactive rebase in progress; onto
     064543ca / Last command done (1 command done) / No commands remaining" with
     ZERO unmerged paths — a rebase that had finished its work and stopped
     without being continued. HEAD was detached, so THREE poller commits of
     invoice ingest (bb3fa651, 27693125 and today's 0370f3dc) were sitting on no
     branch, and `main` still pointed at dd08b1c2, the PRE-rebase version of the
     first of them. The poller had been committing successfully and pushing
     nothing: its own log ends "You are not currently on a branch ... Please
     specify which branch you want to rebase against."
     This is the SESSIONS.md failure mode without a second session — the commit
     log was not evidence the work was on main. Resolved by stashing an unstaged
     drive_backup.log (a second writer, item 31's shape), `rebase --continue`
     (no conflicts to resolve — the todo was empty), then pull --rebase and
     push: 8823369e..3506e2c6. Nothing was lost; dd08b1c2's content is bb3fa651.
     FOR ZAK — the poller cannot detect this. It should refuse to commit when
     HEAD is detached or a rebase is in progress, and say so loudly, rather than
     writing commits onto a branchless HEAD where they accumulate invisibly.

 57. CI IS RED ON A REAL CRASH, NOT A STALE ASSERTION — different from items
     43 and 49, and it should be looked at. build_cogs_list.py exits 1 with
     `TypeError: '<' not supported between instances of 'NoneType' and 'str'`
     sorting on `(r["supplier_code"], r["invoice_description"])`. Some rows now
     carry supplier_code=None — Farmer Joes chicken lines are the visible
     population ("no supplier_code — no identity" appears eight times in today's
     pull_mailbox output). Two tests fail on it:
     test_cogs_row_counted_once.py::test_the_fact_table_still_holds_both_notes
     and test_write_encoding_is_pinned.py::...[build_cogs_list.py-cogs_list.csv].
     VERIFIED NOT THIS RUN'S WORK: both fail identically with today's parser
     change stashed. NOT fixed here — a sort key is a choice about how
     code-less rows order and where they land in the file, and doing that
     unattended in the same run as a parser change makes both unattributable.

The three zero-total documents (now four, with Gulli CI-437314 — see item 18)
can never PASS by construction: validator's _check_required_fields treats
total_incl <= 0 as a BAD_TOTAL ERROR, deliberately.
So no parser can promote them — the only way to stop them costing an LLM call on
every retry pass forever is to classify a STATED $0.00 total as not-an-invoice in
run.py::looks_like_statement. That is a shared, cross-supplier gate, so it wants
Zak's eyes before it ships, not an unattended daily run's.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.invoices import pdf_text                         # noqa: E402
from modules.invoices.domains import DOMAIN_KEY               # noqa: E402
from modules.invoices.parsers import DOMAIN_TO_PARSER, parse_pdf  # noqa: E402
from modules.invoices.run import (looks_like_credit_note,      # noqa: E402
                                  looks_like_statement)
from modules.invoices.validator import Validator              # noqa: E402

CORPUS = ROOT / "data" / "invoice_corpus"
KEY_DOMAIN = {v: k for k, v in DOMAIN_KEY.items()}


def main() -> int:
    only = set(sys.argv[1:])
    cfg = yaml.safe_load((ROOT / "modules/invoices/suppliers.yaml").read_text())
    V = Validator(cfg)

    keys = sorted(d.name for d in CORPUS.iterdir()) if CORPUS.exists() else []
    if not keys:
        print(f"no corpus at {CORPUS.relative_to(ROOT)} — run build_corpus.py first")
        return 1

    tot_p = tot_n = tot_skip = tot_scan = 0
    # IDENTITY AUDIT — the blind spot this harness had until 2026-08-14.
    # The PASS rate only asks "did the money reconcile". supplier_code plays NO
    # part in reconciling, so a parser can score 100% while quietly corrupting
    # product IDENTITY. Fresh Fruit Team did exactly that: its SKU cell was
    # swallowing the UNIT word, so "CLKG" and "CLKG Kilogram" became two
    # products — split price history, duplicate picker entries, and the "fullest
    # name across the spellings of this identity" consolidation broken, which is
    # how "Large" reached the chef as a product name instead of "Carrot Large".
    # 94 of FFT's 186 codes were affected and this table said 52/52 (100%) the
    # entire time. A supplier code is an IDENTIFIER: it does not contain
    # whitespace. Cheap to check, and it would have caught the whole thing.
    codes: dict[str, set[str]] = {}
    # SECOND BLIND SPOT, found 2026-08-15. The whitespace check above catches a
    # neighbouring column bleeding INTO the code cell. It cannot see the same
    # layout drift bleeding into the UNIT cell, which is how Fresh Fruit Team put
    # the description's first word in raw_uom ("Carrot") and shipped "Large" to
    # the chef as a product name. 274 lines and 51 of 119 codes were affected
    # while this table read 52/52 (100%), because a wrong UNIT still reconciles.
    # A UOM comes from a small, closed vocabulary — pack_size.names_a_unit already
    # knows it — so anything else in that field is another column's text.
    uoms: dict[str, set[str]] = {}
    print(f"{'supplier':<18} {'pass':>11}   review  parsefail   not-inv   scan   parser")
    for key in keys:
        if only and key not in only:
            continue
        dom = KEY_DOMAIN.get(key, "")
        pdfs = sorted((CORPUS / key).glob("*.pdf"))
        p = r = f = skip = scan = 0
        for pf in pdfs:
            raw = pf.read_bytes()
            # Mirror production's order of operations (run.py): a scan has no
            # text for any parser to read, and a statement is refused before the
            # parser is ever called. Neither is a parser failure, so neither
            # belongs in the denominator.
            if not pdf_text.has_text_layer(raw):
                scan += 1
                continue
            _t = pdf_text.text(raw)
            # MIRROR PRODUCTION, BOTH GATES. This mirrored looks_like_statement
            # only, so a CREDIT NOTE — which run.py has always refused — scored
            # here as a parse-fail, i.e. as a missing parser. That is how
            # foodlink read 137/138 on 2026-08-17: the shortfall was Credit Memo
            # SC338338, a document no parser should ever read. A harness that
            # reports a correct refusal as a defect sends the next triage looking
            # for a parser to write.
            if looks_like_statement(_t) or looks_like_credit_note(_t):
                skip += 1
                continue
            try:
                inv = parse_pdf(raw, dom)
            except Exception:
                inv = None
            if inv is None:
                f += 1
                continue
            for _l in inv.lines:
                if _l.supplier_code:
                    codes.setdefault(key, set()).add(_l.supplier_code)
                if _l.raw_uom:
                    uoms.setdefault(key, set()).add(_l.raw_uom.strip())
            if V.validate(inv).ok:
                p += 1
            else:
                r += 1
        n = p + r + f                      # real, readable invoices only
        tot_p += p
        tot_n += n
        tot_skip += skip
        tot_scan += scan
        pct = f"{p}/{n} ({100 * p // n if n else 0}%)"
        has = "yes" if dom in DOMAIN_TO_PARSER else "—"
        print(f"{key:<18} {pct:>11}   {r:>6}   {f:>8}   {skip:>7}   {scan:>4}   {has}")
    tpct = f"{tot_p}/{tot_n} ({100 * tot_p // tot_n if tot_n else 0}%)"
    print(f"{'TOTAL':<18} {tpct:>11}   {'':>6}   {'':>8}   {tot_skip:>7}   {tot_scan:>4}")
    print(f"\n{tot_skip} not-invoice PDF(s) and {tot_scan} scan(s) excluded from the rate.")

    # --- identity audit (see the note above the loop) -----------------------
    # Allowlist mirrors modules/invoices/tests/test_parser_identity.py, which
    # carries the evidence for each entry. nicholas_seafood's "ITEM NO." column
    # genuinely holds multi-word codes ("Barra FSO", "Squid - LW"), so its
    # whitespace is the supplier's own and collapsing it would MERGE distinct
    # products — the opposite of the FFT bug.
    # xero: Philter's own item code is "XPA 200", printed as two tokens ("XPA" at
    # x=31, "200" at x=47) BOTH inside the Item column, with the description
    # starting cleanly at the Description anchor x=85. Verified on PHIN-56956 and
    # PHIN-57196 — the supplier's whitespace, not a bleed. (This check earned its
    # keep the day it was written: it flagged the brand-new xero parser within
    # minutes, and the flag had to be read rather than assumed.)
    WHITESPACE_OK = {"nicholas_seafood", "xero"}
    dirty = {k: sorted(c for c in v if " " in c)
             for k, v in codes.items() if k not in WHITESPACE_OK}
    dirty = {k: v for k, v in dirty.items() if v}
    if dirty:
        print("\n!! IDENTITY WARNING — supplier codes containing whitespace.")
        print("   A code is an identifier. Whitespace almost always means an")
        print("   adjacent column (usually the UOM) bled into the code cell, which")
        print("   SPLITS ONE PRODUCT INTO TWO in the cost book. The money above")
        print("   still reconciles — that is exactly why this needs its own check.")
        for k, v in sorted(dirty.items()):
            total = len(codes[k])
            print(f"   {k:<18} {len(v)}/{total} codes affected, e.g. {v[:3]}")
    else:
        print("\nidentity: no supplier code contains whitespace in any parsed invoice.")

    # --- unit audit (the second blind spot; see the note above the loop) ------
    # A raw_uom that does not name a unit is another column's text sitting in the
    # unit cell — which means the description lost that word. Reported as a count
    # per supplier because a handful are legitimate partial units the supplier
    # itself prints (FFT's "Market" is the head of "Market Bunch"), so this is a
    # smoke alarm to investigate, not a hard failure.
    from modules.invoices.pack_size import names_a_unit          # noqa: E402
    # Allowlist of values the SUPPLIER itself prints that names_a_unit does not
    # know. Every one was opened on 2026-08-15 and its description confirmed
    # COMPLETE, so none is a bleed:
    #   be_foods     DRU/EAC/PK/ROL  truncated DRUM/EACH/PACK/ROLL
    #                                ("OIL - CANOLA OIL" DRU, "FOIL ROLL" ROL)
    #   andrews_meat PK              PACK ("PROSCIUTTO SLICED 500G")
    #   ilg          1xKEG49./1xKEG50 keg pack descriptors ("SAPPORO KEG 50LT")
    #   paramount    MISC            charge lines ("Carton Freight", "Fuel Levy")
    #   fft          Market          head of "Market Bunch"; 400gm a real size
    #   netsuite     CS(12)          Bacchus's printed Units cell, "case of 12".
    #                                Added 2026-08-19 and checked the way this
    #                                comment demands rather than waved through:
    #                                it is the ONLY distinct uom across all 17
    #                                readable Bacchus invoices, it sits in its
    #                                own Units column between Qty and Item Code,
    #                                and every description on those invoices is
    #                                complete (the two that were NOT — a stolen
    #                                vintage and a mid-word item code — were
    #                                found and fixed before this entry went in,
    #                                so this is not papering over a bleed). The
    #                                12 is not discarded: PACK reads it into
    #                                pack_size, which is what makes the
    #                                per-bottle unit price right.
    # They are allowlisted here rather than added to names_a_unit on purpose:
    # names_a_unit is a GUARD in the FFT parser, and widening it would let a
    # description word through as a unit — the very bug this check exists for.
    UNIT_OK = {
        "be_foods": {"DRU", "EAC", "PK", "ROL"},
        "andrews_meat": {"PK"},
        "ilg": {"1xKEG49.", "1xKEG50"},
        "paramount": {"MISC"},
        "fresh_fruit_team": {"Market", "400gm"},
        "netsuite": {"CS(12)"},
    }
    odd = {k: sorted(u for u in v
                     if not names_a_unit(u) and u not in UNIT_OK.get(k, set()))
           for k, v in uoms.items()}
    odd = {k: v for k, v in odd.items() if v}
    if odd:
        print("\n!! UNIT WARNING — raw_uom values that do not name a unit.")
        print("   A UOM is a closed vocabulary. Anything else in that field is")
        print("   another column's text, which means the DESCRIPTION lost a word")
        print("   (FFT: raw_uom 'Carrot', description 'Large'). The money still")
        print("   reconciles, which is exactly why this needs its own check.")
        for k, v in sorted(odd.items()):
            total = len(uoms[k])
            print(f"   {k:<18} {len(v)}/{total} distinct uoms, e.g. {v[:4]}")
    else:
        print("units: every raw_uom names a unit in every parsed invoice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
