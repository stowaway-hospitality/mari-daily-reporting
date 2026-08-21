"""The functions enquiry pipeline — the monday tracker turned into a feed.

WHAT THIS IS FOR
----------------
`/functions/` has two halves. The DIARY lists bookings — rooms actually held.
The PIPELINE listed *briefs*, rows in the booking engine's own table, and that
table has been empty since the day it was created. So the screen said
"Pipeline 0" while sixty enquiries sat on the monday.com FUNCTIONS ENQUIRY
TRACKER (board 5027645686) with a dated history of every reply and every
silence on them. Zak asked three times why it was empty. It was empty because
it was reading the wrong table.

This module turns the board into `data/functions_pipeline.json`. It is pure —
it takes the captured board and returns a dict — so every rule below can be
asserted against the real rows in `modules/functions/tests/`.

EVERY ENQUIRY, INCLUDING THE ARCHIVE
------------------------------------
Zak: "the pipeline should reflect all enquiries, whether or not they've been
replied to." So nothing is filtered. The board's three groups — `Stowaway
Bar`, `Harry Gatos` and `archive` — are carried through on every row, and the
screen groups by them rather than dropping any. Thirty-six of the sixty rows
are in `archive`, which is more than half the board: a pipeline that silently
dropped them would report 24 and look like the bug it replaced.

Rows with no date, no headcount and no notes at all are published too. There
are nineteen of them. They are real enquiries somebody typed a name into and
never came back to, and a list that hides them is how they stay forgotten.

WHOSE MOVE IS IT — THE ONE THING THE BOARD KNOWS THAT NOTHING ELSE DOES
-----------------------------------------------------------------------
The Notes column carries a dated running log written by the autodraft
automation:

    [2026-08-19 auto] Awaiting customer reply — nothing back since our
                      11 Aug email (8 days)
    [2026-08-15 auto] Customer replied 14 Aug: "This looks great!..."
    [2026-08-16 auto] Steph replied 15 Aug 2:07pm — ...

`whose_move()` reads the LAST dated entry and nothing else, because the last
entry is the only one that describes the current state; an older line saying
"awaiting customer reply" is a fact about July.

It answers one of four ways, and the fourth is the point:

    them     the entry says in words that we are waiting on the customer
    us       the entry names something for US to do — an unanswered question,
             a chase, an outcome left for a human, a correction, or a fresh
             enquiry with no reply logged after it
    nobody   there are no notes at all. Nobody has done anything.
    unclear  everything else

`unclear` is a real answer and it is drawn as one. Six of the sixty rows land
there and each is a row a person has to read: a note that is not a dated log
at all, a bare "Customer replied" with nothing recorded about whether it was
answered, a log whose last entry is cut off mid-sentence by monday's 2000-
character cap. Guessing on those would put a confident verdict on the screen
where the evidence does not support one, and a wrong "waiting on them" is the
expensive direction — it is indistinguishable from "nothing to do".

Whatever the verdict, `whose_move_evidence` carries the last entry's text
VERBATIM and `whose_move_since` its date, so the screen shows the sentence the
verdict was read off rather than only the verdict. A judgement nobody can
check is a judgement nobody should act on.

US BEATS THEM WHEN AN ENTRY SAYS BOTH. "Awaiting customer reply — no reply
since our first reply on 14 Jul (23 days). Large enquiry (100 pax, 28 Nov) —
worth a chase." says both. The chase wins, because "awaiting a reply" only
states what last happened while "worth a chase" names the next action, and it
is ours. Four rows turn on this.
"""
from __future__ import annotations

import re
from decimal import Decimal

# ---------------------------------------------------------------- the board
BOARD_ID = "5027645686"

# Column ids, confirmed against get_board_info on 2026-08-21 rather than taken
# on trust. A monday column id is opaque — `text_mm6dgpr2` says nothing about
# being the bar tab — so the wrong one reads a plausible value from the wrong
# column and nothing errors.
COL = {
    "event_date":     "date_mm25wnr1",
    "follow_up_date": "date_mm251g5m",
    "stage":          "color_mm25mjfz",
    "outcome":        "color_mm25sdez",
    "occasion":       "text_mm259k8p",
    "group_size":     "numeric_mm25m44y",
    "revenue":        "numeric_mm25gmdd",
    "lost_reason":    "color_mm25e80f",
    "email":          "email_mm259mkz",
    "phone":          "phone_mm25qcxd",
    "notes":          "long_text_mm25dk1",
    "start_time":     "hour_mm69yr9k",
    "area":           "color_mm69zbmc",
    "source":         "color_mm6c9qgr",
    "drinks":         "color_mm6dn3ba",
    "bar_tab_covers": "text_mm6dgpr2",
    "food":           "text_mm6dvkw9",
    "deposit":        "color_mm6dj4yx",
    "music":          "color_mm6d7ar4",
    "settling_up":    "text_mm6dgnbc",
    "min_spend":      "numeric_mm6dnjnp",
}

SCHEMA = "functions_pipeline/1"

# monday's long-text column stops at 2000 characters. It does not say so, it
# does not ellipsis, and it does not care where the sentence was: Emma's log
# ends "a guitarist has a B" and Maryanne's ends "She said she". Both are
# exactly 2000. A note at the cap is reported as INCOMPLETE rather than shown
# as the whole record, because the missing half is the recent half.
NOTES_CAP = 2000


# ------------------------------------------------------------- reading a row
def _txt(v):
    """A column value as text, or None. monday sends "" and null for the same
    thing — nothing was entered — so both become None and neither reaches a
    screen as an empty box that looks like a deliberate blank."""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _num(v):
    """A numbers column as an int, or None. Never a float where the board holds
    a whole number: these are heads and dollars, kept as strings on the board."""
    s = _txt(v)
    if s is None:
        return None
    try:
        d = Decimal(s)
    except Exception:                                        # noqa: BLE001
        return None
    return int(d) if d == d.to_integral_value() else float(d)


def _phone(v):
    """monday's phone column can carry a trailing ISO country code —
    "+61411642774 AU" over GraphQL, "+61411642774" through the MCP. Strip it so
    the two sources produce the same feed and the number is dialable."""
    s = _txt(v)
    if s is None:
        return None
    return re.sub(r"\s+[A-Z]{2}$", "", s)


# ------------------------------------------------------------------ the log
LOG_LINE = re.compile(r"^\[(\d{4}-\d{2}-\d{2})\s+([^\]]+)\]\s*(.*)$")


def parse_log(notes):
    """The Notes column as a list of dated entries, oldest first.

    An entry is a line starting `[YYYY-MM-DD author]`. A line that is not — a
    continuation, or a note somebody typed by hand like "Regular - local super
    fun guy" — is appended to the entry above it if there is one, and otherwise
    ignored. Ignored, not invented: a note with no dated entry anywhere yields
    an empty list, which is what makes `whose_move` say it cannot tell.
    """
    out = []
    for raw in str(notes or "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = LOG_LINE.match(line)
        if m:
            out.append({"date": m.group(1), "author": m.group(2).strip(),
                        "text": m.group(3).strip()})
        elif out:
            out[-1]["text"] = (out[-1]["text"] + " " + line).strip()
    return out


# ------------------------------------------------------- whose move is it
# Each marker is a (regex, why) pair and the `why` is published on the row, so
# the screen can say WHICH phrase decided it. A verdict with no stated reason
# is the thing this feed exists to replace.
#
# Order matters twice over: US is scanned before THEM (see the module
# docstring), and within each list the first match wins and supplies the `why`.
US_MARKERS = [
    (r"needs? a reply",                     "the log says it needs a reply"),
    (r"unanswered",                         "the log says a question is unanswered"),
    (r"chase or close out",                 "the log says chase or close out"),
    (r"worth a chase",                      "the log says it is worth a chase"),
    (r"worth a call",                       "the log says it is worth a call"),
    (r"may need chasing",                   "the log says it may need chasing"),
    (r"final chase",                        "the log calls for a final chase"),
    (r"needs? correcting",                  "the log says something needs correcting"),
    (r"needs? confirming",                  "the log says something needs confirming"),
    (r"left for (?:a )?human",              "the outcome was left for a human to set"),
    (r"please confirm",                     "the log asks a human to confirm it"),
    (r"confirm/close",                      "the log asks a human to confirm or close it"),
    (r"follow[- ]up (?:date[^.]*?)?has passed",
                                            "our own follow-up date has passed"),
    (r"follow[- ]up (?:set|moved to)\b",
                                            "we set ourselves a follow-up and it is the last thing logged"),
    (r"^enquiry via functions form",        "the enquiry is logged and no reply is recorded after it"),
]

THEM_MARKERS = [
    (r"awaiting (?:the )?customer(?:'s)? (?:reply|response)",
                                            "the log says we are awaiting the customer's reply"),
    (r"awaiting (?:her|his|their) (?:reply|response|pick|choice)",
                                            "the log says we are awaiting their answer"),
    (r"awaiting [A-Za-z]+(?:'s)? (?:reply|response|pick|choice|date choice)",
                                            "the log says we are awaiting their answer"),
    (r"ball is (?:now )?in [^.]*court",     "the log says the ball is in their court"),
    (r"awaiting ",                          "the log says we are awaiting something from them"),
]

# A bare "Customer replied ..." with none of the markers above. It is NOT read
# as our move: the log records replies going both ways and this one does not
# say whether ours followed. Reported as unclear, with the line shown.
CUSTOMER_REPLY = re.compile(r"^customer (?:replied|sent)", re.I)


def _match(markers, text):
    for pat, why in markers:
        if re.search(pat, text, re.I):
            return why
    return None


def whose_move(notes):
    """Whose move it is, the date it was decided, the evidence, and the reason.

    Reads ONLY the last dated entry. Returns a dict with `who` in
    {"us", "them", "nobody", "unclear"}.
    """
    log = parse_log(notes)
    if not log:
        raw = str(notes or "").strip()
        if not raw:
            return {"who": "nobody", "since": None, "evidence": None,
                    "why": "there are no notes on this row at all — no reply, "
                           "no chase, nothing logged"}
        # Notes exist but are not a dated log: a line somebody typed. Real
        # information, but it cannot date anything, so it cannot say whose turn
        # it is now.
        return {"who": "unclear", "since": None, "evidence": raw.split("\n")[0],
                "why": "the notes carry no dated log entry, so nothing says "
                       "when anything last happened"}
    last = log[-1]
    text = last["text"]
    common = {"since": last["date"], "evidence": text}
    why = _match(US_MARKERS, text)
    if why:
        return {"who": "us", "why": why, **common}
    why = _match(THEM_MARKERS, text)
    if why:
        return {"who": "them", "why": why, **common}
    if CUSTOMER_REPLY.match(text):
        return {"who": "unclear", "why":
                "the last entry is a customer reply and nothing after it says "
                "whether it was answered", **common}
    return {"who": "unclear", "why":
            "the last entry does not say whose move it is", **common}


# --------------------------------------------------------------- what is open
# The labels the booking engine itself uses (functions.REQUIRED), so the two
# ends agree about what "outstanding" means. Different words here would put one
# list on this screen and another in the 409 the deposit route answers with,
# and staff would have to reconcile them.
REQUIRED = [("event_date", "date"), ("start_time", "start time"),
            ("area", "room"), ("group_size", "guest count"),
            ("food", "food choice"), ("drinks", "drink choice")]


def outstanding(row):
    """Everything still to pin down, in the order you would ask for it.

    "bar tab terms" is conditional, exactly as it is in the engine: an
    open-ended tab with nothing written about what it covers is, per the
    board's own column description, "the single most common way a function
    bill turns into an argument".

    The contact details are NOT in this list even when they are missing. They
    are not something to ask the client for — if there is no email and no
    phone, there is nobody to ask. `contactable` says so separately.
    """
    out = [label for key, label in REQUIRED if not row.get(key)]
    if row.get("drinks") == "Bar tab" and not row.get("bar_tab_covers"):
        out.append("bar tab terms")
    return out


# ------------------------------------------------------------------- flags
# Things in the data that a person has to resolve. Every one of them was found
# in the real board, and each is SURFACED rather than fixed: picking one of two
# dates silently is how the wrong Saturday ends up held.
TITLE_DATE = re.compile(
    r"(\d{1,2})\s*(?:st|nd|rd|th)?\s+"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sept|sep|oct|nov|dec)[a-z]*"
    r"(?:\s+(\d{4}))?", re.I)
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
          "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12}


def title_date(name, event_date):
    """The date written into the row TITLE, as YYYY-MM-DD, or None.

    Half the rows are named "Marcus - 10th Oct" or "Sonia - 12 sept", and the
    title is what a human typed while the Event date column is what the form
    said. When they disagree, one of them is wrong and nobody knows which.

    The year is almost never in the title, so it is taken from the Event date
    column — which is the only assumption made here, and it is the safe one:
    it can only ever make the two agree, never manufacture a disagreement.
    """
    m = TITLE_DATE.search(str(name or ""))
    if not m:
        return None
    day, mon = int(m.group(1)), MONTHS[m.group(2).lower()]
    year = int(m.group(3)) if m.group(3) else (
        int(str(event_date)[:4]) if event_date else None)
    if year is None or not (1 <= day <= 31):
        return None
    return f"{year:04d}-{mon:02d}-{day:02d}"


def flags(row, same_date):
    """What is wrong or unresolved on this row, as a list of coded findings."""
    out = []

    t = title_date(row["name"], row["event_date"])
    if t and row["event_date"] and t != row["event_date"]:
        out.append({
            "code": "date_conflict",
            "note": f"the row is titled \"{row['name']}\", which reads as {t}, "
                    f"but the Event date column says {row['event_date']}. "
                    "Nothing here decides which is right.",
        })

    if row["notes_chars"] >= NOTES_CAP:
        out.append({
            "code": "notes_truncated",
            "note": f"the note is {row['notes_chars']} characters, which is "
                    "monday's cap for this column. It ends mid-sentence and "
                    "the missing part is the most recent part.",
        })

    if len(same_date) > 1:
        others = [o for o in same_date if o["item_id"] != row["item_id"]]
        who = ", ".join(f"{o['name']} ({o['group_size'] or '?'} pax)"
                        for o in others)
        out.append({
            "code": "date_shared",
            "note": f"{len(same_date)} live enquiries at {row['group']} want "
                    f"{row['event_date']}: also {who}. Two functions CAN share "
                    "a night in different rooms — 8 August 2026 was two — so "
                    "this is not automatically a clash, but nothing on the "
                    "board says which room either of them is taking.",
            "others": [o["item_id"] for o in others],
        })

    if row["group"] == "Harry Gatos":
        out.append({
            "code": "no_floor_plan",
            "note": "Harry Gatos has no floor plan in the booking engine, so "
                    "no room can be held for this enquiry here. It is tracked, "
                    "not bookable.",
        })

    if not row["email"] and not row["phone"]:
        out.append({
            "code": "no_contact",
            "note": "no email and no phone on the row — there is nobody to "
                    "chase even if the pipeline says it is our move.",
        })

    return out


# ------------------------------------------------------------- into a brief
# A brief exists so a deposit link can be minted and a room held. That is the
# ONLY reason the pipeline touches the booking engine at all: the sixty rows
# here are not sixty briefs waiting to be created, they are sixty enquiries,
# and most of them will never become a booking.
#
# So `brief_prefill` is what would be POSTed to /api/admin/functions if
# somebody presses "Take a deposit" on this row, and nothing creates it until
# they do. `source_ref` is `monday:<item id>` because create_brief upserts on
# source_ref: pressing the button twice, or re-running a future sync, converges
# on one brief instead of littering.

# The board's Drinks labels carry the price; the engine's DRINK_CHOICES do not,
# and functions.validate() rejects a package name it does not know because a
# package name IS a price. Mapped here rather than in the page so there is one
# copy of the translation.
DRINK_MAP = {
    "SHIN-DIGG $49pp":     "SHIN-DIGG",
    "SOIRÈE $60pp":        "SOIRÈE",
    "RAZZLE DAZZLE $80pp": "RAZZLE DAZZLE",
    "Bar tab":             "bar tab",
    "Not decided yet":     None,
}

# "Not sure yet" and "Whole venue" are both real answers on the board and
# neither is an area the engine accepts — validate() rejects "Whole venue"
# outright. Sent as nothing rather than as a guess; the panel then shows the
# room as unanswered, which is true.
AREA_MAP = {"Main Hall": "Main Hall", "Old Stow": "Old Stow",
            "Not sure yet": None, "Whole venue": None}

# BriefIn.notes is capped at 2000 characters by the engine, the same cap the
# board applies, so a note that is already at the cap fits exactly.
BRIEF_NOTES_CAP = 2000


def brief_prefill(row):
    """The body for POST /api/admin/functions, or None where it cannot help.

    Only Stowaway rows get one. Harry Gatos has no floor plan in the engine, so
    a brief there could never hold a room, and offering a button that mints a
    deposit link against a room nobody can hold is worse than offering nothing.
    """
    if row["group"] == "Harry Gatos":
        return None
    out = {"source_ref": row["source_ref"], "name": row["name"],
           "venue": "Stowaway"}

    def put(k, v):
        if v not in (None, ""):
            out[k] = v

    put("email", row["email"])
    put("phone", row["phone"])
    put("occasion", row["occasion"])
    put("date", row["event_date"])
    put("start_time", row["start_time"])
    put("area", AREA_MAP.get(row["area"] or ""))
    put("guests", row["group_size"])
    put("food", row["food"])
    put("drink", DRINK_MAP.get(row["drinks"] or ""))
    put("tab_restriction", row["bar_tab_covers"])
    put("music", row["music"])
    put("settlement", row["settling_up"])
    put("enquiry_source", row["source"])
    if row["min_spend_dollars"] is not None:
        out["min_spend_cents"] = int(round(row["min_spend_dollars"] * 100))
    if row["notes"]:
        out["notes"] = row["notes"][:BRIEF_NOTES_CAP]
    return out


# ------------------------------------------------------------------- build
def row_of(item):
    """One board item as a feed row, before the cross-row flags are added."""
    cv = item.get("column_values") or {}
    g = item.get("group") or {}
    notes = _txt(cv.get(COL["notes"]))
    move = whose_move(notes)
    row = {
        "id": f"monday:{item['id']}",
        "item_id": str(item["id"]),
        "source_ref": f"monday:{item['id']}",
        "url": item.get("url"),
        "name": item.get("name"),
        "group_id": g.get("id"),
        "group": g.get("title"),
        "archived": g.get("id") == "group_mm50tkw",
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "event_date": _txt(cv.get(COL["event_date"])),
        "follow_up_date": _txt(cv.get(COL["follow_up_date"])),
        "stage": _txt(cv.get(COL["stage"])),
        "outcome": _txt(cv.get(COL["outcome"])),
        "lost_reason": _txt(cv.get(COL["lost_reason"])),
        "occasion": _txt(cv.get(COL["occasion"])),
        "group_size": _num(cv.get(COL["group_size"])),
        "revenue_dollars": _num(cv.get(COL["revenue"])),
        "email": _txt(cv.get(COL["email"])),
        "phone": _phone(cv.get(COL["phone"])),
        "start_time": _txt(cv.get(COL["start_time"])),
        "area": _txt(cv.get(COL["area"])),
        "source": _txt(cv.get(COL["source"])),
        "drinks": _txt(cv.get(COL["drinks"])),
        "bar_tab_covers": _txt(cv.get(COL["bar_tab_covers"])),
        "food": _txt(cv.get(COL["food"])),
        "deposit": _txt(cv.get(COL["deposit"])),
        "music": _txt(cv.get(COL["music"])),
        "settling_up": _txt(cv.get(COL["settling_up"])),
        "min_spend_dollars": _num(cv.get(COL["min_spend"])),
        "notes": notes,
        "notes_chars": len(notes or ""),
        "notes_truncated": len(notes or "") >= NOTES_CAP,
        # The parsed log is NOT republished. `notes` already carries every
        # line verbatim and the screen draws it in full; a second copy of the
        # same prose is 40% of the feed's bytes and one more thing that can
        # disagree with the first. What the derivation actually used is
        # published instead: how many dated entries there are, who wrote the
        # last one, and the line itself in `whose_move_evidence`.
        "log_entries": len(parse_log(notes)),
        "log_last_author": (parse_log(notes)[-1]["author"]
                            if parse_log(notes) else None),
        "whose_move": move["who"],
        "whose_move_since": move["since"],
        "whose_move_evidence": move["evidence"],
        "whose_move_why": move["why"],
    }
    row["min_spend_cents"] = (None if row["min_spend_dollars"] is None
                              else int(round(row["min_spend_dollars"] * 100)))
    row["outstanding"] = outstanding(row)
    row["contactable"] = bool(row["email"] or row["phone"])
    return row


def build(raw):
    """The whole feed, from the captured board. No clock, no network."""
    rows = [row_of(i) for i in raw["items"]]

    # Two enquiries share a date only if they are both LIVE and both at the
    # same venue. Archived rows are history — a July clash resolved itself
    # when July happened — and Stowaway and Harry Gatos are different rooms in
    # different buildings, so 19 September carrying one of each is not a
    # clash. Keying on the date alone reported thirty of these, most of them
    # noise, and a flag that is usually noise is a flag people stop reading.
    by_date = {}
    for r in rows:
        if r["event_date"] and not r["archived"]:
            by_date.setdefault((r["group"], r["event_date"]), []).append(r)

    for r in rows:
        key = (r["group"], r["event_date"] or "")
        r["flags"] = flags(r, by_date.get(key, []))
        r["brief_prefill"] = brief_prefill(r)

    counts = {"total": len(rows)}
    for key in ("group", "whose_move"):
        c = {}
        for r in rows:
            c[r[key] or "—"] = c.get(r[key] or "—", 0) + 1
        counts["by_" + key] = dict(sorted(c.items()))
    counts["flagged"] = sum(1 for r in rows if r["flags"])
    counts["no_notes"] = sum(1 for r in rows if not r["notes"])

    return {
        "schema": SCHEMA,
        "captured_at": raw["captured_at"],
        "board_id": raw["board_id"],
        "board_url": raw["board_url"],
        "board_name": raw["board_name"],
        "groups": raw["groups"],
        "counts": counts,
        "note": "Every enquiry on the monday FUNCTIONS ENQUIRY TRACKER, "
                "including the archive group. `whose_move` is read from the "
                "LAST dated entry of the notes log and is 'unclear' wherever "
                "that entry does not say; `whose_move_evidence` carries that "
                "entry verbatim so the verdict can be checked against it. "
                "`captured_at` is when the board was read, not when this file "
                "was written — a consumer must show its age.",
        "enquiries": rows,
    }
