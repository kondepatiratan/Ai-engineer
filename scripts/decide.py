"""Decide one order (JSON) or a whole CSV of new COD orders.

    python scripts/decide.py --json '{"customer_name":"asdf","address":".","city":"Patna", ...}'
    python scripts/decide.py --csv new_orders.csv --out decisions.csv
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from rto.clean import normalise_phone
from rto.engine import Engine

ap = argparse.ArgumentParser()
ap.add_argument("--json")
ap.add_argument("--csv")
ap.add_argument("--out", default="decisions.csv")
ap.add_argument("--no-llm", action="store_true")
a = ap.parse_args()

eng = Engine(use_llm=not a.no_llm)
if a.json:
    o = json.loads(a.json)
    o["phone"] = normalise_phone(o.get("phone", ""))
    d = eng.decide(o)
    print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in d.to_dict().items()},
                     indent=2, ensure_ascii=False))
elif a.csv:
    df = pd.read_csv(a.csv, dtype={"phone": str, "pincode": str}).fillna("")
    df["phone"] = df["phone"].map(normalise_phone)
    out = pd.DataFrame([eng.decide(o).to_dict() for o in df.to_dict("records")])
    out.to_csv(a.out, index=False)
    print(out.label.value_counts().to_string())
    print(f"Wrote {a.out}")
else:
    ap.print_help()
