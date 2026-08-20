#!/usr/bin/env python3
"""Rock-solid pass 2026-08-20: invoice-fed bridges + pricebook pre-declarations."""
import csv, io, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from check_invoice_coverage import findings

by_name = {e["name"]: e for e in findings()}

latest = {}
for r in csv.DictReader((ROOT / "data" / "costs.csv").open(encoding="utf-8-sig")):
    if "seed" in (r["source_invoice"] or "").lower():
        continue
    k = r["ingredient"]
    if k not in latest or r["observed_on"] > latest[k]["observed_on"]:
        latest[k] = r

SUP = {"b-e": "B&E", "fresh-fruit-team": "Fresh Fruit Team", "select-fresh": "Select Fresh",
       "the-berry-man": "The Berry Man", "jun-pacific": "Jun Pacific", "sun-circle": "Sun Circle",
       "urbun-bakery": "Urbun Bakery", "gulli": "Gulli", "foodlink": "Foodlink",
       "paramount": "Paramount", "ilg": "ILG", "nelson": "Nelson", "bacchus": "Bacchus",
       "combined-wines": "Combined Wines"}

# (record name, candidate id, note) — invoice-fed, identity eyeballed.
B = [
 ("Kuku Sauvignon Blanc - Bottle", "nelson:KU25SAB", "Nelson wine invoice; $14.5742/bottle equals BO to the cent"),
 ("Kuku Sauv Blanc - Bottle", "nelson:KU25SAB", "the HG spelling of the same bottle"),
 ("Version Two Sparkling - Bottle", "nelson:HANVSPA", "VERSION TWO BRUT CUVEE; $8.8333 equals BO exactly"),
 ("L'esprit Rosé - Bottle", "nelson:LG24ESP", "LE ESPRIT GRAND CROS ROSE; $25.22 vs BO $18 — a real rise the seed was hiding"),
 ("Petits Detours Rosé Mediterranee - Bottle", "bacchus:PETDETMEDROSE24", "Reserve en Jandem MEDITERRANEE — the med rosé, not the plain PETDETROSE"),
 ("Trentham Estate Rosé - Bottle", "bacchus:TRE3SROSE25", "Trentham Family Sangiovese Rose; $10.5542 vs BO $10.55 — four decimals"),
 ("River Retreat Pinot Grigio - Bottle", "bacchus:TRE1RRPG25", "$10.0517 vs BO $10.05"),
 ("Angas & Bremer Grenache - Bottle", "bacchus:AB1GREN23", "invoiced 2026-06-22; +10% on the seed, real"),
 ("Angas & Bremer Rosé - Bottle", "bacchus:AB1ROSE25", "invoiced 2026-06-09"),
 ("Vinada Sparking Rosé [Bottle]", "paramount:10025433", "MP-VINADA ROSE 750ml, invoiced 2026-08-16"),
 ("Myer's Jamaican Rum", "paramount:78708", "MYERS'S RUM 1000 ml, invoiced 2026-07-21"),
 ("Solo 1.25L", "ilg:460-6046", "SOLO 1.25LT, invoice-fed"),
 ("Coke Zero 1.25L", "ilg:460-1639", "COKE NO SUGAR 1.25 LITRE — Coke's own name for Zero"),
 ("Tsingtao Longneck", "ilg:115-3860", "TSINGTAO PREMIUM BEER 640ML"),
 ("Bittermen's Burlesque Bitters", None, None),
 ("Gyoza Vegetable Marumatsu Jun [800g]", "jun-pacific:QB8514617", "Marumatsu Vegetable Dumpling 10/800gm — exact"),
 ("Passionfruit Puree Seedless [1kg]", "the-berry-man:PJ1", "Passionfruit Puree Seedless 12x1kg; $9.50/kg is the rate the Berry Man test already pins"),
 ("Ice Cream Gold Label Vanilla [4L]", "foodlink:105644", "exact"),
 ("Chicken Nuggets Dinosnacks 6X1Kg Steggles", "foodlink:103383", "exact"),
 ("Turkish Bread [ea]", "urbun-bakery:TFB120", "Urbun Turkish/Focaccia — the earlier garlic-bread candidate was a rate coincidence"),
 ("Eggs 700 Grams [12x]", "fresh-fruit-team:EGL7BX", "FFT 'Eggs 700 Grams' — same name"),
 ("Anchovies", "b-e:18484", "ANCHOVY FILLETS IN OIL 690G Selesta, invoiced 2026-07-16"),
 ("Paprika Smoked Bulk 25kg Galaxy", "b-e:18124", "SPICE - SMOKE PAPRIKA 1KG; per-g, the record's 25kg bulk pack is name drift"),
 ("Ponzu Dashi Vinegar Uchibori [360mL]", "foodlink:101670", "MIZKAN PONZU 1.8L — what the kitchen actually buys; brand drift on the record noted"),
 ("Zaat'ar", "foodlink:109778", "ZAATAR 500GM"),
 ("Char-grilled Caps [4.1Kg]", "foodlink:100442", "PEPPERS ROASTED RED (CAPSICUM) 2.35KG Marco Polo; per-g"),
 ("Prawn Har Gao Sun Circle - 50pcs [1kg]", "sun-circle:SC-PRAWNHARGAO-LG", "exact"),
 ("Sundried Tomato Strips Mezzat [2kg]", "b-e:11602", "ANTIPASTO - SUNDRIED (Mezzat tub) — same product as the bridged 'Sundried Tomato'"),
 ("Cheese Parmesan Block Grana 1/4 Avg 4.4kg R/W", "foodlink:100543", "CHEESE PARMESAN BLOCK GRANA"),
 ("Organic Vanilla Extract [500ml]", "foodlink:105228", "VANILLA CONCENTRATED EXTRACT"),
 ("Nectar Agave Organic 4L Chefs Choice", "foodlink:107287", "NECTAR AGAVE ORGANIC"),
 ("Spring Roll Veg Hakka [ea]", "b-e:18620", "FZ SPRING ROLL - VEGE"),
 ("BBQ Sauce", "b-e:26598", "SAUCE - BBQ 4LTR HEINZ (unit-checked at apply; auto-skips a g/ml clash)"),
 ("Davidson Plum Puree", None, None),
 ("Aioli Garlic Mayonnaise 10kg Birch & Waite", None, None),
]

# Pricebook pre-declarations: identity from ILG's own price book (code+size);
# NO cost row until an invoice lands, exactly the Massel pattern.
PRE = [
 ("Frangelico [Bottle]", "ilg:410-266-0", "Frangelico Liqueur 700ml, ILG price book MAR 2026 $37.78/unit"),
 ("Chambord [Bottle]", "ilg:410-940-4", "Chambord 700ml, book $49.90"),
 ("Disaronno [Bottle]", "ilg:410-602-3", "Disaronno (Amaretto) 700ml, book $42.84"),
 ("Grey Goose", "ilg:330-015-4", "Grey Goose Vodka 700ml, book $58.75"),
 ("Gosling's Black Rum", "ilg:345-623-6", "Goslings Black Seal Rum 700ml, book $62.36 — the 'candidate' was KRAKEN, rejected"),
 ("Laphroaig 10yr", "ilg:300-537-6", "Laphroaig Single Malt 10yo 700ml, book $88.58"),
 ("St. Germain Elderflower Liqueur", "ilg:410-040-0", "St Germain 750ml, book $46.92"),
 ("James Boags Light", "ilg:110-155-0", "James Boags Light Prem Stub 375ml, book"),
 ("Fellr Watermelon Seltzer Tin", "ilg:425-710-0", "Fellr Seltzer Watermelon 330ml, book"),
 ("Sprite 1.25L", "ilg:460-324-5", "Sprite 1.25l x12 PET, book — the invoice candidate was the 375ml cube, rejected"),
]

rows = []
def add(name, cand, note, pre=False):
    e = by_name.get(name)
    if not e:
        print(f"!! not open: {name}")
        return
    pid = e["id"].split(":", 1)[1]
    sup, code = cand.split(":", 1)
    sup_p = SUP.get(sup, sup.title())
    if pre:
        conf = (f"PRE-DECLARED 2026-08-20 rock-solid pass: identity from ILG price book MAR 2026 "
                f"({note}). No cost row until an invoice lands; the day one does, it supersedes the "
                f"seed automatically. The Massel pattern.")
        rows.append([sup_p, code, pid, name, "stowaway", f"{e['seed_rate']:.6f}", "", "", conf, "", ""])
        return True
    li = latest.get(cand)
    if not li:
        print(f"!! no invoice row: {cand}")
        return
    if li["unit"] != e["seed_unit"]:
        # allow bottle-priced wine rows: the declared bottle=750ml conversions
        # added alongside will restate them; same-dimension kg/L handled by build.
        if not (li["unit"] in ("bottle",) or
                {li["unit"], e["seed_unit"]} in ({"kg", "g"}, {"l", "ml"}, {"L", "ml"})):
            print(f"!! UNIT CLASH {name}: seed/{e['seed_unit']} vs {cand}/{li['unit']} — SKIPPED")
            return
    conf = (f"verified 2026-08-20 rock-solid pass: {str(li['description'])[:52]} @ "
            f"{li['cost_per_unit']}/{li['unit']}, latest {li['source_invoice']} {li['observed_on']}. {note}")
    rows.append([sup_p, code, pid, name, "stowaway", f"{e['seed_rate']:.6f}",
                 li["cost_per_unit"], "", conf, li["source_invoice"], li["observed_on"]])
    return True

n = sum(1 for name, cand, note in B if cand and add(name, cand, note))
np = sum(1 for name, cand, note in PRE if add(name, cand, note, pre=True))
buf = io.StringIO(); w = csv.writer(buf, lineterminator="\r\n")
for r in rows:
    w.writerow(r)
(ROOT / "data" / "product_map.csv").open("ab").write(buf.getvalue().encode())
print(f"bridges: {n}, pre-declared: {np}")
