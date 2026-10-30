"""Run with:  python -m pytest -q"""
from datetime import datetime, timedelta

import pytest

from rto import config, economics
from rto.checks import address_check, name_check
from rto.clean import normalise_phone, phone_is_valid
from rto.engine import Engine
from rto.model import RiskModel
from rto.whatsapp import MockWhatsAppAPI, classify_reply, start_conversation

CFG = config.load()
BASE = dict(order_id="T1", customer_name="Priya Sharma", phone="9876543210",
            address="Flat 12, Sai Apartments, MG Road", city="Mumbai", state="Maharashtra",
            pincode="400050", order_value=699, num_items=1, customer_prior_orders=0,
            customer_prior_rto=0, utm_source="direct", courier="Delhivery")


@pytest.fixture(scope="module")
def engine():
    return Engine(RiskModel.load(), CFG, use_llm=False)


def test_phone_cleaning():
    assert normalise_phone("+91 98765 43210") == "9876543210"
    assert not phone_is_valid(normalise_phone("98765432"))


@pytest.mark.parametrize("name,expected", [("asdf", "fake"), ("xyz xyz", "fake"), ("test", "fake"),
                                           ("Priya Sharma", "real"), ("Pinky", "suspicious")])
def test_names(name, expected):
    assert name_check(name) == expected


@pytest.mark.parametrize("addr,expected", [(".", "junk"), ("home", "junk"), ("Patna", "junk"),
                                           ("Hanuman Mandir ke peeche wala ghar", "landmark_only"),
                                           ("House No. 4, Sector 7, near Water Tank", "complete")])
def test_addresses(addr, expected):
    assert address_check(addr, "Patna") == expected


def test_rto_cost():
    assert config.rto_cost(CFG) == pytest.approx(169.4)


def test_safe_order_ships(engine):
    d = engine.decide(BASE)
    assert d.action == "ship" and d.reason


def test_fake_order_held(engine):
    d = engine.decide({**BASE, "customer_name": "asdf", "address": "."})
    assert d.action == "hold" and "look fake" in d.reason


def test_serial_refuser_nudged_not_cancelled(engine):
    d = engine.decide({**BASE, "customer_prior_orders": 3, "customer_prior_rto": 2})
    assert d.action in ("nudge", "hold")


def test_risky_area_gets_protection(engine):
    d = engine.decide({**BASE, "city": "Darbhanga", "state": "Bihar", "pincode": "846004",
                       "address": "Hanuman Mandir ke peeche wala ghar", "utm_source": "meta_ads"})
    assert d.action in ("confirm", "nudge")
    assert "Darbhanga" in d.reason


def test_thresholds_come_from_config():
    cfg = config.load()
    cfg["risk_bands"]["new_customer"].update(confirm_from=1, nudge_from=2, hold_from=3)
    d = Engine(RiskModel.load(), cfg, use_llm=False).decide(BASE)
    assert d.action == "hold"


def test_cancel_breakeven_is_high():
    # cancelling a Rs 649 first order only pays off above ~80% risk
    assert economics.best_action(0.6, 649, True, CFG) != "hold"
    assert economics.net("hold", 0.95, 649, True, CFG) > 0


@pytest.mark.parametrize("text,intent", [("Yes", "yes"), ("haan bhej do", "yes"), ("nahi chahiye", "no"),
                                         ("no problem", "yes"), ("cancel", "no"), ("??", "unclear"),
                                         ("address change karna hai", "change_address")])
def test_reply_classifier(text, intent):
    assert classify_reply(text)[0] == intent


def _conv(action="confirm", risk=0.3, **kw):
    return start_conversation({**BASE, **kw}, action, risk, CFG, datetime(2026, 9, 30, 10, 0),
                              MockWhatsAppAPI(persist=False))


def test_confirm_yes():
    c = _conv()
    c.reply("haan", datetime(2026, 9, 30, 10, 5))
    assert c.state == "CONFIRMED"


def test_confirm_random_twice_goes_to_agent():
    c = _conv()
    c.reply("kya hai ye", datetime(2026, 9, 30, 10, 5))
    assert c.state == "AWAIT_CONFIRM"
    c.reply("hmm", datetime(2026, 9, 30, 10, 6))
    assert c.state == "AGENT"


def test_no_reply_low_risk_ships_high_risk_holds():
    t0 = datetime(2026, 9, 30, 10, 0)
    low, high = _conv(risk=0.28), _conv(risk=0.45)
    for c in (low, high):
        c.tick(t0 + timedelta(hours=12))
        assert c.reminded
        c.tick(t0 + timedelta(hours=24))
    assert low.state == "SHIP_UNCONFIRMED" and high.state == "CANCELLED_NO_REPLY"
    high.reply("yes", t0 + timedelta(hours=30))
    assert high.state == "CONFIRMED"


def test_nudge_timeout_falls_back_to_confirm():
    c = _conv("nudge", 0.5)
    c.tick(datetime(2026, 9, 30, 16, 1))
    assert c.state == "AWAIT_CONFIRM"


def test_quiet_hours():
    c = start_conversation(BASE, "confirm", 0.3, CFG, datetime(2026, 9, 30, 23, 30),
                           MockWhatsAppAPI(persist=False))
    assert c.api.sent[0]["at"].startswith("2026-10-01T08:00")


def test_hinglish_for_bihar():
    c = _conv(state="Bihar")
    assert c.language == "hinglish" and "Namaste" in c.api.sent[0]["text"]


def test_llm_paths_with_fake_provider(monkeypatch, tmp_path):
    """No network here: fake the HTTP call and check both providers' responses are parsed and cached."""
    from rto import checks, llm
    monkeypatch.setattr(llm, "CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setattr(llm, "_cache", None)
    answer = '{"name_quality":"real","address_quality":"complete","language":"hinglish","issue":""}'
    calls = []

    def fake_post(url, payload, headers):
        calls.append(url)
        if "googleapis" in url:
            return {"candidates": [{"content": {"parts": [{"text": answer}]}}]}
        return {"content": [{"text": "```json\n" + answer + "\n```"}]}

    monkeypatch.setattr(llm, "_post", fake_post)
    order = {**BASE, "address": "Gupta ji ka ghar, gali no 4 ke andar, Shiv Mandir ke paas"}
    for prov, key in [("gemini", "GEMINI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")]:
        monkeypatch.setenv("LLM_PROVIDER", prov)
        monkeypatch.setenv(key, "test-key")
        q = checks.check_order(order)
        assert q.source == "llm" and q.address == "complete"
    n = len(calls)
    checks.check_order(order)  # same input -> served from cache
    assert len(calls) == n
