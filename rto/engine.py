"""The decision engine: one COD order in -> one action + a plain-English reason.

Order of checks:
  1. Hard rules from config (fake name + junk address, 2+ past RTOs, ...)
  2. Risk bands from config (separate bands for new vs repeat customers)
The reason is built from the model's biggest drivers, in plain words.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from . import config as cfgmod
from . import economics
from .model import RiskModel

LABELS = {
    "ship": "Ship as-is",
    "confirm": "Ship after WhatsApp confirmation",
    "nudge": "Nudge to prepaid",
    "hold": "Hold / cancel",
}
ACTION_ALIASES = {"nudge_prepaid": "nudge", "cancel": "hold"}


@dataclass
class Decision:
    order_id: str
    action: str
    label: str
    risk: float
    reason: str
    rule: str                 # which hard rule or band fired
    issues: list
    check_source: str         # "rules" or "llm"
    saved: float
    lost: float
    cost: float

    def to_dict(self) -> dict:
        return asdict(self)


def _band_action(risk_pct: float, bands: dict) -> str:
    if risk_pct >= bands["hold_from"]:
        return "hold"
    if risk_pct >= bands["nudge_from"]:
        return "nudge"
    if risk_pct >= bands["confirm_from"]:
        return "confirm"
    return "ship"


def _join(parts: list[str]) -> str:
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


class Engine:
    def __init__(self, model: RiskModel | None = None, cfg: dict | None = None, use_llm: bool = True):
        self.model = model or RiskModel.load()
        self.cfg = cfg or cfgmod.load()
        self.use_llm = use_llm

    def reload_config(self) -> None:
        self.cfg = cfgmod.load()

    def decide(self, order: dict) -> Decision:
        cfg = self.cfg
        pred = self.model.predict(order, use_llm=self.use_llm)
        p, f, q = pred["risk"], pred["features"], pred["quality"]
        risk_pct = round(p * 100)
        new = int(order.get("customer_prior_orders", 0) or 0) == 0
        prior_rto = int(order.get("customer_prior_rto", 0) or 0)

        rules = cfg["hard_rules"]
        action, rule = None, None
        checks = [
            ("fake_name_and_junk_address", q.name == "fake" and q.address == "junk"),
            ("invalid_phone", not q.phone_valid),
            ("two_or_more_past_rtos", prior_rto >= 2),
            ("junk_address", q.address == "junk"),
        ]
        for name, hit in checks:
            r = rules.get(name, {})
            if hit and r.get("enabled", False):
                action, rule = ACTION_ALIASES.get(r["action"], r["action"]), name
                break

        bands = cfg["risk_bands"]["new_customer" if new else "repeat_customer"]
        band_action = _band_action(risk_pct, bands)
        if action is None:
            action, rule = band_action, f"risk band ({'new' if new else 'repeat'} customer)"
        elif action == "confirm" and band_action in ("nudge", "hold"):
            # a soft hard-rule should never make a risky order *less* protected
            action = band_action

        ups, downs = self.model.explain(order, pred)
        why = {
            "ship": _join(downs) or "nothing unusual about this order",
            "confirm": _join(ups) or "risk is moderate",
            "nudge": _join(ups) or "risk is high",
            "hold": _join(ups) or "risk is very high",
        }[action]
        tail = {
            "ship": "",
            "confirm": " A quick WhatsApp yes costs far less than a wasted delivery.",
            "nudge": " Offer a small discount to pay online; if they decline, confirm COD on WhatsApp.",
            "hold": " Don't ship until someone has spoken to the customer.",
        }[action]
        if rule == "fake_name_and_junk_address":
            why = f"name '{order.get('customer_name','')}' and address '{order.get('address','')}' look fake"
        elif rule == "invalid_phone":
            why = f"phone '{order.get('phone','')}' is not a valid mobile, so we can't confirm on WhatsApp"
        elif rule == "junk_address" and action == "confirm":
            why = f"address '{order.get('address','')}' is too vague for a courier"
            tail = " Ask the customer for their full address on WhatsApp before shipping."
        if new and action in ("confirm", "nudge") and rule.startswith("risk band"):
            tail += " First-time buyer, so we don't cancel."
        reason = f"{risk_pct}% risk: {why}.{tail}".strip()
        reason = reason[0].upper() + reason[1:]

        money = economics.impact(action, p, float(order.get("order_value", 0) or 0), new, cfg)
        return Decision(
            order_id=str(order.get("order_id", "")),
            action=action,
            label=LABELS[action],
            risk=p,
            reason=reason,
            rule=rule,
            issues=q.issues,
            check_source=q.source,
            saved=money["saved"],
            lost=money["lost"],
            cost=money["cost"],
        )
