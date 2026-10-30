"""WhatsApp confirmation + prepaid-nudge flow, with a mock WhatsApp API.

Two flows (full design in docs/whatsapp_flow.md):

CONFIRM (bucket "Ship after confirmation")
  t=0    send confirmation with 2 buttons [Yes, ship it] [Cancel order]
         (if the address is junk, we ask for the full address instead)
  yes    -> CONFIRMED, order released to courier
  no     -> CANCELLED by customer (+ one optional "why?" question)
  change address -> ask for it, re-check it, then confirm
  random -> one clarifying message with the buttons again; a second
            unclear reply goes to a human agent
  12h    -> one reminder
  24h    -> no reply: ship anyway if risk < no_reply_ship_if_risk_below,
            otherwise cancel with "reply anytime in 3 days to restore"

NUDGE (bucket "Nudge to prepaid")
  t=0    offer Rs X off to pay online, with a pay link (valid 6h)
         buttons [Pay online] [Keep COD]
  paid   -> PREPAID, ship
  keep COD -> counts as a confirmation (they engaged) -> CONFIRMED
  no reply in 6h -> switch to the CONFIRM flow above

Messages never go out between quiet hours (21:00-08:00); they queue to 08:00.
Reply understanding: keyword rules first (fast, free, covers ~90%); the LLM is
only asked when the keywords conflict or find nothing (e.g. "abhi nahi, kal
bhejna" or "no problem bhej do").
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import llm
from .config import ROOT

OUTBOX = ROOT / "data" / "whatsapp_outbox.jsonl"

T = {
    "english": {
        "confirm": "Hi {name}! 👋 Thanks for your Oytra order {order_id} of Rs {value} (Cash on Delivery).\n"
                   "Please confirm so we can ship it today:",
        "confirm_btn": ["✅ Yes, ship it", "❌ Cancel order"],
        "ask_address": "Hi {name}! Thanks for your Oytra order {order_id} (Rs {value}, COD). "
                       "The address we have is \"{address}\" - the courier may not find you. "
                       "Please reply with your full address (house no., street, area, landmark).",
        "nudge": "Hi {name}! 👋 Your Oytra order {order_id} of Rs {value} is ready.\n"
                 "Pay online now and get Rs {discount} OFF (you pay Rs {new_value}) + priority dispatch. "
                 "Offer valid for {hours} hours: {link}",
        "nudge_btn": ["💳 Pay online", "💵 Keep Cash on Delivery"],
        "confirmed": "Great, your order is confirmed and will be shipped shortly. 🚚",
        "paid": "Payment received - thank you! Your order ships today. 🚚",
        "cancelled": "Okay, we've cancelled order {order_id}. Could you tell us why? (optional) "
                     "1 Ordered by mistake  2 Price  3 Will buy later  4 Other",
        "clarify": "Sorry, we didn't get that. Please tap a button below 👇",
        "reminder": "Reminder: your Oytra order {order_id} (Rs {value}) is waiting for your confirmation.",
        "address_thanks": "Thanks! We've updated your address. Shall we ship the order?",
        "agent": "Thanks - one of our team will message you shortly.",
        "no_reply_cancel": "We couldn't reach you, so order {order_id} is on hold. "
                           "Reply YES within 3 days and we'll ship it right away.",
        "switch_to_cod": "No problem! Please confirm your Cash on Delivery order:",
    },
    "hinglish": {
        "confirm": "Namaste {name} ji! 👋 Aapka Oytra order {order_id} (Rs {value}, Cash on Delivery) mil gaya hai.\n"
                   "Kya hum ise aaj bhej dein?",
        "confirm_btn": ["✅ Haan, bhej do", "❌ Order cancel karo"],
        "ask_address": "Namaste {name} ji! Order {order_id} (Rs {value}, COD) ke liye dhanyavaad. "
                       "Aapka address \"{address}\" adhoora lag raha hai. Kripya pura address bhejein "
                       "(ghar no., gali, area, landmark).",
        "nudge": "Namaste {name} ji! 👋 Aapka Oytra order {order_id} (Rs {value}) ready hai.\n"
                 "Abhi online pay karein aur Rs {discount} ki CHHOOT paayein (sirf Rs {new_value}) + jaldi dispatch. "
                 "Offer {hours} ghante ke liye: {link}",
        "nudge_btn": ["💳 Online pay karein", "💵 Cash on Delivery hi rakhein"],
        "confirmed": "Dhanyavaad! Aapka order confirm ho gaya, jaldi bhej rahe hain. 🚚",
        "paid": "Payment mil gaya - dhanyavaad! Order aaj hi dispatch hoga. 🚚",
        "cancelled": "Theek hai, order {order_id} cancel kar diya. Kya wajah thi? (optional) "
                     "1 Galti se order hua  2 Keemat  3 Baad mein lenge  4 Kuch aur",
        "clarify": "Maaf kijiye, samajh nahi aaya. Kripya neeche button dabayein 👇",
        "reminder": "Yaad dilana: aapka Oytra order {order_id} (Rs {value}) confirmation ka intezaar kar raha hai.",
        "address_thanks": "Dhanyavaad! Address update ho gaya. Kya order bhej dein?",
        "agent": "Dhanyavaad - hamari team aapko jaldi message karegi.",
        "no_reply_cancel": "Aapse sampark nahi ho paaya, isliye order {order_id} hold par hai. "
                           "3 din ke andar HAAN likhein, hum turant bhej denge.",
        "switch_to_cod": "Koi baat nahi! Kripya apna Cash on Delivery order confirm karein:",
    },
}
T["hindi"] = T["hinglish"]  # Devanagari templates need approval as WhatsApp templates; Hinglish reads well on phones

YES = {"yes", "y", "haan", "han", "haa", "ha", "hn", "ji", "ok", "okay", "k", "confirm", "confirmed",
       "1", "sure", "theek", "thik", "done", "bhejo", "bhej", "👍", "✅", "yess", "yup", "yeah"}
NO = {"no", "n", "nahi", "nahin", "nhi", "nai", "cancel", "2", "dont", "don't", "❌", "mat", "nope"}
PAY = {"pay", "online", "prepaid", "upi", "link", "💳"}
COD = {"cod", "cash", "💵"}
ADDRESS = {"address", "pata", "adress", "addres", "location"}
YES_PHRASES = ("no problem", "koi baat nahi", "no issue")


def classify_reply(text: str, flow: str = "confirm") -> tuple[str, str]:
    """Return (intent, source). intent in yes|no|pay|cod|change_address|unclear."""
    t = text.strip().lower()
    if any(p in t for p in YES_PHRASES):
        return "yes", "rules"
    tokens = set(re.findall(r"[\w']+|[^\w\s]", t))
    if flow == "nudge":
        if tokens & PAY or t in {"1"}:
            return "pay", "rules"
        if tokens & COD or t in {"2"}:
            return "cod", "rules"
    if tokens & ADDRESS:
        return "change_address", "rules"
    y, n = bool(tokens & YES), bool(tokens & NO)
    if y and not n:
        return "yes", "rules"
    if n and not y:
        return "no", "rules"
    # conflicting or no keywords -> ask the LLM if available
    res = llm.ask_json(
        "Classify a customer's WhatsApp reply to an Indian e-commerce order message. "
        "Replies may be English, Hindi or Hinglish. Return JSON {\"intent\": one of "
        "\"yes\",\"no\",\"change_address\",\"unclear\"}. 'yes' = wants the order shipped "
        "(even later); 'no' = does not want it.",
        f"Message type: {'COD confirmation' if flow=='confirm' else 'prepaid offer'}\nReply: {text}",
    )
    if res and res.get("intent") in ("yes", "no", "change_address", "unclear"):
        return res["intent"], "llm"
    return "unclear", "rules"


class MockWhatsAppAPI:
    """Pretends to be the WhatsApp Business API: records every outgoing message."""

    def __init__(self, persist: bool = True):
        self.sent: list[dict] = []
        self.persist = persist

    def send(self, to: str, text: str, buttons: list[str] | None, at: datetime, order_id: str) -> dict:
        msg = {"to": to, "order_id": order_id, "at": at.isoformat(timespec="minutes"),
               "text": text, "buttons": buttons or []}
        self.sent.append(msg)
        if self.persist:
            OUTBOX.parent.mkdir(parents=True, exist_ok=True)
            with open(OUTBOX, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        return {"status": "sent", "message_id": f"wamid.mock.{len(self.sent)}"}


@dataclass
class Conversation:
    order: dict
    flow: str                   # "confirm" | "nudge"
    risk: float
    cfg: dict
    api: MockWhatsAppAPI
    started: datetime
    state: str = "NEW"
    language: str = "english"
    unclear_count: int = 0
    reminded: bool = False
    phase_started: datetime | None = None
    log: list[dict] = field(default_factory=list)
    needs_address: bool = False
    discount: int = 0

    # ---------- helpers ----------
    def _fmt(self, key: str) -> str:
        o = self.order
        v = int(float(o.get("order_value", 0) or 0))
        return T[self.language][key].format(
            name=(o.get("customer_name") or "there").split()[0].title(),
            order_id=o.get("order_id", ""), value=v, address=o.get("address", ""),
            discount=self.discount, new_value=v - self.discount,
            hours=self.cfg["prepaid_nudge"]["offer_valid_hours"],
            link=f"https://pay.oytra.in/mock/{o.get('order_id','')}",
        )

    def _send_time(self, now: datetime) -> datetime:
        w = self.cfg["whatsapp"]
        if now.hour >= w["quiet_hours_start"]:
            return (now + timedelta(days=1)).replace(hour=w["quiet_hours_end"], minute=0)
        if now.hour < w["quiet_hours_end"]:
            return now.replace(hour=w["quiet_hours_end"], minute=0)
        return now

    def _say(self, key: str, now: datetime, buttons_key: str | None = None) -> None:
        at = self._send_time(now)
        btns = T[self.language][buttons_key] if buttons_key else None
        self.api.send(self.order.get("phone", ""), self._fmt(key), btns, at, self.order.get("order_id", ""))
        self.log.append({"at": at, "who": "brand", "text": self._fmt(key), "buttons": btns})

    def _event(self, now: datetime, text: str) -> None:
        self.log.append({"at": now, "who": "system", "text": text})

    @property
    def done(self) -> bool:
        return self.state in {"CONFIRMED", "PREPAID", "CANCELLED", "CANCELLED_NO_REPLY",
                              "SHIP_UNCONFIRMED", "AGENT"}

    # ---------- flow ----------
    def start(self) -> None:
        state = self.order.get("state", "")
        langs = self.cfg["language_by_state"]
        self.language = langs.get(state, langs.get("default", "english"))
        if self.language not in T:
            self.language = "english"
        from .economics import discount_for
        self.discount = int(discount_for(float(self.order.get("order_value", 0) or 0), self.cfg))
        now = self.started
        self.phase_started = self._send_time(now)
        if self.flow == "nudge":
            self.state = "AWAIT_PAYMENT"
            self._say("nudge", now, "nudge_btn")
        elif self.needs_address:
            self.state = "AWAIT_ADDRESS"
            self._say("ask_address", now)
        else:
            self.state = "AWAIT_CONFIRM"
            self._say("confirm", now, "confirm_btn")

    def _start_confirm(self, now: datetime, intro: str | None = None) -> None:
        self.state, self.unclear_count, self.reminded = "AWAIT_CONFIRM", 0, False
        self.phase_started = self._send_time(now)
        if intro:
            self._say(intro, now)
        self._say("confirm", now, "confirm_btn")

    def payment_received(self, now: datetime) -> None:
        if self.state == "AWAIT_PAYMENT":
            self._event(now, "Payment webhook: paid online")
            self.state = "PREPAID"
            self._say("paid", now)

    def reply(self, text: str, now: datetime) -> None:
        self.log.append({"at": now, "who": "customer", "text": text})
        if self.done and self.state != "CANCELLED_NO_REPLY":
            return
        if self.state == "CANCELLED_NO_REPLY":  # late "yes" restores the order
            intent, src = classify_reply(text, "confirm")
            if intent == "yes":
                self._event(now, f"Late YES ({src}) - order restored")
                self.state = "CONFIRMED"
                self._say("confirmed", now)
            return
        if self.state == "AWAIT_ADDRESS":
            from .checks import address_check
            if address_check(text, self.order.get("city", "")) == "junk":
                self.unclear_count += 1
                if self.unclear_count >= 2:
                    self.state = "AGENT"
                    self._say("agent", now)
                else:
                    self._say("ask_address", now)
                return
            self.order["address"] = text.strip()
            self._event(now, "Address updated and re-checked: OK")
            self.state = "AWAIT_CONFIRM"
            self.unclear_count, self.reminded = 0, False
            self._say("address_thanks", now, "confirm_btn")
            return

        intent, src = classify_reply(text, self.flow if self.state == "AWAIT_PAYMENT" else "confirm")
        self._event(now, f"Reply understood as '{intent}' (by {src})")
        if self.state == "AWAIT_PAYMENT":
            if intent == "pay":
                self._event(now, "Customer chose to pay - waiting for payment (link stays valid)")
                return
            if intent in ("cod", "yes"):
                self.state = "CONFIRMED"
                self._say("confirmed", now)
                return
            if intent == "no":
                self.state = "CANCELLED"
                self._say("cancelled", now)
                return
            if intent == "change_address":
                self.state = "AWAIT_ADDRESS"
                self._say("ask_address", now)
                return
        else:  # AWAIT_CONFIRM
            if intent == "yes":
                self.state = "CONFIRMED"
                self._say("confirmed", now)
                return
            if intent == "no":
                self.state = "CANCELLED"
                self._say("cancelled", now)
                return
            if intent == "change_address":
                self.state = "AWAIT_ADDRESS"
                self.unclear_count = 0
                self._say("ask_address", now)
                return
        # unclear
        self.unclear_count += 1
        if self.unclear_count >= 2:
            self.state = "AGENT"
            self._say("agent", now)
        else:
            self._say("clarify", now, "nudge_btn" if self.state == "AWAIT_PAYMENT" else "confirm_btn")

    def tick(self, now: datetime) -> None:
        """Advance the clock: send reminders / apply timeouts."""
        if self.done or self.phase_started is None:
            return
        w, n = self.cfg["whatsapp"], self.cfg["prepaid_nudge"]
        waited = now - self.phase_started
        if self.state == "AWAIT_PAYMENT" and waited >= timedelta(hours=n["offer_valid_hours"]):
            self._event(now, f"No payment in {n['offer_valid_hours']}h - switching to COD confirmation")
            self._start_confirm(now, "switch_to_cod")
            return
        if self.state in ("AWAIT_CONFIRM", "AWAIT_ADDRESS"):
            if not self.reminded and waited >= timedelta(hours=w["reminder_after_hours"]):
                self.reminded = True
                self._say("reminder", now, "confirm_btn" if self.state == "AWAIT_CONFIRM" else None)
            if waited >= timedelta(hours=w["give_up_after_hours"]):
                if self.state == "AWAIT_CONFIRM" and self.risk * 100 < w["no_reply_ship_if_risk_below"]:
                    self._event(now, f"No reply in {w['give_up_after_hours']}h, risk "
                                     f"{self.risk:.0%} is low enough - shipping unconfirmed")
                    self.state = "SHIP_UNCONFIRMED"
                else:
                    self._event(now, f"No reply in {w['give_up_after_hours']}h - order put on hold")
                    self.state = "CANCELLED_NO_REPLY"
                    self._say("no_reply_cancel", now)


def start_conversation(order: dict, action: str, risk: float, cfg: dict, now: datetime | None = None,
                       api: MockWhatsAppAPI | None = None, needs_address: bool = False) -> Conversation:
    if action not in ("confirm", "nudge"):
        raise ValueError("WhatsApp flow only runs for 'confirm' and 'nudge' decisions")
    c = Conversation(order=dict(order), flow=action, risk=risk, cfg=cfg, api=api or MockWhatsAppAPI(),
                     started=now or datetime.now(), needs_address=needs_address)
    c.start()
    return c
