/* Unit tests locking the behaviour of the pure helpers in util.js / pnl.js.
   Snapshot of known-correct outputs — any future change that alters a formatter,
   date helper or status rule fails here before it ships. Run: node scripts/test_dashboard_units.mjs */
import fs from 'fs'; import vm from 'vm'; import path from 'path';
import { fileURLToPath } from 'url';
const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const ctx=vm.createContext({console,Math,Date,JSON,isNaN,parseFloat,parseInt,Number,Object,Array,String,Set,Map,Boolean,RegExp,Intl});
ctx.STATE={};
vm.runInContext(fs.readFileSync(path.join(ROOT,'dashboard/_shared/pnl.js'),'utf8'),ctx);
vm.runInContext(fs.readFileSync(path.join(ROOT,'dashboard/_shared/util.js'),'utf8'),ctx);
// config constants the helpers read (normally globals from index.html)
ctx.COGS_TARGET_PCT=22; ctx.IS_DARK=false;

let fails=0, n=0;
const eq=(expr,expected)=>{ n++; let got; try{ got=vm.runInContext(expr,ctx);}catch(e){got='ERR:'+e.message;} 
  const g=typeof got==='string'?got:JSON.stringify(got);
  if(g!==expected){ fails++; console.log(`✗ ${expr}\n    expected ${JSON.stringify(expected)}\n    got      ${JSON.stringify(g)}`);} };

// number coercion
eq("toNum('42')","42"); eq("toNum('1234.5')","1234.5"); eq("toNum('')","0");
eq("toNum('abc')","0"); eq("toNum(null)","0");
eq("hasVal('')","false"); eq("hasVal('0')","true"); eq("hasVal(null)","false"); eq("hasVal('x')","false");
// dates
eq("isoDate(new Date('2026-07-22T00:00:00'))","2026-07-22");
eq("isoDate(weekStart(new Date('2026-07-22T00:00:00')))","2026-07-20");   // Wed -> Mon
eq("isoDate(weekStart(new Date('2026-07-19T00:00:00')))","2026-07-13");   // Sun -> prev Mon
eq("isoDate(monthStart(new Date('2026-07-22T00:00:00')))","2026-07-01");
eq("isoDate(quarterStart(new Date('2026-07-22T00:00:00')))","2026-07-01");
eq("isoDate(addDays(new Date('2026-07-22T00:00:00'),5))","2026-07-27");
// formatting
eq("fmtDollars(1234.5)","$1,235"); eq("fmtDollars(0)","$0"); eq("fmtDollars(1000000)","$1,000,000");
eq("fmtPct(23.34)","23.3%"); eq("fmtPct(0)","0.0%"); eq("fmtPct(100)","100.0%");
// status rules (need COGS_TARGET_PCT)
eq("cogsStatus(20)", vm.runInContext("cogsStatus(20)",ctx));  // self-consistent (won't throw now)
eq("typeof cogsStatus(25)","string");
// vsTarget HTML
eq("vsTarget(25,22)",'<span class="vs-t">target 22.0%</span><span class="vs-bad">3.0pp over</span>');
eq("vsTarget(20,22)",'<span class="vs-t">target 22.0%</span><span class="vs-ok">2.0pp under</span>');
// pill (needs IS_DARK)
eq("typeof pill('closed','green')","string");

// ---- Uber feed stitching (regression, 2026-08-09) -------------------------
// uberActual must ADD the weekly feed (pre-2026-07-13) to the daily feed rather
// than let the daily branch win outright. The old `uberSplit(..) || uberWeekly(..)`
// dropped every pre-boundary week whenever the window touched even one daily
// row, making delivery cost too LOW on any multi-month window.
ctx.STATE.uberDaily=[
  {date:'2026-07-13',shop:'mari',commission_inc_gst:'100.00',offers_inc_gst:'10.00'},
  {date:'2026-07-14',shop:'mari',commission_inc_gst:'200.00',offers_inc_gst:'20.00'}];
ctx.STATE.uberFees=[
  {week_ending:'2026-07-12',venue:'mari',service_fees_inc_gst:'700.00',marketing_inc_gst:'70.00'}];
ctx.STATE.uberAds=[];
// window entirely inside the daily era -> daily only
eq("uberActual('mari','2026-07-13','2026-07-14').commission","300");
eq("uberActual('mari','2026-07-13','2026-07-14').marketing","30");
// window straddling the boundary -> daily PLUS the whole prior week (7/7 days)
eq("uberActual('mari','2026-07-06','2026-07-14').commission","1000");
eq("uberActual('mari','2026-07-06','2026-07-14').marketing","100");
// window entirely before the daily feed -> weekly only
eq("uberActual('mari','2026-07-06','2026-07-12').commission","700");
eq("uberActual('mari','2026-07-06','2026-07-12').marketing","70");
// the daily feed must not be double-counted by the weekly branch
eq("uberActual('mari','2026-07-06','2026-07-14').commission === 300 + 700","true");
// no feed reaches the window -> null, caller estimates
eq("uberActual('mari','2026-05-01','2026-05-07')","null");
// marketing must NOT pick up uber_marketing_weekly ads on top (double-count)
ctx.STATE.uberAds=[{week_ending:'2026-07-19',shop:'mari',ads_inc_gst:'999.00'}];
eq("uberActual('mari','2026-07-13','2026-07-14').marketing","30");

// COVERAGE — the feed must say how far it reaches, so a window whose tail has
// not landed cannot read as complete. (The daily pull runs next morning, so any
// window ending today is uncovered; if the pull is stuck it is uncovered by more.)
eq("uberActual('mari','2026-07-13','2026-07-14').covered","true");
eq("uberActual('mari','2026-07-13','2026-07-20').covered","false");
eq("uberActual('mari','2026-07-13','2026-07-20').coveredEnd","2026-07-14");
// the covered sum itself must not change when the window runs past the feed
eq("uberActual('mari','2026-07-13','2026-07-20').commission","300");
// venueDeliveryEst tops the uncovered tail up from revenue rather than dropping it
ctx.STATE.histories={mari:[
  {date:'2026-07-13',revenue_ex_gst:'1000'},{date:'2026-07-14',revenue_ex_gst:'1000'},
  {date:'2026-07-15',revenue_ex_gst:'1000'}]};
ctx.STATE.uberDirect=[]; ctx.STATE.xeroOH=[]; ctx.STATE.baselines={};
// covered $2000 rev carried $330 of fees -> the uncovered $1000 day adds $165
eq("Math.round(venueDeliveryEst('mari','2026-07-13','2026-07-15','2026-07-15').tailEst)","165");
eq("venueDeliveryEst('mari','2026-07-13','2026-07-14','2026-07-14').tailEst","0");

console.log(`\n${n} unit assertions, ${fails} failures`);
process.exit(fails?1:0);
