# HANDOFF — creating FUNCTIONS in the bookings engine

**For a fresh Cowork chat. Read this whole file before touching code.**
Written 2026-08-16 from the live service + live data, not from memory.

## The one-paragraph version

Functions already work. They are created by `POST /api/admin/bookings` with
`pinned_table` set to an AREA and a long `hold_minutes`. What is wrong is that
this path still runs the party through the **seating solver**, which applies
per-table seat maths that describe *dining covers* — so a function that is
obviously fine in the room gets refused with `409 the floor can't seat this party
at that sitting`. The job is to give functions their own admission rule: **is the
area free? then yes.** No seat counts, no push rules, no party-shape logic.

## Where the code lives (this is NOT the reporting repo)

| Thing | Where |
|---|---|
| Service | `zakstowaway/stowaway-bookings` (**private**), deployed at `https://stowaway-bookings.onrender.com` |
| Engine | `booking_api.py` (FastAPI + SQLite), `solver.py`, `tables.json` |
| Email | `email_notify.py` |
| Daily data snapshots | `backups/YYYY-MM-DD.json`, `backups/live.json` — use these as test fixtures |
| Staff UI | `dashboard/bookings/` in `zakstowaway/mari-daily-reporting` — a **client only**, 442 lines, calls three endpoints |

The staff page calls exactly `/api/admin/overview`, `/api/admin/day/{d}` and
`/api/admin/bookings`. All business logic is server-side. Do not put any of this
logic in the dashboard.

## Proof the manual path works (the Roman booking)

From `backups/live.json`, created 6 Aug for Saturday 8 Aug:

```json
{ "name": "Roman Bunting", "date": "2026-08-08", "time": "15:30",
  "adults": 40, "pinned_table": "Old Stow", "hold_minutes": 240,
  "status": "confirmed",
  "notes": "FUNCTION - birthday. Old Stow booked out. $80 Razzle Dazzle bottomless (2hr) ... Numbers rose 30->40. Source: Monday tracker + functions inbox 1 Aug." }
```

And the same day, `Harry Baker`, 25 pax, `pinned_table: "Main Hall"`, 240 min.

So: **two functions, two different areas, one Saturday, both live.** That is the
shape to preserve. Any change that cannot re-create these two bookings exactly is
wrong, and they are your regression fixture.

Note what marks them as functions today: the string `FUNCTION - ` typed at the
front of `notes`. That is a convention, not a field. See "Decision 1" below.

## What actually blocks bigger functions

`admin_create` (booking_api.py ~line 497) ends with:

```python
ok, _ = check_booking(existing, cand, cfg, event_allowed(ev), event_profile(ev))
if not ok:
    raise HTTPException(409, "the floor can't seat this party at that sitting")
```

`pinned_table` does **not** bypass the solver. In `solver.py` (~line 207) a pin
*narrows* the candidate set to that single unit, and the unit must still be legal:
`seats` / `push` capacity, height, `kid_ok`, `dog_ok`, zone rules. Areas are Units
built with `default_bookable=False` (solver.py ~119) precisely so that only an
explicit pin can select them — that part is right and stays.

The mismatch: an area's `seats` is its **seated dining** capacity. A 40-pax
standing function in Old Stow is a real, sold, paid-for event that the solver
refuses because 40 people cannot be *seated* there. Roman got in at 40 — check
`tables.json` for Old Stow's numbers to see how close to the edge that was; the
next one may not be so lucky.

## Zak's decisions — build to these, do not re-litigate

1. **Admission rule.** A function pins an area and blocks every table in it for
   its duration. The engine refuses **only** if that area is already held at that
   time. Seat maths, push rules and party-shape logic do not apply to functions.
2. **Clash with ordinary bookings inside the area.** Refuse with `409` **and name
   the bookings in the way** (id, time, name, pax) so staff can phone those guests
   and move them, then retry. Nothing is auto-moved and nothing is silently
   oversold.
3. **Entry points — both.** Build the `/bookings/` admin path first so it is
   usable this week, and expose it as one endpoint the Monday.com functions
   tracker can call later with the same payload. Same endpoint, two callers.

## Suggested shape of the work

**Step 1 — make "function" a field, not a note prefix.**
Add `is_function INTEGER NOT NULL DEFAULT 0` via the existing additive `ALTER
TABLE` list (booking_api.py ~line 179 — that is the established migration
pattern; schema changes are additive-only). Backfill it for existing rows whose
notes start with `FUNCTION`. Keep writing the notes prefix for now so the
runsheet and anything reading notes does not change behaviour.

**Step 2 — split the admission check.**
In `admin_create`, when `is_function` is true, replace `check_booking` with an
`area_is_free(existing, area, start, hold, buffer)` check that:
- resolves the area to its member tables via `FloorConfig`;
- overlaps against every active booking on that date using the **same** time
  model the solver uses (`hold_minutes + turn_buffer_minutes`, solver.py header);
- returns the list of conflicting bookings, not just a boolean.
Then `409` with that list in the detail payload.

**Step 3 — prove functions still BLOCK other bookings.**
This is the risk that will bite you. `rows_to_bookings` feeds every existing
booking back into the solver with its `pinned` value, so a stored function is what
stops a normal table booking landing inside Old Stow. Confirm that a function
whose pax exceeds the area's seated capacity still blocks correctly rather than
being dropped or throwing — write the test before you change the code.

**Step 4 — the UI.** A Function mode in `dashboard/bookings/`: area picker
(sourced from `tables.json` areas, not hard-coded), start + end time, pax,
contact, package notes. The page stays a shell; logic stays server-side.

**Step 5 — the tracker seam.** Same endpoint, called with the tracker's fields.
Do not build the Monday integration in the same PR.

## Traps, each one real

- **There is an orphan `forced` column in the LIVE database** (visible in
  `backups/live.json`, `"forced": 0`) that does **not** exist in `booking_api.py`
  — not in the `CREATE TABLE`, not in the `ALTER` list. Either the deployed
  revision is ahead of `main`, or a previous attempt at this exact feature was
  abandoned. **Find out which before adding a new flag**, and check the deployed
  Render revision against `main`.
- **The service token is not yours to paste.** The dashboard fetches it from
  Supabase `app_config` via `Auth.bookingToken()` (RLS: authenticated users only).
  Never reintroduce a token box as the normal path.
- **Two functions can legitimately share a day** (Roman + Harry, 8 Aug) on
  different areas. Do not write a one-function-per-day rule.
- **Guests are never promised a table number** — assignments are internal. Do not
  surface them.
- **`MAX_FUNCTION_PARTY = 200`** already exists; party-size is not the blocker.
- **Do not change `/api/bookings`** (the public guest form). Regression-test it.
- This work is in a **different repo** from the reporting platform. The
  `session.py` claim system does not cover `stowaway-bookings`. If you also touch
  `dashboard/bookings/`, claim `bookings` in the reporting repo first
  (`python3 scripts/session.py start bookings --who "..."`).

## Definition of done

- Roman (40 pax, Old Stow, 240 min) and Harry (25 pax, Main Hall, 240 min) can
  both be created on a clean 8 Aug fixture.
- A regular 4-top on a table inside Old Stow at 16:00 that day is **refused**.
- Attempting a second function on Old Stow at 17:00 is refused, and the response
  names Roman.
- The public guest booking flow is byte-for-byte unchanged in behaviour.
- Deployed, and one real function created through the staff UI end to end.

## Ask Zak before assuming

- Default `hold_minutes` for a function (240 is what both live ones used).
- Whether a function should block the whole area or only its member tables that
  are actually needed (e.g. half of Main Hall).
- Whether deposits / Stripe apply to functions, or they stay invoice-and-pay-on-
  arrival as Roman and Harry both were.
