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

The three zero-total documents can never PASS by construction: validator's
_check_required_fields treats total_incl <= 0 as a BAD_TOTAL ERROR, deliberately.
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
from modules.invoices.run import looks_like_statement         # noqa: E402
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
            if looks_like_statement(pdf_text.text(raw)):
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
    WHITESPACE_OK = {"nicholas_seafood"}
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
    # They are allowlisted here rather than added to names_a_unit on purpose:
    # names_a_unit is a GUARD in the FFT parser, and widening it would let a
    # description word through as a unit — the very bug this check exists for.
    UNIT_OK = {
        "be_foods": {"DRU", "EAC", "PK", "ROL"},
        "andrews_meat": {"PK"},
        "ilg": {"1xKEG49.", "1xKEG50"},
        "paramount": {"MISC"},
        "fresh_fruit_team": {"Market", "400gm"},
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
