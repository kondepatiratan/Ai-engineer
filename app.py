"""RTO Guard - one app with four pages.

    streamlit run app.py

  Morning dashboard  : yesterday's 4 buckets and Rs impact (for the founder)
  Check an order     : type an order, get the decision + reason
  WhatsApp simulator : play the customer side of the confirmation / prepaid flow
  Rules              : change thresholds without touching code (saves config/rules.yaml)
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from rto import config, economics, llm
from rto.clean import load_orders
from rto.config import ROOT
from rto.engine import LABELS, Engine
from rto.model import train
from rto.whatsapp import MockWhatsAppAPI, start_conversation

st.set_page_config(page_title="RTO Guard", page_icon="📦", layout="wide")
ORDER = ["ship", "confirm", "nudge", "hold"]
ICON = {"ship": "🟢", "confirm": "🟡", "nudge": "🟠", "hold": "🔴"}


def rs(x: float) -> str:
    return f"₹{x:,.0f}" if x >= 0 else f"-₹{abs(x):,.0f}"


@st.cache_data
def orders() -> pd.DataFrame:
    return load_orders(ROOT / "data" / "orders_sample.csv", keep_unresolved=True)


@st.cache_resource
def model_before(month_start: str):
    """Model trained only on orders before this month (no peeking at the answers)."""
    df = orders()
    hist = df[(df.order_datetime < month_start) & df.final_status.isin(["DELIVERED", "RTO"])]
    return train(hist)


def decide_frame(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    rows = []
    for o in df.to_dict("records"):
        month = pd.Timestamp(o["order_datetime"]).strftime("%Y-%m-01")
        eng = Engine(model_before(month), cfg, use_llm=llm.available())
        d = eng.decide(o)
        rows.append({**o, **{k: v for k, v in d.to_dict().items() if k != "order_id"}})
    return pd.DataFrame(rows)


cfg = config.load()
page = st.sidebar.radio("Page", ["Morning dashboard", "Check an order", "WhatsApp simulator", "Rules"])
st.sidebar.caption(
    f"Address/name checks: **{'LLM (' + llm.provider() + ')' if llm.available() else 'rules only'}**. "
    "Set LLM_PROVIDER + API key to turn on the LLM."
)

# ---------------------------------------------------------------- dashboard
if page == "Morning dashboard":
    df = orders()
    last = max(df.order_date)
    st.title("☀️ Good morning - yesterday's COD orders")
    day = st.date_input("Day", value=last, min_value=min(df.order_date), max_value=last,
                        help="The sample data ends on 30 Sep 2026, so 'yesterday' defaults to that day.")
    today = decide_frame(df[df.order_date == day], cfg)
    if today.empty:
        st.info("No COD orders on this day.")
        st.stop()

    cols = st.columns(4)
    for c, a in zip(cols, ORDER):
        n = int((today.action == a).sum())
        c.metric(f"{ICON[a]} {LABELS[a]}", n, f"{n / len(today):.0%} of {len(today)} orders", delta_color="off")

    saved, lost, cost = today.saved.sum(), today.lost.sum(), today.cost.sum()
    m = st.columns(4)
    m[0].metric("Est. saved from avoided RTOs", rs(saved))
    m[1].metric("Est. lost from genuine orders cancelled / dropped", rs(lost))
    m[2].metric("Discounts + WhatsApp costs", rs(cost))
    m[3].metric("Net impact", rs(saved - lost - cost))
    st.caption(
        f"Estimates use each order's RTO risk. One RTO costs {rs(config.rto_cost(cfg))}; a cancelled genuine "
        f"order costs its {cfg['money']['gross_margin']}% margin, plus {rs(cfg['money']['new_customer_future_value'])} "
        "future value if it was a first-time buyer."
    )

    st.subheader("Orders that need attention")
    view = today[today.action != "ship"].sort_values("risk", ascending=False)
    st.dataframe(
        view.assign(decision=view.action.map(lambda a: f"{ICON[a]} {LABELS[a]}"),
                    risk=(view.risk * 100).round().astype(int).astype(str) + "%")[
            ["order_id", "customer_name", "city", "order_value", "risk", "decision", "reason"]],
        hide_index=True, width="stretch",
    )
    with st.expander(f"Shipped as-is ({int((today.action == 'ship').sum())})"):
        s = today[today.action == "ship"]
        st.dataframe(s[["order_id", "customer_name", "city", "order_value", "reason"]], hide_index=True,
                     width="stretch")

    st.subheader("Last 30 days")
    span = df[(df.order_date > day - timedelta(days=30)) & (df.order_date <= day)]
    hist = decide_frame(span, cfg)
    hist["net"] = hist.saved - hist.lost - hist.cost
    daily = hist.groupby("order_date").agg(net=("net", "sum"))
    st.bar_chart(daily, y="net", y_label="Net ₹ per day")
    known = hist[hist.final_status.isin(["DELIVERED", "RTO"])]
    check = known.groupby("action").agg(orders=("order_id", "size"),
                                        actual_rto_rate=("final_status", lambda s: (s == "RTO").mean()))
    check = check.reindex([a for a in ORDER if a in check.index])
    check.index = [LABELS[a] for a in check.index]
    st.caption("How the buckets actually turned out (outcomes arrive 7-10 days later). "
               "If the 'Ship as-is' RTO rate creeps up, the model needs retraining.")
    st.dataframe(check.style.format({"actual_rto_rate": "{:.0%}"}), width="stretch")

# ---------------------------------------------------------------- single order
elif page == "Check an order":
    st.title("🔎 Check a COD order")
    df = orders()
    with st.form("order"):
        c1, c2 = st.columns(2)
        name = c1.text_input("Customer name", "Rahul Kumar")
        phone = c2.text_input("Phone", "9876543210")
        address = st.text_input("Address", "Hanuman Mandir ke peeche wala ghar")
        c1, c2, c3 = st.columns(3)
        cities = sorted(c for c in df.city.unique() if c)
        city = c1.selectbox("City", cities, index=cities.index("Darbhanga"))
        state = df[df.city == city].state.mode().iloc[0]
        pincode = c2.text_input("Pincode", "846004")
        value = c3.number_input("Order value (₹)", 0, 20000, 899)
        c1, c2, c3, c4 = st.columns(4)
        items = c1.number_input("Items", 1, 10, 2)
        prior = c2.number_input("Past orders", 0, 50, 0)
        prior_rto = c3.number_input("Past refused (RTO)", 0, 50, 0)
        utm = c4.selectbox("Source", sorted(df.utm_source.unique()), index=sorted(df.utm_source.unique()).index("meta_ads"))
        go = st.form_submit_button("Decide", type="primary")
    if go:
        o = dict(order_id="NEW-1", customer_name=name, phone=phone, address=address, city=city, state=state,
                 pincode=pincode, order_value=value, num_items=items, customer_prior_orders=prior,
                 customer_prior_rto=prior_rto, utm_source=utm)
        d = Engine(model_before("2026-10-01"), cfg, use_llm=llm.available()).decide(o)
        st.markdown(f"## {ICON[d.action]} {d.label}")
        st.markdown(f"**Why:** {d.reason}")
        st.progress(min(d.risk, 1.0), text=f"RTO risk {d.risk:.0%}")
        if d.issues:
            st.markdown("**Checks flagged:** " + "; ".join(d.issues) + f"  _(checked by {d.check_source})_")
        st.caption(f"Rule: {d.rule}. Expected vs shipping blindly: saved {rs(d.saved)}, "
                   f"lost {rs(d.lost)}, costs {rs(d.cost)}.")
        st.markdown("**₹ comparison of all 4 actions for this order**")
        new = prior == 0
        st.dataframe(pd.DataFrame([{"action": LABELS[a], "net ₹ vs shipping": round(
            economics.net(a, d.risk, value, new, cfg))} for a in ORDER]), hide_index=True)

# ---------------------------------------------------------------- whatsapp
elif page == "WhatsApp simulator":
    st.title("💬 WhatsApp flow simulator")
    st.caption("You play the customer. Messages go to a mock WhatsApp API (logged in data/whatsapp_outbox.jsonl). "
               "Use the clock buttons to see reminders and the 24-hour rule.")
    df = orders()
    pool = decide_frame(df[df.order_date == max(df.order_date)], cfg)
    pool = pool[pool.action.isin(["confirm", "nudge"])]
    pick = st.selectbox("Order", pool.order_id,
                        format_func=lambda i: f"{i} - {pool.set_index('order_id').loc[i, 'customer_name']} "
                                              f"({pool.set_index('order_id').loc[i, 'city']}, "
                                              f"{LABELS[pool.set_index('order_id').loc[i, 'action']]})")
    if st.button("Start / restart conversation") or "conv" not in st.session_state or \
            st.session_state.get("conv_id") != pick:
        row = pool.set_index("order_id").loc[pick].to_dict()
        row["order_id"] = pick
        st.session_state.clock = datetime(2026, 10, 1, 10, 0)
        st.session_state.conv = start_conversation(
            row, row["action"], row["risk"], cfg, st.session_state.clock, MockWhatsAppAPI(),
            needs_address=row["rule"] == "junk_address")
        st.session_state.conv_id = pick
    conv = st.session_state.conv

    st.markdown(f"**State:** `{conv.state}` · language: {conv.language} · risk {conv.risk:.0%} · "
                f"clock: {st.session_state.clock:%d %b %H:%M}")
    for m in conv.log:
        if m["who"] == "system":
            st.caption(f"⚙️ {m['at']:%d %b %H:%M} - {m['text']}")
            continue
        with st.chat_message("assistant" if m["who"] == "brand" else "user",
                             avatar="🛍️" if m["who"] == "brand" else "🙋"):
            st.write(m["text"])
            if m.get("buttons"):
                st.caption(" | ".join(f"[{b}]" for b in m["buttons"]))

    c = st.columns(5)
    if c[0].button("⏩ +1 hour"):
        st.session_state.clock += timedelta(hours=1)
    if c[1].button("⏩ +6 hours"):
        st.session_state.clock += timedelta(hours=6)
    if c[2].button("⏩ +12 hours"):
        st.session_state.clock += timedelta(hours=12)
    if conv.flow == "nudge" and c[3].button("💳 Customer pays"):
        conv.payment_received(st.session_state.clock)
    conv.tick(st.session_state.clock)
    text = st.chat_input("Reply as the customer (try: haan, nahi, 'kal bhejna', 'address change', 'asdf')")
    if text:
        conv.reply(text, st.session_state.clock)
        st.rerun()

# ---------------------------------------------------------------- rules
else:
    st.title("⚙️ Rules (no code needed)")
    st.caption("Changes save to config/rules.yaml and apply to the next order. "
               "The table below shows what each setting would have done to September's orders.")
    new_cfg = copy.deepcopy(cfg)
    for who, label in [("new_customer", "First-time customers"), ("repeat_customer", "Repeat customers")]:
        st.subheader(label)
        b = new_cfg["risk_bands"][who]
        c = st.columns(3)
        b["confirm_from"] = c[0].slider("WhatsApp confirm from risk %", 0, 100, b["confirm_from"], key=who + "c")
        b["nudge_from"] = c[1].slider("Prepaid nudge from risk %", 0, 100, b["nudge_from"], key=who + "n")
        b["hold_from"] = c[2].slider("Hold / cancel from risk %", 0, 100, b["hold_from"], key=who + "h")
    st.subheader("Hard rules")
    for k, r in new_cfg["hard_rules"].items():
        c = st.columns([3, 1, 2])
        r["enabled"] = c[0].checkbox(k.replace("_", " ").capitalize(), r["enabled"])
        opts = ["hold", "nudge_prepaid", "confirm"]
        r["action"] = c[2].selectbox("action", opts, index=opts.index(r["action"]), key=k, label_visibility="collapsed")
    st.subheader("Offer & WhatsApp")
    c = st.columns(3)
    new_cfg["prepaid_nudge"]["discount_rs"] = c[0].number_input("Prepaid discount ₹", 0, 500,
                                                                new_cfg["prepaid_nudge"]["discount_rs"])
    new_cfg["whatsapp"]["no_reply_ship_if_risk_below"] = c[1].slider(
        "No reply in 24h: still ship if risk below %", 0, 100, new_cfg["whatsapp"]["no_reply_ship_if_risk_below"])
    new_cfg["money"]["new_customer_future_value"] = c[2].number_input(
        "Future value of a new customer ₹", 0, 5000, new_cfg["money"]["new_customer_future_value"])

    from scripts.backtest import run
    with st.spinner("Re-running September with these settings..."):
        res = run("2026-09", cfg=new_cfg, verbose=False)
    s = res.groupby("action").agg(orders=("order_id", "size"), actual_rto=("actual", lambda a: (a == "RTO").mean()))
    s = s.reindex([a for a in ORDER if a in s.index]); s.index = [LABELS[a] for a in s.index]
    c = st.columns(4)
    c[0].metric("Saved", rs(res.saved.sum()))
    c[1].metric("Lost", rs(res.lost.sum()))
    c[2].metric("Costs", rs(res.cost.sum()))
    c[3].metric("Net (Sept, vs shipping all)", rs(res.net.sum()))
    st.dataframe(s.style.format({"actual_rto": "{:.0%}"}), width="stretch")
    if st.button("💾 Save these rules", type="primary"):
        config.save(new_cfg)
        st.success("Saved to config/rules.yaml")
