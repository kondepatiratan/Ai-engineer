# Decision log

The important choices, the alternatives I considered, and why I picked what I did. Newest thinking last.

## Data

- **Cleaned before analysing anything.** The export had 30 exact duplicate order IDs (dropped, keeping the first), `final_status` in 5 spellings (`RTO`, `Rto`, ` RTO`, `delivered`…), normalised to upper case, and 1 `IN_TRANSIT` order (excluded from training because it has no outcome yet). It also had phones as `+91 98xxx xxxxx` (normalised) or 9 digits long (flagged invalid), 12 missing pincodes, 4 missing cities and 3 orders worth ₹0. *Alternative:* drop every row with any problem. *Rejected:* that throws away real orders whose outcome we know.
- **Tested on the last month only, trained on the months before it.** The model is trained on April–August and tested on September (329 orders). *Alternative:* a random 80/20 split. *Rejected:* it would let the model learn from orders placed after the ones it is tested on, which flatters the results. "What would this have done last month" is also exactly the founder's question.

## Risk model

- **Logistic regression with 10 readable signals, not a gradient-boosted model.** With about 2,000 orders a bigger model mostly memorises noise. A logistic model gives one weight per signal, and those weights become the plain-English reasons directly. The result is AUC 0.74 on September and well calibrated: when it says 30%, about 30% are actually refused. Checked by bucket.
- **City-level history, not pincode-level.** 1,066 pincodes for 1,987 orders, at most 8 orders each, so pincode RTO rates are noise. City rates are smoothed toward the state and overall average (20-order prior), so a city with 3 orders can't swing to 100%. *Rejected Ops' "stop COD in bad pincodes"* for this reason, and because blocking whole cities lost money in the backtest (−₹9,557 in September).
- **Dropped 3 signals after looking at the weights.** `new_customer` was redundant with the repeat/past-RTO signals, and its weight came out *negative* (implying first-timers are safer, which is false). `name_fake` alone and `pincode ≠ city` had 7 and 4 standalone examples, too few for a stable weight. Fake names are handled by a hard rule (fake name + junk address) instead, and pincode mismatch is shown as an issue.
- **Left out day-of-week and hour.** Friday shows 35% RTO against 22% on Monday, but there is no business reason for it, and with ~300 orders per weekday it is likely noise. Not worth the risk of a spurious rule.

## Where the LLM is used (and where it isn't)

- **Code:** phone format, pincode format, pincode-vs-city, ₹ maths, risk scoring, thresholds, final decision, reason text, keyword reply matching. These are exact, free, instant and auditable, and an LLM adds only cost and randomness here.
- **LLM, only in the uncertain middle:** addresses with no house number or in Hinglish ("Hanuman Mandir ke peeche wala ghar": findable or not?), names the rules can't place ("Pinky", "Bablu" are real Indian names, not fake), and WhatsApp replies where keywords conflict ("abhi nahi, kal bhejna"). Rules decide first and call the LLM only when unsure. On this data that is about 25% of orders, which keeps cost and latency low.
- **The LLM outputs a label, never a decision.** It can move an address from "landmark only" to "complete" or "junk", but the bucket is still decided by code and the config file. An LLM mistake can therefore at worst send an order to WhatsApp confirmation, never cancel it on its own.
- **Without an API key, everything falls back to the rules.** Reviewers can run the project in minutes, and an outage at the LLM provider never stops orders from shipping. Answers are cached on disk so re-runs don't pay twice.
- **Model training uses the rule-based labels.** I couldn't call an LLM on all 2,000 orders in my environment. In this dataset the address patterns are regular enough that rules catch them; the LLM matters more for real, messier addresses. `scripts/train.py --llm` retrains with LLM labels.

## Decision rules and ₹

- **Every decision is compared with "ship everything" in ₹.** An RTO costs ₹169.40 (65 + 65 + 25 + 8% × ₹180). A cancelled genuine order costs its 62% margin, plus ₹300 future value if it was a first-time buyer, using Marketing's acquisition cost as a proxy. *Alternative:* optimise RTO rate. *Rejected:* the RTO rate is easy to "improve" by cancelling everything, which loses money.
- **Key insight: cancellation needs very high risk.** Margin on a ₹649 order (₹402) is 2.4× the RTO cost. Cancelling only pays above 70% risk for repeat buyers and 81% for first-timers. So hold bands are 75% (repeat) and 85% (new). "Cancel if risk ≥ 40%" would have lost ₹19,383 in September.
- **Risk bands instead of pure "pick the highest expected ₹".** The ₹ maths says nudging beats confirming from about 25–30% risk, but that rests on my assumption about how risky the people who switch to prepaid are. I kept confirmation for 25–40% and nudges for 40%+, the more cautious choice, and made it easy to move once real data arrives. Bands are also easier for an ops person to read and change than a formula.
- **Separate bands for new and repeat customers.** This is how Marketing's concern is built into the rules rather than argued about.
- **Hard rules, each checked against the backtest:**
  - Fake name + junk address → hold. All 4 such orders in September were RTO.
  - 2+ past refusals → nudge, not cancel. 77% fail, but they still pay if they prepay.
  - Junk address → ask for the address on WhatsApp (escalated to nudge or hold if the risk is higher anyway).
  - *Invalid phone → hold was turned OFF* after the backtest showed both held orders were genuine. The model already counts a bad phone.
- **Thresholds live in `config/rules.yaml`, editable from the app's Rules page.** *Alternative:* a Google Sheet. *Rejected for now:* an extra dependency, though easy to add later. The Rules page re-runs September with the new settings so the person changing them sees the ₹ effect before saving. Saving keeps the comments in the file.

## WhatsApp flow and discount

- **Ask once, remind once, then decide.** One reminder at 12 h; at 24 h, ship anyway if risk is below 35%, otherwise hold with a "reply YES in 3 days" message. *Alternative:* cancel every non-reply. *Rejected:* many genuine buyers simply don't see the message.
- **Quiet hours 21:00–08:00.** Messages sent at night get ignored or reported as spam.
- **Hinglish for Hindi-belt states.** That is how these customers write their own addresses.
- **"Keep COD" counts as a confirmation**, since the customer engaged. The nudge never removes COD, which protects Marketing's first-time buyers.
- **Two unclear replies go to a human agent.** We never cancel because a sentence was hard to parse.
- **₹40 discount (capped at 8% of order value).** For a typical nudge order (₹860, 55% risk) one switch is worth about ₹100, and ₹40 off costs ₹27 after the gateway fee and the COD fee saved. Break-even is about ₹113. I chose ₹40, not ₹100, to cap the loss if switchers turn out to be as safe as normal prepaid buyers, and to avoid training customers to wait for discounts. Full maths in `docs/whatsapp_flow.md`.
- **Assumed a courier COD fee of ₹30** saved on prepaid orders. This wasn't in the brief; it needs checking against the courier invoice.

## Dashboard

- **Streamlit, one app with 4 pages** (dashboard, check an order, WhatsApp simulator, rules). *Alternative:* a static HTML page. *Rejected:* the Rules page has to save settings and re-run the backtest live. Streamlit runs with a single `pip install`.
- **"Yesterday" defaults to the last day in the data** (30 Sep 2026), with a date picker. The ₹ figures for a day are *estimates* from each order's risk, because real outcomes arrive 7–10 days later. A second table shows how each bucket actually turned out, which is the early-warning check for model drift.
- **The dashboard uses a model trained only on months before the day shown**, so it never peeks at answers it is being judged on.
