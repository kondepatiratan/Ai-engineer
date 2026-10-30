"""RTO risk model: plain logistic regression on a handful of readable signals.

Why logistic regression and not a bigger model: ~2,000 orders is small, the
founder needs to trust it, and every coefficient maps to a sentence an ops
person can read ("Bihar orders are refused more often"). It is also exactly
what produces the plain-English reasons.
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from .checks import check_order
from .config import ROOT

MODEL_PATH = ROOT / "models" / "rto_model.pkl"
SMOOTHING = 20  # orders of "prior weight" when estimating a city's RTO rate

FEATURES = [
    "area_risk",          # log-odds of RTO in this city (smoothed)
    "repeat_clean",       # bought before and never refused (baseline = new customer)       # bought before and never refused
    "past_rto_1",
    "past_rto_2plus",
    "address_junk",
    "address_landmark_only",
    "phone_invalid",
    "high_value",         # order value > Rs 2,000
    "four_plus_items",
    "from_meta_ads",
]

# name_fake and pin_city_mismatch are NOT model inputs: too few examples (7 and 4
# orders on their own) to learn a stable weight. They are handled by hard rules
# and shown as issues instead. new_customer is left out because it is the
# baseline of the repeat/past-RTO group (keeping it makes the weights unstable).

# Plain-English phrases used in reasons. (raises risk, lowers risk)
PHRASES = {
    "area_risk": ("deliveries to {city} are often refused (~{city_rate:.0%} of orders)",
                  "{city} has a good delivery record"),
    "new_customer": ("first order from this customer", ""),
    "repeat_clean": ("", "repeat customer who has always accepted delivery"),
    "past_rto_1": ("customer refused 1 earlier COD order", ""),
    "past_rto_2plus": ("customer refused {prior_rto} earlier COD orders", ""),
    "address_junk": ("address is too vague for a courier to find", ""),
    "address_landmark_only": ("address has only landmarks, no house number", ""),
    "name_fake": ("name looks fake or like a placeholder", ""),
    "phone_invalid": ("phone number is not a valid mobile", ""),
    "pin_city_mismatch": ("pincode does not match the city", ""),
    "high_value": ("unusually large COD order (Rs {order_value:,.0f})", ""),
    "four_plus_items": ("4 or more items in one COD order", ""),
    "from_meta_ads": ("came from a Meta ad (higher refusal rate)", ""),
}


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return float(np.log(p / (1 - p)))


@dataclass
class RiskModel:
    clf: LogisticRegression
    city_rate: dict
    state_rate: dict
    global_rate: float
    pin_prefix_by_city: dict
    feature_means: np.ndarray

    # ---------- features ----------
    def area_rate(self, city: str, state: str) -> float:
        if city in self.city_rate:
            return self.city_rate[city]
        if state in self.state_rate:
            return self.state_rate[state]
        return self.global_rate

    def features(self, order: dict, use_llm: bool = True) -> tuple[dict, object]:
        q = check_order(order, self.pin_prefix_by_city, use_llm=use_llm)
        prior_orders = int(order.get("customer_prior_orders", 0) or 0)
        prior_rto = int(order.get("customer_prior_rto", 0) or 0)
        f = {
            "area_risk": _logit(self.area_rate(order.get("city", ""), order.get("state", ""))),
            "new_customer": int(prior_orders == 0),
            "repeat_clean": int(prior_orders > 0 and prior_rto == 0),
            "past_rto_1": int(prior_rto == 1),
            "past_rto_2plus": int(prior_rto >= 2),
            "address_junk": int(q.address == "junk"),
            "address_landmark_only": int(q.address == "landmark_only"),
            "name_fake": int(q.name == "fake"),
            "phone_invalid": int(not q.phone_valid),
            "pin_city_mismatch": int(q.pin_city_mismatch),
            "high_value": int(float(order.get("order_value", 0) or 0) > 2000),
            "four_plus_items": int(int(order.get("num_items", 1) or 1) >= 4),
            "from_meta_ads": int(order.get("utm_source", "") == "meta_ads"),
        }
        return f, q

    # ---------- scoring ----------
    def predict(self, order: dict, use_llm: bool = True) -> dict:
        f, q = self.features(order, use_llm)
        x = np.array([[f[k] for k in FEATURES]], dtype=float)
        p = float(self.clf.predict_proba(x)[0, 1])
        # contribution of each signal vs. an average order (in log-odds)
        contrib = self.clf.coef_[0] * (x[0] - self.feature_means)
        return {"risk": p, "features": f, "quality": q, "contrib": dict(zip(FEATURES, contrib))}

    def explain(self, order: dict, pred: dict, top: int = 2) -> tuple[list[str], list[str]]:
        """Return (risk-raising phrases, risk-lowering phrases), biggest first."""
        ctx = {
            "city": order.get("city") or order.get("state") or "this area",
            "city_rate": self.area_rate(order.get("city", ""), order.get("state", "")),
            "prior_rto": order.get("customer_prior_rto", 0),
            "order_value": float(order.get("order_value", 0) or 0),
        }
        ups, downs = [], []
        for k, c in sorted(pred["contrib"].items(), key=lambda kv: -abs(kv[1])):
            up, down = PHRASES[k]
            if c > 0.15 and up and (k != "area_risk" or ctx["city_rate"] > self.global_rate):
                ups.append(up.format(**ctx))
            elif c < -0.15 and down and (k != "area_risk" or ctx["city_rate"] < self.global_rate):
                downs.append(down.format(**ctx))
        return ups[:top], downs[:top]

    def save(self, path=MODEL_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @staticmethod
    def load(path=MODEL_PATH) -> "RiskModel":
        if not path.exists():  # first run: train from the sample data (takes ~2 seconds)
            from .clean import load_orders
            m = train(load_orders(ROOT / "data" / "orders_sample.csv"))
            m.save(path)
            return m
        with open(path, "rb") as f:
            return pickle.load(f)


def _smoothed(df: pd.DataFrame, col: str, prior: float) -> dict:
    g = df.groupby(col)["is_rto"].agg(["sum", "count"])
    return ((g["sum"] + SMOOTHING * prior) / (g["count"] + SMOOTHING)).to_dict()


def train(df: pd.DataFrame, use_llm: bool = False) -> RiskModel:
    """Fit on cleaned historical orders (DELIVERED / RTO only)."""
    global_rate = float(df["is_rto"].mean())
    state_rate = _smoothed(df[df.state != ""], "state", global_rate)
    city_rate = _smoothed(df[df.city != ""], "city", global_rate)
    pins = df[(df.city != "") & (df.pincode.str.len() == 6)]
    pin_prefix = pins.groupby("city")["pincode"].agg(lambda s: s.str[:3].mode().iloc[0]).to_dict()

    m = RiskModel(LogisticRegression(C=1.0, max_iter=1000), city_rate, state_rate, global_rate,
                  pin_prefix, np.zeros(len(FEATURES)))
    rows = [m.features(r, use_llm)[0] for r in df.to_dict("records")]
    X = np.array([[r[k] for k in FEATURES] for r in rows], dtype=float)
    m.clf.fit(X, df["is_rto"].values)
    m.feature_means = X.mean(axis=0)
    return m


def score_frame(m: RiskModel, df: pd.DataFrame, use_llm: bool = False) -> pd.Series:
    return pd.Series([m.predict(r, use_llm)["risk"] for r in df.to_dict("records")], index=df.index)
