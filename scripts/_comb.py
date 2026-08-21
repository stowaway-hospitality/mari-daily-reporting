#!/usr/bin/env python3
"""Fine-tooth comb: every open coverage item vs every invoiced identity.
A match requires BOTH a name-token overlap AND a rate agreement — rate
coincidence alone is the trap this repo has named four times."""
import sys, csv, re
from collections import defaultdict
sys.path.insert(0, '.'); sys.path.insert(0, 'scripts')
from check_invoice_coverage import findings

STOP = {'the','and','of','in','a','with','fresh','frozen','fz','pack','box','carton',
        'bottle','can','tin','keg','ea','kg','g','ml','l','lt','ltr','gm','x','per',
        'plain','whole','mixed','large','small','medium','regular','style','premium'}
def toks(s):
    return {t for t in re.split(r'[^a-z0-9]+', s.lower()) if len(t) > 2 and t not in STOP and not t.isdigit()}

# seeds + latest invoice rate per identity
seeds = {}
latest = {}
for r in csv.DictReader(open('data/costs.csv', encoding='utf-8-sig')):
    k = r['ingredient']
    if 'seed' in r['source_invoice'].lower():
        # keep the ls-recipe seed if present, else bo
        cur = seeds.get(k)
        if cur is None or 'ls-recipe' in r['source_invoice']:
            seeds[k] = (float(r['cost_per_unit']), r['unit'], r['source_invoice'])
    else:
        cur = latest.get(k)
        if cur is None or r['observed_on'] > cur[0]:
            latest[k] = (r['observed_on'], float(r['cost_per_unit']), r['unit'],
                         (r['description'] or ''), r['source_invoice'])

# already-bridged codes (don't resuggest)
bridged_to = defaultdict(set)
for r in csv.DictReader(open('data/product_map.csv', encoding='utf-8-sig')):
    pass

open_items = findings()
print(f"open items: {len(open_items)}; invoiced identities: {len(latest)}")
hits = []
for e in open_items:
    pid = e['id']; name = e['name']
    nt = toks(name)
    sd = seeds.get(pid)
    for iid, (dt, rate, unit, desc, inv) in latest.items():
        if iid.startswith('lightspeed:'):
            continue
        ov = nt & toks(desc)
        if not ov:
            continue
        score = len(ov)
        ratio = None
        if sd and sd[0] > 0 and rate > 0 and sd[1] == unit:
            ratio = rate / sd[0]
        hits.append((score, name, pid, iid, desc[:44], rate, unit, dt, ratio, sd))

hits.sort(key=lambda h: (-h[0], h[1]))
seen = set()
for score, name, pid, iid, desc, rate, unit, dt, ratio, sd in hits:
    key = (pid, iid)
    if key in seen: continue
    seen.add(key)
    rtxt = f"ratio={ratio:.3f}" if ratio else f"seed={sd[0] if sd else '?'}/{sd[1] if sd else '?'} vs {rate}/{unit}"
    flag = " ***" if ratio and 0.5 < ratio < 2.0 else ""
    print(f"[{score}] {name[:42]:<44} <- {iid:<30} {desc:<46} {rate:>9}/{unit:<4} {dt} {rtxt}{flag}")
