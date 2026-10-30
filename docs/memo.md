---
title: "Cutting COD returns without cutting growth"
---

**To:** Founder, Oytra · **From:** Kondepati Ratan · **Date:** 30 Oct 2026
**Re:** what the order data says about RTO, and what I recommend we do

**The short version.** Cancelling risky COD orders costs us more than it saves. A delivered ₹649 order earns ₹402 of margin, while an RTO costs ₹169. So cancelling only pays when an order is **more than 70% likely to fail**, or **81% for a first-time buyer** once we count the ₹300 we paid to acquire them. Almost no orders are that clear-cut. The money is in *confirming* and *nudging to prepaid*, not cancelling. On September's orders my rules would have added about **₹16,000 a month** at our volume (1,650 COD orders). The "cancel anything risky" rule would have *lost* about **₹97,000**.

## 1. What surprised me in the data (1,987 past COD orders, Apr–Sep 2026)

1. **We can't judge pincodes, but we can judge cities.** The 1,987 orders are spread over 1,066 pincodes, and none has more than 8 orders. "Bad pincode history" is mostly noise. At city level the gap is real: Gorakhpur 62%, Bareilly 61% and Darbhanga 59% RTO against 18% in Mumbai and Pune. Bihar as a whole is at 54%.
2. **1 in 11 orders has an address no courier can use** (".", "home", "call me", or just the city name). These fail 62% of the time. **But 38% of them were still delivered**, so cancelling them all would have turned away 70 real customers. Asking for the address on WhatsApp is the better move.
3. **Customer history is the strongest signal we have.** Repeat buyers who have never refused fail only 14% of the time, against 30% for first-timers. Buyers who refused 2+ times before fail 77% of the time. 75% of COD orders come from first-timers, which is exactly why we can't simply cancel the unknowns.
4. **Our most expensive customers are our riskiest COD buyers.** First-time buyers from Meta ads refuse 34% of the time, against 22% for first-time buyers who come direct. Marketing is right that these buyers matter, and they are also where prepaid nudges pay off most.
5. **A fake name on its own means little.** The 7 orders with a name like "asdf" but a real address were all delivered. A fake name *plus* a junk address failed 73% of the time. Only that combination is held.
6. *Data note:* the export had 30 duplicate orders, 5 spellings of "RTO", 9-digit phone numbers and ₹0 orders. I cleaned these before any analysis. Also, the average COD order here is ₹920, not the ₹649 company average. COD orders may simply be larger, or the sample is skewed; worth checking.

## 2. Recommended rules, and what they'd have done in September

Every COD order gets an RTO risk score from a simple model trained on April–August, which ranks September's orders well (AUC 0.74). Then:

| Bucket | Rule | Sept orders | Real RTO rate |
|---|---|---|---|
| Ship as-is | risk < 25% | 188 (57%) | 14% |
| Confirm on WhatsApp | 25–40%, or junk address (ask for full address) | 73 (22%) | 33% |
| Nudge to prepaid (₹40 off) | 40%+, or 2+ past refusals; COD still allowed | 63 (19%) | 49% |
| Hold / cancel | fake name + junk address, or risk ≥ 75% (85% for first-timers) | 5 (2%) | 100% |

We touch 43% of orders, and that group holds **70% of all RTOs**. Only 5 orders were held, and all 5 were real RTOs, so **zero genuine buyers were cancelled**. Each decision comes with a one-line reason, e.g. *"66% risk: deliveries to Gorakhpur are often refused and came from a Meta ad. Offer a small discount to pay online…"*. Every threshold above can be changed on the app's Rules page without touching code.

## 3. Operations vs Marketing, in rupees (September orders, real outcomes)

| Policy | Orders cancelled | Genuine buyers lost | Net vs today |
|---|---|---|---|
| Ops: cancel if risk ≥ 40% | 68 | 32 | **−₹19,383** |
| Ops: stop COD in the 5 worst cities | 37 | 17 | **−₹9,557** |
| **Recommended rules** | 5 | 0 | **+₹3,264** |

This is a 329-order sample, about one-fifth of a real month. **Ops is right that RTO is expensive** (₹78,000 a month at our volume). But blanket cancellation throws away margin worth more than the returns it prevents. That stays true even if we ignore Marketing's ₹300 acquisition cost completely: "cancel ≥ 40%" is still −₹10,983. **Marketing is right that first orders matter**, so first-timers are never cancelled on risk alone (85% bar). Instead they get confirmation and a prepaid offer, which never takes COD away. Stopping COD in bad cities stays negative even if half those buyers switched to prepaid (−₹3,084).

## 4. What could go wrong, and how we'd notice

- **My WhatsApp assumptions could be off.** I assumed 40% of would-be RTOs are caught, 3% of genuine buyers drop off and 20% take the prepaid offer. In a pessimistic case (25% / 6% / 10%) the system roughly breaks even (−₹312 in the sample). To find out, keep a **10% random control group** that ships without confirmation, and measure the real numbers in two weeks.
- **The model gets stale or meets new cities.** Unknown cities fall back to the state average. The dashboard shows each bucket's real RTO rate; if "Ship as-is" rises above ~18%, retrain.
- **Customers tap "Yes" and still refuse.** We'll track RTO among confirmed orders. If it stays near 33%, confirmation isn't filtering anyone.
- **The LLM misreads an address.** An LLM label can only push an order into *confirmation*, never into cancellation, and 20 labels a week get a spot-check.
- **Discount habit.** We'll watch whether repeat customers start waiting for the ₹40 offer.

::: {style="page-break-inside: avoid"}
## 5. With two more weeks, I would build

1. **Live plumbing:** a Shopify order webhook, the WhatsApp Business API (Meta Cloud API or Interakt), a payment-link webhook, and the courier's RTO feed so outcomes flow back automatically.
2. **The control-group test** above, plus an A/B test of ₹30 / ₹40 / ₹60 discounts, to replace my assumptions with our own numbers.
3. **Address repair before shipping:** an LLM rewrites Hinglish landmark addresses into a clean format, a geocoder checks them, and the customer approves the result on WhatsApp.
4. **Courier choice by city:** Delhivery's RTO rate is 26% against 31% for Xpressbees. Routing risky cities to the best courier there is a cheap win to test.
5. **Weekly automatic retraining** with a short "what changed" note to ops.
:::
