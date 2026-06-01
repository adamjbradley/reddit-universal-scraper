# RedditRRAI_EA — MetaTrader 5 execution client

A **thin** EA that mirrors the project's `/signals` feed. It computes no alpha — it polls
the server and goes **long risk (AUDJPY / index CFDs)** while the RRAI **capitulation
overlay** is active (retail capitulates *and* VIX ≥ 18), flat otherwise.

> ⚠️ **Validation lives in the Python backtester.** MT5 is for **forward/demo execution
> only**. The signal is Phase-0 preliminary (single ~12-month regime, small n, overlapping
> windows). **Run on a DEMO account.**

## The signal it trades
`rrai_capitulation_long` — the one edge that survived friction-aware, regime-split
backtesting. Strongest/most sign-stable via **AUDJPY** (FX risk-on proxy), corroborated on
S&P 500 and Nasdaq 100. Fear-side only (euphoria-short failed). See the project's
`backtest/engine.py` and `analytics/signals.py`.

## Setup
1. **Server:** ensure the API is reachable and serving `GET /signals?format=mt5`
   (returns lines `SYMBOL,SIDE,STRENGTH,HORIZON`, or `# flat`).
2. **MetaEditor:** compile `RedditRRAI_EA.mq5` into `MQL5/Experts/`.
3. **Terminal:** Tools → Options → Expert Advisors → **Allow WebRequest for listed URL**,
   add your host (e.g. `http://192.168.1.50:8000`).
4. **Attach** to any chart and set inputs:
   - `SignalsUrl` → `http://<host>:8000/signals?format=mt5`
   - `AuthBearerToken` → only if your API requires it
   - `FxSymbol` / `IndexSymbol` / `TechSymbol` → **your broker's** symbol names for
     AUDJPY / S&P500 / Nasdaq100 (index CFD names vary; leave blank to skip an instrument)
   - `BaseLots`, `MaxHoldDays` (default 10), `PollSeconds` (default 300)

## Behaviour
- Polls every `PollSeconds`; opens a long when its instrument appears in the feed and no
  position is open (lots optionally scaled by signal `strength`).
- Closes when the signal clears **or** after `MaxHoldDays` (the ~10-day horizon).
- All positions tagged with `MagicNumber` so it only manages its own trades.

## Not included (deliberately)
- The equity **pump book** — it did not survive backtesting, so it is **not** served as a
  tradeable signal (only as an informational watchlist in the JSON feed).
- Server-side risk sizing beyond strength-scaling — set `BaseLots` and broker-side limits
  conservatively.
