# Recipe derivation notes (for the Apple Notes -> recipe yaml build)

These capture corrections/rules that aren't obvious from the note titles.
Source: Zak, 2026-08-09.

## Base template: Classic Martini
Note "MARTINI - VODKA OR GIN". Classic build:
- 50 ml base spirit (vodka or gin)
- 10 ml Noilly Prat Dry Vermouth [Bottle]
(Dirty adds 7.5ml olive brine; Dry = rinse only; Wet = 40/20.)

## "<Brand> Martini" = template + that brand's spirit (NOT a gintonica)
The par-relevant point: these draw down the specific base-spirit bottle.

- **Four Pillars Olive Leaf Martini**  ->  Classic Martini template with
  **50 ml Four Pillars Olive Leaf Gin [Bottle]** + 10 ml Noilly Prat.
  (It is NOT the "FOUR PILLARS OLIVE LEAF GINTONICA" note.)
- Same rule applies to any brand-named martini in Classic/Signature, e.g.
  "Bombay Sapphire Gin Martini" -> 50 ml Bombay Sapphire [Bottle] + 10 ml Noilly Prat.

## Coverage-gate implication
Brand martinis must map to the template + the correct base-spirit Lightspeed ID,
otherwise the base spirit's consumption is undercounted. The gate should treat a
"<brand> martini" with no explicit recipe as COVERED-BY-TEMPLATE only if the brand
resolves to a base-spirit SKU; otherwise flag it.


## Yuzu Chu-hi (HG, Signature) — spec from Zak 2026-08-09
- 30 ml Yuzushu  -> "Wa no Kokoro Yuzushu [Bottle]" (HG)
- 15 ml Lemon Juice
- 15 ml Sugar Syrup  (subrecipe "Sugar Syrup")
- Soda (top)
- Lemon slice (garnish)
Par-relevant draw: 30ml Wa no Kokoro Yuzushu per serve.
(Lemon Chu-hi has its own note "CHU-HI LEMON (1L)" — batch, resolve separately.)
