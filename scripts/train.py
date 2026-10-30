"""Train the RTO risk model and save it to models/rto_model.pkl.

    python scripts/train.py                 # train on all history (production)
    python scripts/train.py --until 2026-09-01   # train only on orders before a date
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from sklearn.metrics import roc_auc_score

from rto.clean import load_orders
from rto.config import ROOT
from rto.model import FEATURES, train

ap = argparse.ArgumentParser()
ap.add_argument("--data", default=str(ROOT / "data" / "orders_sample.csv"))
ap.add_argument("--until", default=None, help="only use orders before this date (YYYY-MM-DD)")
ap.add_argument("--llm", action="store_true", help="use the LLM for address/name checks while training")
args = ap.parse_args()

df = load_orders(args.data)
if args.until:
    df = df[df.order_datetime < args.until]
print(f"Training on {len(df)} resolved COD orders, RTO rate {df.is_rto.mean():.1%}")
m = train(df, use_llm=args.llm)
m.save()

X = np.array([[m.features(r, False)[0][k] for k in FEATURES] for r in df.to_dict("records")])
print(f"In-sample AUC: {roc_auc_score(df.is_rto, m.clf.predict_proba(X)[:, 1]):.3f}")
print("Signal weights (log-odds, + means riskier):")
for f, c in sorted(zip(FEATURES, m.clf.coef_[0]), key=lambda t: -abs(t[1])):
    print(f"  {f:24s} {c:+.2f}")
print("Saved models/rto_model.pkl")
