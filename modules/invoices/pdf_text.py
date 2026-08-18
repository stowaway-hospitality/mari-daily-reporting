"""
Pull the text layer out of an invoice PDF — free, deterministic, no API.

Foundation for the per-supplier parsers. System-generated supplier invoices
(from Xero/MYOB/accounting software) carry a real text layer, so this is exact.
A SCANNED image invoice has no text layer; text() returns near-empty and the
caller falls back to the LLM. See parsers/.
"""

from __future__ import annotations


def text(pdf_bytes: bytes) -> str:
    """All page text, newline-joined. '' if there's no text layer (scanned)."""
    import fitz  # PyMuPDF
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def has_text_layer(pdf_bytes: bytes) -> bool:
    """Enough real text to parse? If not, it's a scan — use the LLM."""
    return len(text(pdf_bytes).strip()) > 60


def word_rows(pdf_bytes: bytes, y_tol: float = 3.0) -> list[list[tuple]]:
    """
    Reconstruct visual table ROWS from word coordinates — robust where the
    flattened text layer is not. A description that wraps to two visual lines,
    or an unusual column order, breaks a "7 lines per item" assumption but not
    this: words are clustered by their y-position into rows, each row sorted
    left→right. Each word is (x0, x1, text). Pages are concatenated.

    Parsers use the header row's word x-positions as column boundaries and
    assign each row's words to the nearest column (see parsers that opt in).
    """
    import fitz  # PyMuPDF
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    rows: list[list[tuple]] = []
    try:
        for page in doc:
            ws = [(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")]
            ws.sort(key=lambda w: (w[1], w[0]))          # y then x
            cur, cy = [], None
            for x0, y0, x1, y1, txt in ws:
                if cy is not None and abs(y0 - cy) > y_tol:
                    rows.append(sorted(cur, key=lambda w: w[0]))
                    cur = []
                cur.append((x0, x1, txt))
                cy = y0 if not cur[:-1] else cy
            if cur:
                rows.append(sorted(cur, key=lambda w: w[0]))
    finally:
        doc.close()
    return rows


def word_rows_with_y(pdf_bytes: bytes, y_tol: float = 3.0) -> list[tuple]:
    """
    word_rows(), but each row is (y, words) instead of just words.

    ADDITIVE on purpose — word_rows() is unchanged and every existing parser
    keeps its exact behaviour. This exists because word_rows() throws the y
    coordinate away after clustering, and for at least one supplier the VERTICAL
    GAP is the only thing that says whether a text-only row is a wrapped
    description cell or the start of a new line item.

    Bacchus (NetSuite) is the case that forced it. NetSuite renders a multi-line
    description cell VERTICALLY CENTRED on its money row, so a three-line
    description puts one line ABOVE the money row and one BELOW it:

        y=460.7            TRE1RRPG is for Harry Gatos. Putting on
        y=469.5  0  NOTE   same invoice to keep urgent fees to a   0.00 ...
        y=478.3            minimum as requested by Samantha Ford

    Row INDEX cannot separate those: the row above and the row below are both
    exactly one row away from two different money rows, so any "attach to the
    nearest row, ties go to X" rule gets one of them wrong whichever way X is
    set. The vertical gap separates them cleanly and with a wide margin —
    measured across the readable Bacchus corpus, a continuation sits 4.4-8.8pt
    from its own money row while two separate line items are 14.9-17.6pt apart.
    Nothing lands in between.
    """
    import fitz  # PyMuPDF
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    rows: list[tuple] = []
    try:
        for page in doc:
            ws = [(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")]
            ws.sort(key=lambda w: (w[1], w[0]))          # y then x
            cur, cy = [], None
            for x0, y0, x1, y1, txt in ws:
                if cy is not None and abs(y0 - cy) > y_tol:
                    rows.append((cy, sorted(cur, key=lambda w: w[0])))
                    cur = []
                cur.append((x0, x1, txt))
                if not cur[:-1]:
                    cy = y0
            if cur:
                rows.append((cy, sorted(cur, key=lambda w: w[0])))
    finally:
        doc.close()
    return rows


def bucket(row: list[tuple], bounds: list[tuple]) -> dict:
    """
    Assign a row's words to named columns by x-position.

    `bounds` is [(name, x_start), ...] sorted left→right; a word at x0 belongs to
    the last column whose x_start <= x0. Returns {name: "joined words"}. This is
    how a coordinate parser turns visual rows into fields robustly.
    """
    out: dict[str, list[str]] = {name: [] for name, _ in bounds}
    for x0, x1, t in row:
        name = bounds[0][0]
        for nm, lo in bounds:
            if x0 >= lo - 2:
                name = nm
            else:
                break
        out[name].append(t)
    return {k: " ".join(v).strip() for k, v in out.items()}
