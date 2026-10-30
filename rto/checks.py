"""Name / address / phone quality checks.

Where the line between code and LLM is drawn
--------------------------------------------
* Code (always): phone format, pincode format, pincode-vs-city mismatch,
  obvious junk ("." / "home" / "xyz" / just the city name), keyboard-mash
  names ("asdf", "qwerty"), addresses that carry a house/flat number.
  These are exact, free, instant and never hallucinate.
* LLM (only for the uncertain middle): addresses with no house number,
  Hinglish directions ("Hanuman Mandir ke peeche wala ghar"), names the
  rules can't place. Here we need language understanding: is this a
  findable home or a vague hint? Is "Pinky Rani" a real name? (yes)
  The LLM returns a label, never a decision - the decision stays in code.

If no LLM key is configured, the rule-based label is used for everything,
so the system always runs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import llm
from .clean import phone_is_valid

FAKE_NAMES = {
    "test", "testing", "asdf", "qwerty", "abc", "abcd", "xyz", "xyz xyz", "aaa", "na", "n/a",
    "none", "null", "dummy", "fake", "sample", "customer", "user", "name", "no name",
}
KEYBOARD_RUNS = ("qwert", "asdf", "zxcv", "hjkl", "1234")
JUNK_ADDRESSES = {".", "..", "-", "xyz", "abc", "home", "house", "call me", "near station",
                  "na", "n/a", "same", "address", "test"}
LANDMARK_WORDS = re.compile(
    r"\b(near|opp|opposite|behind|ke paas|ke saamne|ke peeche|peeche|chowk|gali|mandir|masjid|"
    r"wala ghar|ghar ka naam|ph krke|phone karke)\b",
    re.I,
)
HINGLISH_WORDS = re.compile(r"\b(ke|se|ka|ki|wala|wali|paas|saamne|peeche|ghar|krke|karke|aana)\b", re.I)
HOUSE_NUMBER = re.compile(r"\d")


@dataclass
class Quality:
    name: str = "real"                 # real | suspicious | fake
    address: str = "complete"          # complete | landmark_only | junk
    phone_valid: bool = True
    pin_city_mismatch: bool = False
    hinglish: bool = False
    issues: list[str] = field(default_factory=list)
    source: str = "rules"              # rules | llm


def name_check(name: str) -> str:
    n = re.sub(r"\s+", " ", (name or "").strip().lower())
    if not n or n in FAKE_NAMES or any(k in n.replace(" ", "") for k in KEYBOARD_RUNS):
        return "fake"
    if re.search(r"\d", n) or re.fullmatch(r"(.)\1{2,}", n.replace(" ", "")):
        return "fake"
    parts = n.split(" ")
    if len(parts) == 2 and parts[0] == parts[1]:
        return "fake"
    if len(parts) == 1 or len(n) < 4:
        return "suspicious"
    return "real"


def address_check(address: str, city: str = "") -> str:
    a = (address or "").strip()
    al = a.lower()
    if len(a) < 12 or al in JUNK_ADDRESSES or (city and al == city.lower()):
        return "junk"
    if HOUSE_NUMBER.search(a):
        return "complete"
    if LANDMARK_WORDS.search(a):
        return "landmark_only"
    return "landmark_only"


LLM_SYSTEM = """You check delivery details for an Indian e-commerce COD order.
Return ONLY a JSON object:
{"name_quality": "real" | "suspicious" | "fake",
 "address_quality": "complete" | "landmark_only" | "junk",
 "language": "english" | "hinglish" | "hindi" | "other",
 "issue": "<max 12 words, plain English, empty if none>"}
Rules:
- "complete": a courier could find the door (house/flat/plot no., or a named
  building plus street/area). Hinglish is fine if it is specific.
- "landmark_only": only directions or landmarks ("Hanuman Mandir ke peeche
  wala ghar", "near SBI Bank, ghar ka naam Om niwas") - findable only by calling.
- "junk": unusable ("home", ".", "call me", just a city name).
- name "fake": keyboard mash or placeholder (asdf, test, xyz). Indian
  nicknames like "Pinky", "Bablu", "Guddi Devi" are "real" or "suspicious", never fake.
Judge only what is written. Do not invent details."""


def check_order(order: dict, pin_prefix_by_city: dict | None = None, use_llm: bool = True) -> Quality:
    q = Quality()
    q.name = name_check(order.get("customer_name", ""))
    q.address = address_check(order.get("address", ""), order.get("city", ""))
    q.phone_valid = phone_is_valid(str(order.get("phone", "")))
    q.hinglish = bool(HINGLISH_WORDS.search(order.get("address", "")))

    pin = str(order.get("pincode", "") or "")
    city = order.get("city", "")
    if pin_prefix_by_city and city in pin_prefix_by_city and len(pin) == 6:
        q.pin_city_mismatch = pin[:3] != pin_prefix_by_city[city]

    # Ask the LLM only when the rules are unsure (saves cost and latency).
    uncertain = q.address == "landmark_only" or q.name == "suspicious" or (
        q.hinglish and q.address != "junk"
    )
    if use_llm and uncertain and llm.available():
        res = llm.ask_json(
            LLM_SYSTEM,
            f"Name: {order.get('customer_name','')}\nAddress: {order.get('address','')}\n"
            f"City: {city}\nPincode: {pin}",
        )
        if res:
            if res.get("name_quality") in ("real", "suspicious", "fake"):
                q.name = res["name_quality"]
            if res.get("address_quality") in ("complete", "landmark_only", "junk"):
                q.address = res["address_quality"]
            if res.get("issue"):
                q.issues.append(str(res["issue"]))
            q.source = "llm"

    if q.name == "fake":
        q.issues.append("name looks fake or like a placeholder")
    if q.address == "junk":
        q.issues.append("address is too vague for a courier to find")
    elif q.address == "landmark_only":
        q.issues.append("address has only landmarks, no house number")
    if not q.phone_valid:
        q.issues.append("phone number is not a valid 10-digit mobile")
    if q.pin_city_mismatch:
        q.issues.append("pincode does not match the city")
    # de-duplicate, keep order
    q.issues = list(dict.fromkeys(q.issues))
    return q
