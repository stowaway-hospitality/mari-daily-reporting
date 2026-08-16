# Identity review — one batched pass for Zak

Generated 2026-08-16 by `scripts/build_ingredient_map.py`. The map now carries
165 confirmed supplier-code -> Lightspeed bridges. Two piles need a human call.
Answer in any Cowork chat; a session applies answers to `data/ingredient_map.csv`
and re-runs the generator. Everything here is queued, nothing is guessed.

## 1. Merges HELD because they would LOWER a live cost

The fence: a merge may raise a cost freely, never quietly lower one. For each —
is the cheaper, more recent series the true price (approve merge), or are these
genuinely different products (say so, they stay separate)?

| purchasable | -> ingredient | product | would lower |
|---|---|---|---|
| `bacchus:FD2MOTHER 23` | `lightspeed:20464685` | Mother's Milk Shiraz - Bottle | unit clash bottle vs ml |
| `combined-wines:CCCPN750ML` | `lightspeed:20485056` | Cri De Couer Pinot Noir - Bottle | unit clash bottle vs ml |
| `combined-wines:CGPN750ML` | `lightspeed:20655236` | Geppetto Pinot Noir - Bottle | unit clash bottle vs ml |
| `fresh-fruit-team:BROCE` | `lightspeed:22995320` | Broccolini [Bunch] | unit clash box vs bunch |
| `fresh-fruit-team:OS10BG` | `lightspeed:22995945` | Onion Spanish [10kg] | unit clash kg vs g |
| `grifter:GRIFTER-PALE-50L` | `lightspeed:20487426` | Grifter Pale [Keg] | unit clash keg vs ml |
| `ilg:122-2858` | `lightspeed:20487298` | Alehouse Draught Lager [Keg] | unit clash keg vs ml |
| `ilg:122-2867` | `lightspeed:20487313` | Alehouse Summer Mid [Keg] | unit clash keg vs ml |
| `ilg:460-1639` | `lightspeed:20459553` | Coke Zero 1.25L | unit clash ml vs can |
| `ilg:460-3254` | `lightspeed:20459564` | Sprite Can | unit clash ml vs can |
| `nelson:HA25PIG` | `lightspeed:20466120` | Version Two Pinot Grigio - Bottle | unit clash bottle vs ml |
| `viticult:ECPM24` | `lightspeed:20464662` | Padrillos Malbec - Bottle | unit clash bottle vs ml |
| `viticult:OC24` | `lightspeed:20464694` | Ottelia Chardonnay - Bottle | unit clash bottle vs ml |
| `young-rashleigh:PIZVFSAN1224` | `lightspeed:20466168` | Villa Fresco Sangiovese - Bottle | unit clash bottle vs ml |

## 2. One supplier code, two+ Lightspeed products

Mostly seed-era stubs (blank venue, truncated names) vs real BO records, plus
real [Bottle]/[House] splits. For each purchasable: which PID is the canonical
ingredient? (Stub PIDs can then be mapped onto the real one, unifying history.)

| purchasable | candidate PID | name | venue |
|---|---|---|---|
| `fresh-fruit-team:OSKG` | `lightspeed:20445597` | Spanish Onion | stowaway |
| `fresh-fruit-team:OSKG` | `lightspeed:22995945` | Onion Spanish [10kg] | stowaway |
| `ilg:115-1844` | `lightspeed:20445705` | Asahi 3.5% | stowaway |
| `ilg:115-1844` | `lightspeed:20691651` | Asahi 3.5% [HG] | harry_gatos |
| `ilg:115-3762` | `lightspeed:20445701` | Corona | stowaway |
| `ilg:115-3762` | `lightspeed:20691646` | Corona | stowaway |
| `ilg:117-4213` | `lightspeed:20445707` | Heaps Normal Tin | stowaway |
| `ilg:117-4213` | `lightspeed:20691638` | Heaps Normal Quiet XPA | stowaway |
| `ilg:175-0420` | `lightspeed:20445890` | Antica Formula Rosso Vermo | — |
| `ilg:175-0420` | `lightspeed:20484285` | Antica Formula Rosso Vermouth [Bottle] | stowaway |
| `ilg:285-0132P` | `lightspeed:20445815` | Four Pillars Rare Dry | — |
| `ilg:285-0132P` | `lightspeed:20487294` | Four Pillars Rare Dry 700ML | stowaway |
| `ilg:285-1480` | `lightspeed:20445814` | Four Pillars Olive Leaf | — |
| `ilg:285-1480` | `lightspeed:20487289` | Four Pillars Olive Leaf 700ML | stowaway |
| `ilg:295-3922` | `lightspeed:20445839` | Yamazaki 12yr [Bottle] 700 | — |
| `ilg:295-3922` | `lightspeed:20492751` | Yamazaki 12yr 700ML | stowaway |
| `ilg:300-0219` | `lightspeed:20445855` | Monkey Shoulder Scotch [Bo | — |
| `ilg:300-0219` | `lightspeed:20744462` | Monkey Shoulder Scotch [Bottle] | — |
| `ilg:300-0219P` | `lightspeed:20445855` | Monkey Shoulder Scotch [Bo | — |
| `ilg:300-0219P` | `lightspeed:20487836` | Monkey Shoulder Scotch 700ML | stowaway |
| `ilg:300-1726` | `lightspeed:20445841` | Macallan Sherry Oak 12yr [ | — |
| `ilg:300-1726` | `lightspeed:20487567` | Macallan Sherry Oak 12yr 700ML | stowaway |
| `ilg:300-6507P` | `lightspeed:20445846` | Lagavulin 16yr [Bottle] 70 | — |
| `ilg:300-6507P` | `lightspeed:20487534` | Lagavulin 16yr 700ML | stowaway |
| `ilg:305-1949P` | `lightspeed:20445852` | Buffalo Trace Bourbon [House] | — |
| `ilg:305-1949P` | `lightspeed:20484751` | Buffalo Trace Bourbon [Bottle] | stowaway |
| `ilg:345-0401` | `lightspeed:20445860` | Appleton Rum [House] | stowaway |
| `ilg:345-0401` | `lightspeed:20487949` | Appleton Rum [Bottle] | stowaway |
| `ilg:345-5638P` | `lightspeed:20445864` | Sailor Jerry [House] | — |
| `ilg:345-5638P` | `lightspeed:20484932` | Sailor Jerry [700ml] | stowaway |
| `ilg:360-1310` | `lightspeed:20445833` | Rooster Rojo Blanco Tequil | — |
| `ilg:360-1310` | `lightspeed:20483410` | Rooster Rojo Blanco Tequila [Bottle] | stowaway |
| `ilg:360-1524P` | `lightspeed:20445824` | 1800 Coconut [Bottle] 700m | — |
| `ilg:360-1524P` | `lightspeed:20484243` | 1800 Coconut 700ML | stowaway |
| `ilg:360-2514` | `lightspeed:20445826` | Herradura Plata [Bottle] 7 | — |
| `ilg:360-2514` | `lightspeed:20487484` | Herradura Plata 700ML | stowaway |
| `ilg:360-3194P` | `lightspeed:20487923` | Patron Reposado 700ML | stowaway |
| `ilg:360-3194P` | `lightspeed:20744466` | Patron Reposado 700ML | stowaway |
| `ilg:395-6785P` | `lightspeed:20445895` | Aperol | — |
| `ilg:395-6785P` | `lightspeed:20484286` | Aperol [Bottle] | stowaway |
| `ilg:405-0957` | `lightspeed:20445870` | Mr Black Coffee Liqueur 700ml [700ml] | stowaway |
| `ilg:405-0957` | `lightspeed:20487788` | Mr Black Coffee Liqueur 700ML | stowaway |
| `ilg:405-0957` | `lightspeed:20744464` | Mr Black Coffee Liqueur 700ML | stowaway |
| `ilg:410-3552` | `lightspeed:20492689` | Domaine de Canton Ginger Liqueur 700ML | stowaway |
| `ilg:410-3552` | `lightspeed:20750141` | Domaine de Canton Ginger Liqueur 700ML | harry_gatos |
| `ilg:410-4472` | `lightspeed:20445871` | Grand Marnier [Bottle] 700 | — |
| `ilg:410-4472` | `lightspeed:20487225` | Grand Marnier [Bottle] | — |
| `ilg:460-4128` | `lightspeed:20445917` | Bundaberg Ginger Beer 750ml [750ml] | stowaway |
| `ilg:460-4128` | `lightspeed:20485266` | Bundaberg Ginger Beer 750ML | stowaway |
| `ilg:460-4128` | `lightspeed:20750267` | Bundaberg Ginger beer 750ML | stowaway |
| `paramount:10018402` | `lightspeed:20445807` | Never Never Oyster Shell Gin | stowaway |
| `paramount:10018402` | `lightspeed:20487829` | Never Never Oyster Shell Gin 700ML | stowaway |
| `paramount:10018402` | `lightspeed:20750175` | Never Never Oyster Shell Gin 700ML | stowaway |
| `paramount:66366` | `lightspeed:20487270` | Angostura Bitters [200ml] | stowaway |
| `paramount:66366` | `lightspeed:20747514` | Angostura Bitters 200ml 200ML | stowaway |
| `paramount:68140` | `lightspeed:20445860` | Appleton Rum [House] | stowaway |
| `paramount:68140` | `lightspeed:20487949` | Appleton Rum [Bottle] | stowaway |
| `paramount:78607` | `lightspeed:20445812` | Wolf Lane Navy Strength Gin | — |
| `paramount:78607` | `lightspeed:20492747` | Wolf Lane Navy Gin [Bottle] | — |

## 3. Pack sizes still unconfirmed

Already live on the flags tab (`/recipes/#flags`) and in the builder's
'confirm pack' prompt — that stays the canonical queue for pack answers,
per Zak's 'keep the flags on the module' rule. Biggest dollars: the be-foods
frozen-goods boxes (~$14,600 across 6 SKUs).


> **2026-08-16 update:** review applied. Conflicts: **0 remain** (all 28 resolved to stock-item anchors, recorded in `conflict_resolutions.csv`). Held pile now **18** — every one a unit clash awaiting the declared-conversion layer; the zak-confirmed identities among them auto-apply when it lands.
