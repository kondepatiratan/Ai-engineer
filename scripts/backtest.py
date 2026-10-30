"""Backtest: train on history before a month, then run the engine on that month
and compare with what actually happened.

    python scripts/backtest.py                     # train Apr-Aug, test September 2026
    python scripts/backtest.py --month 2026-09 --llm

Rs figures use the real outcome of each order:
  * Hold   : real RTO -> we saved one RTO cost; real delivery -> we lost margin (+ future value if new)
  * Confirm/Nudge : the WhatsApp/prepaid assumptions in config/rules.yaml applied to the real outcome
Writes reports/backtest_<month>.csv with every decision and reason.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from sklearn.metrics import roc_auc_score

from rto import config, economics
from rto.clean import load_orders
from rto.config import ROOT
from rto.engine import Engine, LABELS
from rto.model import train


def run(month: str, use_llm: bool = False, cfg: dict | None = None, verbose: bool = True) -> pd.DataFrame:
    cfg = cfg or config.load()
    df = load_orders(ROOT / "data" / "orders_sample.csv")
    start = pd.Timestamp(month + "-01")
    end = start + pd.offsets.MonthBegin(1)
    hist, test = df[df.order_datetime < start], df[(df.order_datetime >= start) & (df.order_datetime < end)]
    model = train(hist)
    eng = Engine(model, cfg, use_llm=use_llm)
    rows = []
    for o in test.to_dict("records"):
        d = eng.decide(o)
        new = o["customer_prior_orders"] == 0
        real = economics.impact(d.action, float(o["is_rto"]), float(o["order_value"]), new, cfg)
        rows.append({
            "order_id": o["order_id"], "date": o["order_date"], "city": o["city"],
            "customer_name": o["customer_name"], "address": o["address"], "order_value": o["order_value"],
            "new_customer": new, "actual": o["final_status"], "risk": round(d.risk, 3),
            "action": d.action, "decision": d.label, "reason": d.reason, "rule": d.rule,
            "saved": round(real["saved"], 1), "lost": round(real["lost"], 1), "cost": round(real["cost"], 1),
        })
    out = pd.DataFrame(rows)
    out["net"] = out.saved - out.lost - out.cost
    if verbose:
        print(f"Trained on {len(hist)} orders before {month}; testing on {len(out)} orders in {month}")
        print(f"Model AUC on {month}: {roc_auc_score(out.actual.eq('RTO'), out.risk):.3f}\n")
        s = out.groupby("action").agg(orders=("order_id", "size"), rto_rate=("actual", lambda a: (a == "RTO").mean()),
                                      saved=("saved", "sum"), lost=("lost", "sum"), cost=("cost", "sum"))
        s = s.reindex([a for a in LABELS if a in s.index])
        s.index = [LABELS[a] for a in s.index]
        print(s.round(2).to_string())
        print(f"\nSaved Rs {out.saved.sum():,.0f} | Lost Rs {out.lost.sum():,.0f} | "
              f"Costs Rs {out.cost.sum():,.0f} | NET Rs {out.net.sum():,.0f}")
        rto_bill = (out.actual == "RTO").sum() * config.rto_cost(cfg)
        print(f"(RTO bill without the system: Rs {rto_bill:,.0f} on {(out.actual=='RTO').sum()} RTOs)")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default="2026-09")
    ap.add_argument("--llm", action="store_true")
    a = ap.parse_args()
    res = run(a.month, a.llm)
    (ROOT / "reports").mkdir(exist_ok=True)
    path = ROOT / "reports" / f"backtest_{a.month}.csv"
    res.to_csv(path, index=False)
    print(f"\nWrote {path.relative_to(ROOT)}")
