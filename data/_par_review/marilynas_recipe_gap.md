# Marilyna's — what the par model does and does not cover

**Policy (Zak, 2026-08-10): "we aren't doing pars on food items."**
Pizza inputs — flour, mozzarella, bases, boxes — are **out of scope for pars**.
Nothing below is a backlog or a work queue.

## What flows through

Marilyna's has no till of its own: its sales are an attributed slice of the
Stowaway till, and its stock is **Stowaway's**. Two things therefore reach the
Stowaway pars:

1. **Packaged drinks sold directly** — Coke Zero Can 6.85/wk, Coke 1.25L 5.38,
   Sprite Can 4.85, Coke Can 4.69, Coke Zero 1.25L 3.46, Solo/Sprite 1.25L,
   Grifter cans, Sunkist. Attributed automatically by name.
2. **Drinks bundled inside deals** — a "$60 BANQUET" or a "Banquet Deal Pizzas"
   takes a whole 1.25L Coke off the shelf without that bottle ever ringing as a
   sale. Worth **+6.06 bottles/week** on `Coke 1.25L` alone, which took its
   recommendation from 10.0 to 15.0 against a live par of 9.9. See
   `modules/par/deals.py`.

## What does not, by design

215 Marilyna's POS lines (660 units/wk) are pizzas and finished goods. They are
classified **out of scope**, not as an unattributed gap, and the build does not
nag about them.

The only reason the model reads Lightspeed's deal definitions at all is to find
the **drinks** inside them. The food components are ignored deliberately.

## One consequence worth knowing

`Halal Pepperoni Slice Fettayleh [1kg]` is the single food SKU that still carries
a live par (22). Under this policy it has no demand signal behind it and will not
self-correct — it is ordered on judgement, like the rest of the kitchen stock.
Every other pizza input already sits at par 0, i.e. outside the par system.
