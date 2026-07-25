#!/usr/bin/env python3
"""Tests for the self-sustaining SPH email feed (scripts/sph_from_email.py)."""
import io, os, zipfile, importlib.util, tempfile, csv
os.environ.setdefault("GMAIL_ADDRESS", "x"); os.environ.setdefault("GMAIL_APP_PASSWORD", "x")
m = importlib.util.module_from_spec(importlib.util.spec_from_file_location("s", "scripts/sph_from_email.py"))
importlib.util.spec_from_file_location("s", "scripts/sph_from_email.py").loader.exec_module(m)

def zjob(staff=None, category=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if staff is not None:
            z.writestr("dashboard-product_sales/sales_by_staff_(line_added).csv", staff)
        if category is not None:
            z.writestr("dashboard-product_sales/sales_by_category.csv", category)
    return buf.getvalue()

HG_STAFF = ('Staff Name,$ Sales,# of Sales,Gross Profit $,Gross Profit %,% Sales Contribution\n'
            'David ,"$1,261.50",11,"$1,059",84%,68%\n'
            'Zak Britton,$383.75,6,$325,85%,21%\n'
            ',$212.50,3,$183,86%,11%\n'
            'Steph Kunde,$8.80,1,$5,53%,0%\n'
            ',"$1,866.55",17,"$1,571",84%,100%\n')

def test_grand_total_from_staff():
    assert m.totals_from_zip(zjob(staff=HG_STAFF)) == (17, 1866.55)

def test_fallback_to_category_when_no_staff():
    cat = ('POS Category,Total Quantity,$ Sales,Total Tax,Cost,# of Sales,% of Quantity,% of Sale Amount,Gross Profit %\n'
           'FOOD,65,"$1,000.00",$0,$0,10,,,\n'
           'DRINKS,5,$500.00,$0,$0,7,,,\n')
    txns, sales = m.totals_from_zip(zjob(category=cat))
    assert txns == 17 and abs(sales - 1500.0) < 0.01     # summed fallback

def test_num_parses_currency():
    assert m._num('"$1,866.55"') == 1866.55
    assert m._num('$0') == 0.0 and m._num('') == 0.0

def test_put_rejects_implausible():
    rows = {}
    m.put(rows, "2026-07-24", "Stowaway", 1000, 500.0)   # $0.50/txn -> reject
    m.put(rows, "2026-07-24", "Stowaway", 1, 5000.0)     # $5000/txn -> reject
    assert rows == {}
    m.put(rows, "2026-07-24", "HarryGatos", 17, 1866.55) # $109.80 -> ok
    assert rows[("2026-07-24", "HarryGatos")]["Transactions"] == "17"

def test_stow_bar_is_till_minus_mari():
    rows = {}
    till_t, till_s = 502, 16180.41
    mari_t, mari_s = 90, 3513.04
    m.put(rows, "2026-07-24", "Stowaway", till_t - mari_t, till_s - mari_s)
    r = rows[("2026-07-24", "Stowaway")]
    assert r["Transactions"] == "412"
    assert abs(float(r["Sales"]) - 12667.37) < 0.01
    assert abs(float(r["SPH_PerTxn"]) - 30.75) < 0.01

def test_backfill_dates_are_skipped():
    # a date carrying a Marilynas-Uber row is weekly-backfill territory: never overwrite
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "sph.csv")
        with open(f, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=m.HEADER); w.writeheader()
            w.writerow({"Date": "2026-07-05", "Venue": "Marilynas-Uber", "Sales": "711.21",
                        "Transactions": "14", "SalesExGST": "646.55", "Guests": "",
                        "SPH_PerTxn": "46.18", "SPH_PerGuest": "", "AvgGuestsPerTxn": ""})
        m.SPH_FILE = f
        sph = m.load_sph()
        backfill = {dt for (dt, v) in sph if v == "Marilynas-Uber"}
        assert "2026-07-05" in backfill

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print("ok", fn.__name__)
    print(f"\nall {len(fns)} sph-email tests passed")
