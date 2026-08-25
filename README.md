# robinhood-paper-desk

An agentic **paper-trading** system for liquid US stocks and ETFs.
Deterministic Python computes; the AI agent orchestrates, reads news, and
writes decision memos; a simulator is the only broker.

> **PAPER ONLY.** There is no code path to a real brokerage anywhere in this
> repository. Nothing here is financial advice; it is a learning/research
> system trading simulated money.

## How it works

```
GitHub Actions (daily, 21:15 UTC weekdays)          Claude session (agent)
──────────────────────────────────────────          ─────────────────────────
1. pytest (never trade on broken code)              reads signal_packet.json
2. fetch daily OHLCV  (yfinance → Stooq)            + scorecard via raw GitHub
3. fill yesterday's queued orders at today's OPEN   writes decision memo
   (5 bps slippage, hard risk checks)               uses WebSearch for news
4. score universe: 3-pillar cards (-6..+6)          reviews/approves proposals
5. propose orders from score bands                  iterates strategy via PRs
6. commit data/ + state/ back to the repo
```

- **Fills happen at the next session's open**, never the signal bar's close —
  no lookahead by construction.
- **Risk limits live in `sim/risk.py` + `config.yaml`** and reject orders
  outright: position/sector caps, cash floor, trade count, minimum holding
  period, and a 15% drawdown kill switch that goes flat and halts.
- **Advisory vs autonomous** (`strategy.mode` in config.yaml): advisory parks
  proposals in `state/proposed_orders.json` for approval
  (`python run_daily.py --approve`); autonomous queues them directly.
- **News is reference, not signal**: headlines ride along in the signal
  packet for the agent's memo; they never touch the deterministic score.

## The three pillars

The scoring engine (`engine/indicators.py`, `engine/macro_pillar.py`,
`engine/score.py`) is vendored from
[Oft3r/agentic-trading-desk](https://github.com/Oft3r/agentic-trading-desk)
(MIT — see `engine/LICENSE.agentic-trading-desk`): Trend (EMA structure),
Momentum (RSI/MACD/TRIX), Macro-Sentiment (RSP/SPY, IWM/SPY, HYG/LQD,
SPY/TLT, XLY/XLP regime), each −2..+2, composite −6..+6, plus an
exhaustion/rebound decision layer.

## Run it

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q                          # all math is tested
python run_daily.py --synthetic    # end-to-end demo, no network needed
python run_daily.py                # real daily cycle (needs internet)
python run_daily.py --no-fetch     # cache-only (e.g. sandboxed environments)
```

Key state files (committed by the daily workflow):

| File | What it is |
|---|---|
| `state/ledger.json` | portfolio source of truth (cash, positions, NAV history) |
| `state/trades.csv` | every fill AND rejection, with reasons |
| `state/signal_packet.json` | machine-readable daily snapshot for the agent |
| `state/scorecard.md` | human-readable daily scorecard |
| `state/proposed_orders.json` | advisory-mode proposals awaiting approval |

## Roadmap

- [ ] Phase 2: vectorbt backtests + walk-forward validation (`research/`)
- [ ] Equity-curve dashboard artifact
- [ ] News sentiment context from Robinhood MCP when running locally
- [ ] Strategy iteration loop: monthly review PRs driven by paper results
