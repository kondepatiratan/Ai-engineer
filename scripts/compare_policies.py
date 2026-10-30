"""Compare policies on September 2026 in Rs, using real outcomes.

    python scripts/compare_policies.py

Policies:
  * Today              : ship every COD order
  * Ops (strict)       : cancel any order with risk >= 40%
  * Ops (block areas)  : stop COD in the 5 worst cities (cancel those orders)
  * Marketing          : ship everything, never cancel
  * RTO Guard          : our rules from config/rules.yaml
Also shows how RTO Guard's result moves if our WhatsApp/prepaid assumptions are wrong.
"""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from rto import config, economics
from rto.clean import load_orders
from rto.config import ROOT
from scripts.backtest import run

cfg = config.load()
MONTH = "2026-09"
bt = run(MONTH, cfg=cfg, verbose=False)
df = load_orders(ROOT / "data" / "orders_sample.csv")
hist = df[df.order_datetime < MONTH + "-01"]
worst = hist.groupby("city").filter(lambda g: len(g) >= 20).groupby("city").is_rto.mean().nlargest(5).index.tolist()


def total(actions: pd.Series, c=cfg) -> dict:
    s = l = k = 0.0
    for (_, r), a in zip(bt.iterrows(), actions):
        i = economics.impact(a, 1.0 if r.actual == "RTO" else 0.0, r.order_value, bool(r.new_customer), c)
        s, l, k = s + i["saved"], l + i["lost"], k + i["cost"]
    cancelled = int((actions == "hold").sum())
    genuine_cancelled = int(((actions == "hold") & (bt.actual != "RTO")).sum())
    return {"saved": s, "lost": l, "costs": k, "net": s - l - k, "orders cancelled": cancelled,
            "genuine buyers cancelled": genuine_cancelled}


policies = {
    "Today (ship all)": pd.Series(["ship"] * len(bt)),
    "Ops: cancel if risk >= 40%": bt.risk.ge(0.40).map({True: "hold", False: "ship"}),
    f"Ops: no COD in {', '.join(worst)}": bt.city.isin(worst).map({True: "hold", False: "ship"}),
    "RTO Guard (recommended)": bt.action,
}
res = pd.DataFrame({k: total(v.reset_index(drop=True)) for k, v in policies.items()}).T
print(f"September 2026, {len(bt)} COD orders, {int((bt.actual=='RTO').sum())} real RTOs\n")
print(res.round(0).astype(int).to_string())

print("\nSame comparison if a cancelled first-time buyer is worth Rs 0 later (ignore Marketing's CAC point):")
c0 = copy.deepcopy(cfg)
c0["money"]["new_customer_future_value"] = 0
res0 = pd.DataFrame({k: total(v.reset_index(drop=True), c0) for k, v in policies.items()}).T
print(res0[["net", "orders cancelled"]].round(0).astype(int).to_string())

print("\nRTO Guard net if our assumptions are wrong:")
for label, caught, drop, take in [("pessimistic", 25, 6, 10), ("base", 40, 3, 20), ("optimistic", 55, 1, 30)]:
    c = copy.deepcopy(cfg)
    c["whatsapp"]["rto_caught_share"], c["whatsapp"]["genuine_dropoff"] = caught, drop
    c["prepaid_nudge"]["take_up_rate"] = take
    r = total(bt.action.reset_index(drop=True), c)
    print(f"  {label:12s} (caught {caught}%, genuine drop-off {drop}%, prepaid take-up {take}%): net Rs {r['net']:,.0f}")

R = config.rto_cost(cfg)
print(f"\nBreak-even risk for cancelling a Rs 649 order: "
      f"repeat {(0.62*649)/(0.62*649+R):.0%}, new {(0.62*649+300)/(0.62*649+300+R):.0%}")

# Fairer version of "stop COD in bad areas": some buyers would pay online instead.
blk = policies[f"Ops: no COD in {', '.join(worst)}"].reset_index(drop=True)
r = total(blk)
for share in (1 / 3, 1 / 2):
    print(f"No-COD areas, if {share:.0%} of genuine buyers there switch to prepaid: net Rs {r['net'] + share * r['lost']:,.0f}")
