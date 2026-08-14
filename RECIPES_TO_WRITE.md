# Recipes to write — a confirm-not-write worklist

> Regenerate with:
> `python3 scripts/build_recipes_to_write.py > RECIPES_TO_WRITE.md`
>
> **Why this file exists.** 41 of the 87 cost-book flags are "sells well, no
> recipe", and they are the single largest thing standing between the cost book
> and the truth — ~$39,500 of revenue the book cannot cost, which is also what
> stops `data/cogs_variance.json` (bought minus used) from being a waste number
> rather than a question.
>
> None of it is an engineering problem. Every line below needs somebody who knows
> the menu, so the job here was to make that as cheap as possible: rank by money,
> and for anything whose ingredient the book ALREADY prices, quote the portion a
> comparable dish already uses. The add-ons table in particular is 26 lines where
> the only open question is a gram weight.
>
> Nothing here has been written into the book. Guessing a recipe is what
> `data/product_recipe_aliases.yaml` records going wrong three times in one
> session.

Generated from data/cost_book_flags.json. Every line below is revenue the cost
book cannot see, ranked by 13 weeks of sales. Nothing here is guessed into the
book — where the ingredient already exists and other dishes already portion it,
that portion is quoted so the answer is a yes/no rather than a blank page.

## Dishes (write a recipe in Produce)

| 13wk $ | dish | group | nearest costed dish, for reference |
|---:|---|---|---|
| 5,242 | **Arancini Balls [5pc]** | Small Plates |  |
| 4,227 | **Beef Cheek** | Kitchen Specials | Dumplings - Beef & Cabbage — $1.46 |
| 3,385 | **Baked Camembert** | Small Plates |  |
| 3,290 | **Roast Turkey** | Kitchen Specials | Pork Roast — $4.61 |
| 3,200 | **$80 Razzle Dazzle** | Functions & Misc. |  |
| 3,144 | **Shredded Beef** | Big Plates / Harry Gatos Food | Dumplings - Beef & Cabbage — $1.46 |
| 2,519 | **Pie** | Big Plates |  |
| 2,182 | **$60 Soiree** | Functions & Misc. |  |
| 2,090 | **Miso** | Big Plates | Miso Tare — $7.46 |
| 1,649 | **Shoyu** | Big Plates / Harry Gatos Food |  |
| 1,595 | **Unlimited BBQ** | Big Plates | Unlimited Dumplings — $7.00 |
| 1,592 | **Eggplant Parmy** | Big Plates | Fancy Pants Parmy - Classic — $7.60 |
| 1,485 | **Chicken Karaage** | Small Plates | Tandoori Chicken [2Kg] — $19.55 |
| 1,254 | **BBQ Meat Platter** | Big Plates | Regular BBQ Chicken Pizza [Dine-in] — $2.49 |
| 1,245 | **Cauliflower Tacos** | Small Plates | Popcorn Cauliflower — $0.96 |
| 1,217 | **Amalfi Olives** | Small Plates |  |
| 1,131 | **Fresh Lime Soda** | Non-alcoholic | Soda & Lime Glass — $0.04 |
| 1,091 | **Edamame** | Harry Gatos Food / Small Plates |  |
| 1,027 | **Farmhouse Salad** | Salads | Sunomono Pickle Salad — $2.67 |
| 932 | **BBQ Ribs** | Big Plates | Regular BBQ Chicken Pizza [Dine-in] — $2.49 |
| 910 | **The Full Monty [Roast + Dessert + Wine + Tawny]** | Kitchen Specials | Under the Bridge Pizza — $3.93 |
| 901 | **Fries D** | Delivery Kitchen | Sweet Potato Fries D — $2.53 |
| 860 | **Axl Rose Pizza** | Marilyna's Pizza | Vinada Sparkling Rose — $3.05 |
| 822 | **Fancy Pants Parmy - Mexicali** | Big Plates | Fancy Pants Parmy - Classic — $7.60 |
| 789 | **Southern Squid** | Small Plates |  |
| 685 | **Blind Winos Ticket** | Functions & Misc. |  |
| 629 | **Virgin Margy** | Mocktails | Margy Jar — $9.60 |
| 531 | **Chicken Parmy D** | Delivery Kitchen | Tandoori Chicken [2Kg] — $19.55 |

## Add-ons and sides — the ingredient is already costed

Single-ingredient lines. The book already prices the ingredient and already
knows what a plate of it looks like, so the only open question is the PORTION.
Confirm the gram weight and the line is done.

| 13wk $ | add-on | the ingredient it adds | a parent dish's portion |
|---:|---|---|---|
| 234 | **Add Prawns** | harissa prawns [7kg] | Gluten-free Hail Africa uses 83.772g = $2.099 |
| 176 | **Add Pepperoni** | pepperoni [3kg] | Large Little Italy uses 76g = $1.241 |
| 166 | **Add Anchovies** | anchovies [tin 690g] | Gluten-free Cosmopolitan uses 7.16g = $0.200 |
| 147 | **Add Bacon** | bacon pizza [5kg] | Large Super House Special uses 45g = $0.290 |
| 131 | **Add Calabrese** | calabrese [1kg] | Gluten-free New Yorker uses 61.576g = $1.539 |
| 122 | **Add Mushrooms** | mushrooms [4kg box] | Large Super House Special uses 62g = $0.560 |
| 104 | **Add Fetta** | fetta [2kg] | Gluten-free The Paddock [Dine-in] uses 28.64g = $0.379 |
| 96 | **Add Pineapple** | east coast pineapple juice [2l] | Piña Colada uses 45ml = $0.156 |
| 96 | **Add Ham** | ham soccerball sliced pendle [1kg] | Large Super House Special uses 50g = $0.635 |
| 95 | **Add Olives** | mixed olives [6kg] | Large Super House Special uses 40g = $0.400 |
| 92 | **Add Chilli** | chilli flakes | Large Little Italy uses 1g = $0.020 |
| 85 | **Add Jalapenos** | jalapenos [3kg] | Gluten-free Sanchez uses 17.9g = $0.056 |
| 79 | **Add Chicken** | chicken [5kg] [bbq chicken pizza] | Gluten-free The Paddock [Dine-in] uses 62.292g = $0.759 |
| 72 | **Extra Mozzarella** | big cheese - shredded mozzarella [2kg] | Large Little Italy uses 142g = $1.491 |
| 56 | **Add Garlic** | garlic oil [batch] | Gluten-free Garlic Cheese Pizza uses 30.788g = $0.043 |
| 47 | **Add Prosciutto** | prosciutto sliced [500g] | Large Little Italy uses 70g = $1.960 |
| 44 | **Add Capsicum** | capsicum [1kg] | Large Super House Special uses 60g = $0.588 |
| 33 | **Add Bocconcini** | bocconcini [1kg] | Large Little Italy uses 57g = $1.026 |
| 28 | **Add Onion** | spanish onion [10kg] | Large Little Italy uses 20g = $0.048 |
| 27 | **Add Basil** | basil [bunch/2.50] | Large Little Italy uses 1g = $0.012 |
| 24 | **Add Cherry Tomatoes** | — not in the book — | |
| 22 | **Add Rocket** | baby rocket | Gluten-free Truffalo Soldier uses 21.48g = $0.286 |
| 15 | **Add Pumpkin** | pumpkin [1kg] | Gluten-free The Paddock [Dine-in] uses 26.492g = $0.130 |
| 15 | **Add Spanish Onion** | spanish onion [10kg] | Large Little Italy uses 20g = $0.048 |
| 12 | **Add BBQ Sauce** | sunshine smokey bbq sauce [3l] | Gluten-free BBQ Chicken Pizza uses 43.676ml = $0.182 |
| 10 | **Add Sour Cream** | spiced sour cream [batch] | Gluten-free Sanchez uses 32.22g = $0.315 |
| 8 | **Add Tandoori Chicken** | tandoori chicken [2kg] | Gluten-free Tandoori Chicken uses 64.44g = $0.695 |
| 7 | **Add Pesto** | basil pesto [1.9kg] | Gluten-free The Paddock [Dine-in] uses 21.48g = $0.328 |
| 6 | **Add Sun-dried Tomatoes** | — not in the book — | |
| 5 | **Add Corn** | corn [2kg] | Gluten-free Sanchez uses 21.48g = $0.115 |
| 4 | **Add Pesto Base** | — not in the book — | |
| 4 | **Add Eggplant** | pizza eggplant | Gluten-free Roma Special [Dine-in] uses 33.652g = $0.422 |
| 3 | **Add Sliced Tomatoes** | — not in the book — | |
| 2 | **Add Oregano** | oregano leaves rubbed - torino | Large Little Italy uses 1g = $0.030 |
| 1 | **Add Garlic Base** | garlic sauce base | Gluten-free Truffalo Soldier uses 64.44g = $0.235 |
| 1 | **Add Minted Yoghurt** | — not in the book — | |
| 1 | **Add Shallots** | shallots [bunch] | Gluten-free Sanchez uses 10.74g = $0.057 |
| 238 | **1/2 Marinated Egg** | — not in the book — | |
| 220 | **Extra Yorkshire Pudding** | yorkshire pudding prep [110 units] | Nut Roast uses 1ml = $0.210 |
| 162 | **Side Aioli** | j.j. aioli [batch] | Gluten-free Jimmy Jury uses 42.96g = $0.314 |
| 89 | **Add Grilled Chicken** | grilled chicken [kg] | Gluten-free El Patron uses 90.932g = $1.482 |
| 85 | **Extra Pork.** | pork leg roast [1kg] | Pork Roast uses 220g = $2.200 |
| 80 | **Extra Veg** | spring roll veg hakka [ea] | Vegetarian Spring Rolls uses 2ea = $1.280 |
| 79 | **Swap Fries for Sweet Potato Fries** | — not in the book — | |
| 76 | **Side Blue Cheese Dip** | blue cheese dip [5.5kg] | $2 Buffalo Wing uses 4.375g = $0.038 |
| 74 | **Add Grilled Fish** | — not in the book — | |
| 62 | **Add Lepinja Bread** | — not in the book — | |
| 62 | **Side Chipotle Mayo** | chipotle mayo [1.1kg] | Extra Taco uses 20g = $0.129 |
| 56 | **Add Pulled Beef Brisket** | cooked beef brisket [1kg] | Gluten-free Sanchez uses 50.12g = $1.222 |
| 45 | **Extra Piece Prawn Toast** | — not in the book — | |
| 34 | **Side Spicy Salsa** | — not in the book — | |
| 33 | **Swap for Sweet Potato Fries** | — not in the book — | |
| 27 | **Extra Arancini Ball** | — not in the book — | |
| 26 | **Side Guac** | — not in the book — | |
| 18 | **Side Sour Cream** | spiced sour cream [batch] | Gluten-free Sanchez uses 32.22g = $0.315 |
| 18 | **Side Queso Dip** | queso dip prep | Holy Guacamole uses 65g = $0.676 |
| 16 | **Extra Patty** | — not in the book — | |
| 15 | **Extra Piece** | — not in the book — | |
| 11 | **Add Fried Cauliflower** | — not in the book — | |
| 11 | **Extra Corn Chips** | corn chips [bag] | Holy Guacamole uses 155g = $0.853 |
| 9 | **Extra Cheese** | big cheese - shredded mozzarella [2kg] | Large Little Italy uses 142g = $1.491 |
| 9 | **Extra  Piece** | — not in the book — | |
| 8 | **Side Guacamole** | guacamole [4kg] | Holy Guacamole uses 235g = $1.987 |
| 8 | **Add Side Guac** | — not in the book — | |
| 7 | **Add Onion Rings** | — not in the book — | |
| 4 | **Add Pulled Mushroom** | pulled mushroom [1kg] | Cauliflower Burrito uses 90g = $1.119 |
| 4 | **Add Side Spicy Salsa** | — not in the book — | |
| 2 | **Add Side Queso Dip** | queso dip prep | Holy Guacamole uses 65g = $0.676 |
| 2 | **Add Cheese** | big cheese - shredded mozzarella [2kg] | Large Little Italy uses 142g = $1.491 |
| 1 | **Add Parmesan** | dairy farmers shaved parmesan [1kg] | Cauliflower Cheese Prep uses 125g = $2.500 |
| 25 | **Add Side Salad** | — not in the book — | |
| 15 | **Extra Chicken.** | chicken [5kg] [bbq chicken pizza] | Gluten-free The Paddock [Dine-in] uses 62.292g = $0.759 |
| 7 | **Extra Chashu Pork** | — not in the book — | |
| 2 | **Extra Marinated Tofu** | — not in the book — | |
| 14 | **Extra BBQ Pork** | bbq pork buns canton [ea] | BBQ Pork Buns [2pc] uses 2ea = $3.500 |
| 14 | **Extra BBQ Wagyu** | — not in the book — | |

## Everything else in the grouped flags


**Add-ons - Pizza — 44 uncosted lines (Marilyna's)**

- $325 — Online Surcharge
- $106 — Vegan Cheese [Pizza]
- $91 — Chicken
- $24 — Online Delivery Fee
- $21 — Chicken.
- $20 — Sour Cream
- $0 — Online Rounding

**Add-ons - Kitchen — 51 uncosted lines (Harry Gatos)**

- $434 — Chicken
- $137 — Fried Egg
- $68 — Tofu
- $61 — Chilli Bomb
- $60 — GFO [GF Ramen Noodles]
- $43 — Make it Vegan [patty + cheese + bun]
- $29 — Black Garlic Oil
- $24 — Caramelised Onion Jam
- $19 — GF Flatbread
- $18 — Bamboo Shoots
- $17 — Double Scoop
- $14 — Roasted Tomato Coulis
- $9 — Chicken.
- $9 — Hot Honey
- $8 — 2x Aioli
- $6 — Jalapeños Inside Burrito
- $6 — Jalapeños on Side
- $1 — Jalape|os

**Delivery Kitchen — 16 uncosted lines (Stowaway)**

- $400 — Beef Cheek D
- $392 — Chicken Schnitty D
- $257 — Arancini Balls 5pcs D
- $219 — Farmhouse Salad D
- $172 — Baked Camembert D
- $170 — Onion Rings D
- $141 — Eggplant Parmy D
- $127 — Pie D
- $122 — Cauliflower Tacos D
- $119 — Chicken Burger D
- $66 — Lepinja Bread D
- $63 — Southern Squid D
- $47 — Vegilante Burger D
- $29 — Olives D
- $17 — Tuk Tuk Salad D

**Sides — 4 uncosted lines (Harry Gatos)**

- $362 — Onion Rings
- $317 — Lepinja Bread
- $172 — Jasmine Rice
- $55 — Fries [HG]

**Add-ons - Kitchen — 33 uncosted lines (Stowaway)**

- $25 — Roasted Tomato Coulis
- $24 — Make it Vegan [patty + cheese + bun]
- $18 — GF Flatbread
- $17 — Caramelised Onion Jam
- $14 — 2x Aioli
- $11 — Hot Honey
- $10 — Double Scoop
- $4 — Jalapeños Inside Burrito
- $2 — Jalapeños on Side

**Bar / FOH (no reporting group) — 10 uncosted lines (Harry Gatos)**

- $256 — Chicken skewers
- $180 — Rice Pudding
- $124 — Zagara Orange [HG]
- $120 — Gado Gado
- $88 — Buttermilk Chicken
- $8 — Fish Cake [x2 slices]
- $5 — Black Mushroom
- $3 — Corn
- $1 — Nori Sheet

**Kids Meals — 5 uncosted lines (Stowaway)**

- $258 — Kids Margherita Pizza
- $146 — Kids Hawaiian Pizza
- $137 — Kids Ham & Cheese Pizza
- $66 — Kids Meat Roast
- $26 — Kids Spag Bol

**Harry Gatos Food — 12 uncosted lines (Harry Gatos)**

- $269 — Kids Fried Rice
- $148 — Karaage Chicken
- $74 — Miso Ramen
- $61 — Kids Fried Rice D
- $28 — Sticky Chicken Wings
- $15 — Kids Dumplings - Veg
- $15 — 1x Soft-boiled Egg
- $8 — Tofu.
- $6 — Fried Egg.
- $1 — 2x Soft-boiled Eggs

**Non-alcoholic — 5 uncosted lines (Stowaway)**

- $380 — Stow Soda - Passionfruit
- $102 — Ginger Ale
- $66 — Hot Chocolate
- $57 — Tonic Glass
- $10 — $5 Stow Soda

**Small Plates — 5 uncosted lines (Harry Gatos)**

- $245 — Octopus Karaage
- $216 — Salmon Sashimi Special
- $32 — Thai Chicken
