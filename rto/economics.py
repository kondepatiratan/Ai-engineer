"""Rs impact of each action, compared with today's default (ship every COD order).

For one order with RTO risk p, order value V:
    R  = cost of one RTO (shipping both ways + packing + damaged stock)
    M  = gross margin on the order if delivered (62% of V)
    F  = future value lost if we turn away a genuine new customer

  Hold / cancel      saved = p*R              (RTOs we no longer ship)
                     lost  = (1-p)*(M + F)    (genuine buyers we turned away)

  WhatsApp confirm   saved = p*c*R            c = share of would-be RTOs who say no / never reply
                     lost  = (1-p)*g*(M + F)  g = genuine buyers who drop off at the extra step
                     cost  = messages

  Prepaid nudge      t = share who switch to prepaid. Switchers are assumed to be
                     only a THIRD as risky as the order's p (people who pay upfront
                     self-select as genuine), so we don't over-claim. For switchers:
                       saved = (p/3 - 0.04) * (R + M)   avoided RTO + the sale itself is kept
                       cost  = discount + gateway fee - courier COD fee we no longer pay
                     Everyone who does not switch goes through WhatsApp confirm.
"""
from __future__ import annotations

from .config import rto_cost

COD_FEE = 30  # Rs courier COD remittance fee avoided when an order is prepaid (check invoice)


def discount_for(order_value: float, cfg: dict) -> float:
    n = cfg["prepaid_nudge"]
    return min(n["discount_rs"], n["max_discount_pct"] / 100 * order_value)


def impact(action: str, p: float, value: float, new_customer: bool, cfg: dict) -> dict:
    R = rto_cost(cfg)
    M = cfg["money"]["gross_margin"] / 100 * value
    F = cfg["money"]["new_customer_future_value"] if new_customer else 0.0
    w = cfg["whatsapp"]
    c, g, msg = w["rto_caught_share"] / 100, w["genuine_dropoff"] / 100, w["message_cost"]

    if action == "ship":
        return {"saved": 0.0, "lost": 0.0, "cost": 0.0}
    if action == "hold":
        return {"saved": p * R, "lost": (1 - p) * (M + F), "cost": 0.0}

    def confirm(pp: float) -> dict:
        return {"saved": pp * c * R, "lost": (1 - pp) * g * (M + F), "cost": 2 * msg}

    if action == "confirm":
        return confirm(p)
    if action == "nudge":
        n = cfg["prepaid_nudge"]
        t = n["take_up_rate"] / 100
        p_prepaid = n["prepaid_rto_rate"] / 100
        p_switch = p / 3
        p_rest = min((p - t * p_switch) / (1 - t), 1.0)
        d = discount_for(value, cfg)
        fee = n["payment_gateway_fee"] / 100 * value
        rest = confirm(p_rest)
        return {
            "saved": t * max(p_switch - p_prepaid, 0) * (R + M) + (1 - t) * rest["saved"],
            "lost": (1 - t) * rest["lost"],
            "cost": t * (d + fee - COD_FEE) + (1 - t) * rest["cost"] + msg,
        }
    raise ValueError(action)


def net(action: str, p: float, value: float, new_customer: bool, cfg: dict) -> float:
    i = impact(action, p, value, new_customer, cfg)
    return i["saved"] - i["lost"] - i["cost"]


def best_action(p: float, value: float, new_customer: bool, cfg: dict) -> str:
    return max(["ship", "confirm", "nudge", "hold"], key=lambda a: net(a, p, value, new_customer, cfg))
