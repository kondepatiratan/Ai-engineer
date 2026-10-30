# AI usage log

## Tools used

| Tool | What for |
|---|---|
| **Claude** (claude.ai, agentic workspace) | First pass at reading the brief, exploratory data analysis, writing most of the Python code (cleaning, model, engine, WhatsApp state machine, Streamlit app, tests), first drafts of the memo and docs |
| **Gemini / Claude Haiku via API** (inside the product) | Runtime judgement on uncertain addresses, names and WhatsApp replies (see `rto/checks.py`, `rto/whatsapp.py`). Optional; the system runs on rules without a key |
| Me | Choosing the approach (₹-based decisions, bands per customer type), checking every number in the memo against the scripts, reading model weights, running the app and backtests |

## Where the AI got it wrong or weak, and how I caught it

1. **A model weight that said first-time buyers are *safer*.** The first model version included `new_customer` alongside the `repeat_clean` and past-RTO signals. Training "worked", but `new_customer` came out at −0.25, meaning the model thought first-time buyers were safer. The raw data says the opposite (30% RTO for first-timers against 14% for clean repeat buyers). The cause: the signals overlapped, so one of them was redundant and its weight was meaningless. **Caught by** printing and sanity-checking every weight against the raw RTO rates. **Fixed by** removing the redundant signal. Two other signals with only 4–7 examples (fake name alone, pincode/city mismatch) also had weights pointing the wrong way, so they became hard rules instead.

2. **A hard rule that cancelled real customers.** The first config held every order with an invalid phone number, which sounds sensible since we can't WhatsApp them. The September backtest showed both orders it held were delivered: 2 genuine buyers lost, −₹1,683. **Caught by** looking at the per-rule ₹ breakdown in the backtest instead of just the total. **Fixed by** switching the rule off by default. The model already counts a bad phone in the risk score.

3. **Numbers in the memo draft that didn't match the data.** The draft said clean repeat customers fail "12%" (read off a per-group table by eye; the real figure is 14%). It also called ₹878 the average order value of the dataset, when that was September only (the whole sample is ₹920). Separately, the decision reasons said "Gorakhpur … 52% in our history" when the raw history is 62%; 52% is the *smoothed* estimate the model uses. **Caught by** recomputing every memo number in a script before finalising. **Fixed** the memo, and reworded the reason to "~52% of orders".

4. **Weak, not wrong: the "Rules" page wiped the instructions out of the config file.** The first save function rewrote `rules.yaml` from scratch, deleting all the comments that tell a non-technical person what each setting means. That defeats the point of an editable config. **Caught by** diffing the file after a test save. **Fixed by** a saver that only changes values in place and checks the result reloads identically.

5. **An over-confident ₹ assumption.** The first economics assumed people who switch to prepaid carry half of the order's risk. With that assumption, the "best ₹" action became "nudge" for almost everything above 20% risk, and confirmation was never chosen. **Caught by** printing which action wins at each risk level: one assumption was driving the whole design. **Fixed by** using a more cautious assumption (one-third), keeping confirmation for the 25–40% band, and showing a pessimistic / base / optimistic table in the memo instead of a single number.

## Not verified yet

- **The live LLM calls (Gemini / Anthropic) were only tested with a mocked HTTP response** (`tests/test_rto.py::test_llm_paths_with_fake_provider`). The build environment had no internet access. Before submitting I should run once with a real key (`LLM_PROVIDER=gemini GEMINI_API_KEY=… python scripts/decide.py --json '…'`) and check that the `check_source` field says `llm`.
