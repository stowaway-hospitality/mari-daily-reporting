# Pack sizes — settled

**Decision (Zak, 2026-08-10): "its fine to be below 1 case."**

No pack rounding. No pack floor. The model may recommend a par below one full
case or crate, and that is correct — Lightspeed's par is a target stock level,
not an order quantity. Converting a par gap into whole cases, MOQs and supplier
minimums happens at ORDER time (the `lightspeed-reorder` skill), which is the
right place for it.

## Why this was raised, and why it is now closed

47 SKUs carry a pack size >1 in the Back Office catalog (cans in crates of 24,
wine in 6s and 12s, VB in 30s), and the live pars divided suspiciously neatly
into pack multiples — Coke Zero Can 40.7 = 1.70 crates, Little Dragon 49 = 2.04,
Kuku Sauv Blanc 30.5 = 2.54 cases. That pattern suggested pars were being set in
cases deliberately, and that the model producing fractional-case numbers
(Peroni 11.0 = 0.46 of a crate, VB Tinnie 5.0 = 0.17) was a modelling gap.

It is not. Zak has confirmed sub-case pars are intended. The clustering was a
by-product of how the pars were originally set, not a constraint to preserve.

## Consequence

The 34 Stowaway SKUs sitting below one pack need no correction, and the
model-recommended DECREASES that land below a case (Peroni 22 -> 11, VB Tinnie
13.3 -> 5.0, Little Dragon 49 -> 23.6, Corona 43.4 -> 31.3) are legitimate on
this policy. They remain un-uploaded only because the 2026-08-10 upload was
deliberately raises-only; they are not blocked by any pack concern.
