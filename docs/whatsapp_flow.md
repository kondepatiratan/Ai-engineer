# WhatsApp flow — "Ship after confirmation" and "Nudge to prepaid"

Working mock: `rto/whatsapp.py` (state machine + mock WhatsApp API) and the
**WhatsApp simulator** page in the app, where you play the customer and fast-forward the clock.
Every outgoing message is written to `data/whatsapp_outbox.jsonl`, the same shape a real
WhatsApp Business API call would take.

## When messages go out

| Moment | What happens |
|---|---|
| Order placed (08:00–21:00) | Message goes out within a minute, while the customer still remembers ordering. |
| Order placed 21:00–08:00 | Queued until 08:00. Late-night messages get ignored, or blocked as spam. |
| +12 h, no reply | One reminder, and only one. A second reminder feels like spam and costs another message. |
| +24 h, no reply | **Confirm flow:** if risk is below 35%, ship anyway; otherwise put the order on hold and tell the customer "reply YES within 3 days and we ship". **Nudge flow:** see below. |

All timings live in `config/rules.yaml` (`whatsapp:` section).

## Language

Chosen from the delivery state (`language_by_state` in the config):

- **Hinglish** (Hindi in Roman script) for Bihar, UP, MP, Rajasthan and Delhi.
- **English** everywhere else.

Hinglish rather than Devanagari because the customer's own address is usually typed in Hinglish ("Hanuman Mandir ke peeche"). That's the script they read and type on their phone. Adding Tamil or Bengali later means adding one template block, not new code.

## Flow 1 — Ship after confirmation

```
                 ┌──────────────── address is junk? ── ask for full address ──┐
order ─► send "Please confirm" [✅ Yes, ship it] [❌ Cancel order]             │
                 │                                                           ▼
   reply "yes" / "haan" / 👍 ────────────► CONFIRMED → release to courier   re-check address
   reply "no" / "nahi" / cancel ─────────► CANCELLED (ask why: optional)    OK → ask to confirm
   reply "address change karna hai" ─────► ask for new address ─────────────┘   junk twice → human agent
   reply something random ("??", "kab aayega") ─► 1 clarifying message with the buttons again
                                              └─ still unclear ─► human agent (WhatsApp inbox)
   no reply 12 h ─► one reminder
   no reply 24 h ─► risk < 35%: SHIP anyway (not worth losing a likely-genuine buyer)
                    risk ≥ 35%: HOLD + "reply YES in 3 days to restore" (late YES → ship)
```

## Flow 2 — Nudge to prepaid

```
order ─► "Pay online now, get ₹40 off + priority dispatch" + pay link (valid 6 h)
         [💳 Pay online] [💵 Keep Cash on Delivery]
   payment webhook ──────────────► PREPAID → ship
   "Keep COD" / "yes" ───────────► CONFIRMED (they engaged, so this counts as a confirmation) → ship as COD
   "no" / cancel ───────────────► CANCELLED
   random ──────────────────────► clarify once, then human agent
   no payment and no reply in 6 h ─► "No problem! Please confirm your COD order" → Flow 1
```

The nudge never takes COD away. A customer who ignores the offer just moves into the normal confirmation flow. This is what keeps Marketing's first-time buyers safe.

## Understanding replies (where the LLM is and isn't used)

1. **Keyword rules first.** These cover buttons, numbers, yes/haan/ok/👍, no/nahi/cancel, and "address". They're instant, free and predictable. One special case: "no problem" and "koi baat nahi" count as yes.
2. **LLM only when keywords conflict or find nothing.** Examples: "abhi nahi, kal bhejna" (not now, send tomorrow, which means yes) or "mere husband lenge" (my husband will receive it). The LLM returns one label (yes / no / change_address / unclear). Code still decides what happens next.
3. **Still unclear?** Ask once with buttons, then hand off to a person. We never cancel an order because a sentence was hard to parse.

## How much discount for prepaid, and why ₹40

Numbers for a typical "nudge" order in September: order value **₹860**, RTO risk **55%**.

| Item | ₹ |
|---|---|
| Cost of one RTO (65 + 65 + 25 + 8% × 180) | 169.4 |
| Margin on a delivered ₹860 order (62%) | 533 |
| Payment-gateway fee on prepaid (2%) | 17.2 |
| Courier COD fee we no longer pay (assumed; check the invoice) | 30 |

People who choose to pay upfront are mostly genuine, so we **don't** assume they carry the 55% risk. We assume a third of it, **18%**, against 4% for normal prepaid orders.

- Value of one switch = (18% − 4%) × (169.4 + 533) = **₹100**. We avoid the RTO *and* keep the sale.
- Cost of one switch at ₹40 off = 40 + 17.2 − 30 = **₹27**.
- **Net about +₹73 per switch.** Break-even discount ≈ ₹113.

Why ₹40 and not ₹100?

- **Safety margin.** If switchers turn out to be no riskier than normal prepaid buyers, every switch at ₹40 loses only ₹27. ₹40 still pays off if switchers are even **8%** likely to refuse, against 55% for the bucket.
- **Habit risk.** A large discount teaches customers to wait for it. ₹40 (about 5% of the order) is visible without becoming expected.
- **Small orders.** The config caps the discount at 8% of order value, so a ₹349 order gets ₹28.

The discount amount, the cap and the 6-hour validity are all in `config/rules.yaml`. The plan is to A/B test ₹30 / ₹40 / ₹60 in the first two weeks and keep whichever gives the best net ₹ per nudge.
