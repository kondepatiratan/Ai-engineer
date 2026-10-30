# RTO Guard — COD order decision engine for a D2C haircare brand

Looks at every Cash-on-Delivery order before it ships and picks one of four actions, each with a plain-English reason:

| | Action | When (default rules) |
|---|---|---|
| 🟢 | **Ship as-is** | RTO risk < 25% |
| 🟡 | **Ship after WhatsApp confirmation** | 25–40%, or the address is too vague (we ask for it) |
| 🟠 | **Nudge to prepaid** (₹40 off, COD still allowed) | 40%+, or 2+ past refusals |
| 🔴 | **Hold / cancel** | fake name + junk address, or risk ≥ 75% (85% for first-time buyers) |

**On September 2026** (trained on Apr–Aug only, tested on 329 real orders): 43% of orders were touched, and they contained 70% of all RTOs. 5 orders were held, all of them real RTOs. Net **+₹3,264** against shipping everything, while "cancel if risk ≥ 40%" would have been **−₹19,383**.

📄 **Read first:** [`docs/business_memo.pdf`](docs/business_memo.pdf), the 2-page memo to the founder.

## Run it (≈ 5 minutes)

Needs Python 3.10+.

```bash
git clone <this repo> && cd rto-guard
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python scripts/train.py          # trains the model (2 seconds)
python scripts/backtest.py       # what the rules would have done in September
streamlit run app.py             # dashboard, order checker, WhatsApp simulator, rules editor
```

The app opens at http://localhost:8501 with four pages:

1. **Morning dashboard.** Yesterday's orders in each bucket, ₹ saved, ₹ lost, net ₹, the orders that need attention with reasons, and a 30-day trend.
2. **Check an order.** Type in any order and get the decision, the reason, and the ₹ comparison of all 4 actions.
3. **WhatsApp simulator.** Play the customer: reply "haan", "nahi", "address change karna hai" or gibberish, or fast-forward the clock to see the reminder and the 24-hour rule.
4. **Rules.** Change thresholds, discount and hard rules with sliders. September is re-run live so you see the ₹ effect, then **Save** writes to `config/rules.yaml`.

Other commands:

```bash
python -m scripts.compare_policies     # Ops vs Marketing vs our rules, in ₹, plus best/worst case
python scripts/decide.py --no-llm --json '{"customer_name":"asdf","address":".","city":"Patna","state":"Bihar","phone":"9876543210","order_value":899,"num_items":1,"customer_prior_orders":0,"customer_prior_rto":0,"utm_source":"meta_ads"}'
python scripts/decide.py --csv new_orders.csv --out decisions.csv
python -m pytest -q                    # 32 tests
```

### Optional: turn on the LLM

Everything runs without an API key, using rule-based checks. To let an LLM judge uncertain addresses, names and WhatsApp replies:

```bash
export LLM_PROVIDER=gemini   GEMINI_API_KEY=...        # free tier is enough
# or
export LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=...
# optional: export LLM_MODEL=<model name>
```

Answers are cached in `data/llm_cache.json`.

## Changing the rules (no code)

Open `config/rules.yaml` in any text editor, or use the **Rules** page in the app. Every setting has a comment explaining it in plain words: risk bands (separate for new and repeat customers), hard rules (on/off and action), ₹ assumptions, WhatsApp timings, discount, and language per state.

## How it works

```
order ─► clean (phone, pincode) ─► checks: name / address / phone ─► risk model ─► hard rules ─► risk bands ─► action + reason
                                    rules first; LLM only if unsure     logistic       config         config
                                                                        regression
                                                                                    └─► confirm / nudge ─► WhatsApp flow (mock API)
```

| Path | What |
|---|---|
| `rto/clean.py` | Fixes the raw export (duplicates, status spellings, phone formats) |
| `rto/checks.py` | Name/address/phone checks; the code-vs-LLM line is documented at the top |
| `rto/llm.py` | Small Gemini/Anthropic client with disk cache and fallback |
| `rto/model.py` | Risk model and reason phrases |
| `rto/economics.py` | ₹ impact of each action (formulas at the top) |
| `rto/engine.py` | The decision: hard rules → bands → reason |
| `rto/whatsapp.py` | Confirmation and prepaid flows, reply understanding, mock WhatsApp API |
| `app.py` | Streamlit app |
| `config/rules.yaml` | All business settings |
| `scripts/` | train, backtest, policy comparison, batch decide |

## Documents

- [`docs/business_memo.pdf`](docs/business_memo.pdf): memo to the founder ([markdown source](docs/memo.md))
- [`docs/whatsapp_flow.md`](docs/whatsapp_flow.md): message flow, languages, timings, discount maths
- [`docs/decision_log.md`](docs/decision_log.md): important choices, alternatives considered, and why
- [`docs/ai_usage_log.md`](docs/ai_usage_log.md): AI tools used and where they went wrong

## Honest limits

- The ₹ effect of WhatsApp confirmation and prepaid nudges rests on assumptions: 40% of would-be RTOs caught, 3% of genuine buyers dropping off, 20% taking the prepaid offer. The memo shows best and worst cases. The first thing to do live is a 10% control group to measure the real numbers.
- 1,987 orders is small. The model is deliberately simple (AUC 0.74 on September) and should be retrained weekly once live.
