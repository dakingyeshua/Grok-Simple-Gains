# Grok-Simple-Gains

Simple Gains Trading — a self-contained **paper/sim** engine for Aaron Johnson’s equity opening-range breakout / opening-range reversal (ORB/ORR) rules.

**Philosophy:** quality over quantity. Consistent gains over greedy wins.

This version never places a live brokerage order. There are no Webull API calls that send tickets. The broker port is intentionally small so a real Webull adapter can be added later without rewriting Scout, Grader, Risk Officer, or Journal.

---

## What this is (and is not)

| This version does | This version does not |
| --- | --- |
| Paper fills at the 5-minute close | Live tickets of any kind |
| HITL alerts instead of auto-fill | Invent live account balances |
| Finnhub **market data** (quotes/candles/news) | Webull order routing |
| Fixture replay with no API key | Scale-in or +2R scale-out |
| Dashboard + CLI daily flow | Premarket orders |

The `live` mode is a **stub**. It refuses every order and will not fabricate a cash or equity figure.

---

## How to run

Python 3.11+. From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Initialize the local paper book (default starting equity **100000**):

```bash
simple-gains init
```

### Paper a session from fixtures (no API key)

```bash
simple-gains --fixtures session
simple-gains --fixtures status
simple-gains --fixtures journal
simple-gains --fixtures serve
```

`--fixtures` loads `tests/fixtures/session_orb.json` (session date `2024-03-15`) and freezes the clock to 10:00 America/Chicago so the entry window is open.

### Paper a session with Finnhub

```bash
export FINNHUB_API_KEY=your_key
simple-gains scan --tickers AAPL,NVDA,TSLA
simple-gains session --tickers AAPL,NVDA,TSLA
simple-gains serve
```

### Daily flow

1. **Scan** (premarket context from ~3:00 AM CDT) → watchlist. No orders.
2. **Watchlist** names sit until the first 15-minute regular-session candle is complete.
3. **Trigger** is a 5-minute candle that **closes** above that first 15-minute high. Wicks do not count.
4. **Grader** scores Scout survivors only (100-point model).
5. **Risk Officer** sizes, gates, or vetoes. A veto is final for that ticker that day.
6. **Paper fill** (or HITL alert) at the 5-minute close. Full size. One ticker, one ticket.
7. **Journal** records signal / skip / fill / stop / trail / breaker / veto.
8. After **11:00 AM CDT**, only manage open paper positions (trail / stop-out).

Useful commands:

```bash
simple-gains grade AAPL
simple-gains manage
simple-gains postmortem --text "what I did wrong"
simple-gains weekly-review --text "week notes"
simple-gains authorize          # after a portfolio stop only
simple-gains --mode hitl --fixtures session
simple-gains --mode live status # stub — refuses orders
```

Tests (no live key):

```bash
pytest
```

---

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `FINNHUB_API_KEY` | unset | Market data only. Tests and `--fixtures` run without it. |
| `SIMPLE_GAINS_DB` | `./data/simple_gains.sqlite` | Journal + paper book |
| `SIMPLE_GAINS_MODE` | `paper` | `paper` \| `hitl` \| `live` |
| `SIMPLE_GAINS_STARTING_EQUITY` | `100000` | Used when the book is first created |
| `SIMPLE_GAINS_SLIPPAGE_BPS` | `0` | Extra adverse fill on paper market orders |
| `SIMPLE_GAINS_HOST` / `SIMPLE_GAINS_PORT` | `127.0.0.1` / `8000` | Dashboard bind |

Copy `.env.example` to `.env` if you want these loaded automatically.

---

## Session clock

All product times are **America/Chicago** (CST/CDT).

- Premarket ~**3:00 AM** CDT: scan and context only. **Never orders.**
- Regular session / new entries: **9:30 AM–11:00 AM CDT**.
- First 15-minute candle: **9:30–9:45** America/Chicago. That bar **is** the opening range.
- After **11:00 AM CDT**: manage open paper positions only (mechanical trail). No new entries.
- US cash close used for bar filtering: **3:00 PM CDT** (4:00 PM ET).

This is Aaron’s specified Chicago clock, not a silent conversion to 9:30 ET.

---

## Four lanes (hard separation)

1. **Scout** — hunt + hard pre-filters only. Pass/fail. Does not score. Does not size.
2. **Grader** — 100-point conviction on Scout survivors only. Does not hunt. Does not size.
3. **Risk Officer** — size + gates + veto. A veto is final for that setup that day. Grader cannot rescore a veto into a pass the same day.
4. **Journal** — append-only. Rejects any Grader card that lacks the six-bucket split.

Patterns of interest (heuristic + HITL override fields on the Grader card): Inverted Head & Shoulders, Cup & Handle, clean daily / 15-minute levels.

---

## Hard pre-filters (fail any one = out, never scored)

1. On today’s scan / watchlist
2. Regular session before 11:00 AM CDT
3. First 15-minute candle complete
4. Liquid enough (see below)
5. Not halted
6. Cluster slot still open (max **4** concurrent open names; one ticker, one full-size ticket)
7. Not a chase: fail if last price is already **≥ 0.8 × ADR** above the 15-minute high **before** any 5-minute confirmation close

### Liquidity (documented v1)

- Min last price: **$5.00**
- Min 20-day ADV: **1,000,000** shares
- Max quote spread: **0.50%** of mid

---

## Locked 100-point model v1

Do not change these weights.

| Bucket | Max |
| --- | ---: |
| Level / pattern | 25 |
| RS vs SPY (and QQQ if NASDAQ) | 20 |
| Volume | 20 |
| Catalyst | 15 |
| Daily 20 EMA | 10 |
| Opening-range quality | 10 |

**Tiers** — never round up:

| Total | Tier | Mapped risk |
| --- | --- | --- |
| &lt; 85 | skip | 0% |
| 85–89 | A | 1.0% |
| 90–94 | A+ | 1.5% |
| 95–100 | S | 2.0% |

S-tier must be rare. The engine **flags** (does not auto-veto) if three S names print in a session.

The **5-minute close** is the trigger, not the score. Volume / RS / EMA / OR-quality buckets are mechanical from data. Pattern and catalyst may use heuristics plus HITL override fields (`hitl_level_override`, `hitl_catalyst_override`).

Every stored card has: ticker, date, session, pre-filter pass list, six bucket scores, total, tier, mapped risk %, theme/sector, SPY/QQQ headwind note, decision (`S` / `A+` / `A` / `skip`).

---

## Locked risk constitution v1

Do not change these numbers.

- Size off **equity**, never buying power:  
  `shares = (equity × risk%) ÷ (entry − initial stop)`  
  with a **5 bps** slippage buffer added to the planned stop distance.
- Initial stop = **5-minute confirmation candle low**.
- Recalc on the actual fill. If real risk exceeds the **2%** ceiling, cut size immediately.
- Max combined open risk: **6%** of equity.
- Max **2** names in the same sector/theme. Breach is a **skip**, not a resize.
- **Violent against-tape → no new longs.** Testable rule: SPY session return from the system regular open is **≤ −0.60%** and the latest completed 5-minute SPY candle is red. NASDAQ names apply the same test to QQQ as well.
- Breakers halt **new entries only**. Open trades keep the mechanical trail — except the portfolio stop, which flattens everything.

### Breakers

| Breaker | Rule |
| --- | --- |
| Daily | −10% from start-of-day equity, including open P&amp;L |
| Streak | 2 consecutive closed losses |
| After a day-stop | **Next session off** + written post-mortem required (`simple-gains postmortem`) |
| Weekly | −6% from Monday opening equity until `weekly-review` is filed |
| Portfolio | −20% from high-water: flatten all, stay flat until `authorize` |

### Stops (full size, stop only tightens)

`R = fill − initial stop`

1. Entry-candle (confirmation) low
2. Prior-day low once last price is above the initial stop **and** PDL is above that initial stop
3. Daily 20 EMA once **extended**: unrealized R ≥ **1.5** **or** last price ≥ daily 20 EMA × 1.012
4. Once **+2R**, the live stop may not sit below **+1R**

### Sanity cap (tiny stops cannot balloon share count)

- Planned stop distance (after buffer) must be at least **max($0.10, 0.20% of entry)** or the Risk Officer **vetoes** (`micro_stop_sanity_cap`).
- Notional may not exceed **25%** of equity.

---

## Broker modes

| Mode | Behavior |
| --- | --- |
| `paper` (default) | Market-style fill at the 5-minute close ± slippage bps |
| `hitl` | Same book, but a new entry writes an alert instead of filling |
| `live` | `WebullStubBroker` — refuses to send orders, invents no balances |

Implement a future live adapter against `simple_gains.broker.base.Broker`. Do not call Risk Officer with buying power.

---

## Layout

```
simple_gains/
  lanes/          Scout, Grader, Risk Officer, Journal
  broker/         paper, HITL, Webull stub
  data/           Finnhub + fixtures
  clock.py        America/Chicago calendar
  config.py       locked weights and constitution
  engine.py       daily flow (does not score or size itself)
  dashboard.py    FastAPI UI
  cli.py
tests/            fixture session + constitution tests
```

Journal and state persist in SQLite (`data/simple_gains.sqlite` by default).

---

## What is not live yet

- No Webull (or any broker) order ticket
- No live buying-power or real-account sync
- No production news NLP — catalyst is headline presence + HITL override
- Pattern labels are heuristics, not a full CV/vision model
- No overnight / futures session
- No short-sale / ORR short engine in this version (long ORB path is implemented)

Paper a session, read the journal, and keep the constitution boring. That is the product.
