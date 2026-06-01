# MetaTrader 5 — macro overlay execution framework

Thin MT5 clients that **mirror** the project's `/signals` feed. They compute no alpha —
all strategy logic is server-side. Each EA picks one strategy from the feed and trades it
on FX/index CFDs.

> ⚠️ **Validation lives in the Python backtester.** MT5 is **forward/demo execution only**.
> The edge is a *modest timing tilt* over buy-and-hold (~0.2–0.5pp/10d, single ~12-month
> regime, small n). **Run on a DEMO account.**

## Files
| File | Role |
|---|---|
| `SignalClient.mqh` | Shared include — fetch/parse the feed + position helpers. Reused by every EA. |
| `RedditMacro_EA.mq5` | Strategy-selectable EA. Set `StrategyTag` to the strategy to trade. |

## Why only one strategy trades
Macro R&D (`backtest/macro_research.py`) benchmarked every candidate against **buy-and-hold**
(a bull market makes "long anything" look like a winner). Only the **`retail_fear`**
contrarian family beat it (capitulation / bear-extreme / pessimism-fade — same signal, three
formulations). RRAI-momentum and froth had **no edge vs buy-and-hold**; every short lost
(fighting the trend). So the feed serves `retail_fear` only. (Equity pump strategies can't
trade on MT5 at all — no micro-cap instruments; they're Alpaca-bound.)

### `retail_fear` (VALIDATED, served live)
Long a **risk-on basket** when retail capitulates (`rrai_pct ≤ 0.15`) **and** `VIX ≥ 18`;
~10-day hold; strength-scaled. An instrument scan (FX/commodities/indices/crypto) found the
signal **generalises across risk assets** — so spread, don't bet one:
- **AUDJPY** — steadiest (+0.47pp/10d excess, win 88%, +ve every regime)
- **Gold (XAUUSD)** — robust (+ve every regime)
- **US500 / USTEC** — stable
- **Silver (XAGUSD)** — highest excess (+2.2pp) but **lumpy/outlier-driven → size small**
- *(Oil and crypto were tested and dropped: one-episode / no edge.)*

## Setup
1. **Server:** API reachable, serving `GET /signals?format=mt5` (lines
   `STRATEGY,SYMBOL,SIDE,STRENGTH,HORIZON`, or `# flat`).
2. **MetaEditor:** put `SignalClient.mqh` + `RedditMacro_EA.mq5` in `MQL5/Experts/` and compile.
3. **Terminal:** Tools → Options → Expert Advisors → **Allow WebRequest for listed URL**,
   add your host (e.g. `http://192.168.1.50:8000`).
4. **Attach** `RedditMacro_EA` to any chart; set inputs:
   - `SignalsUrl` → `http://<host>:8000/signals?format=mt5`
   - `StrategyTag` → `retail_fear`
   - `AudJpySymbol` / `GoldSymbol` / `IndexSymbol` / `TechSymbol` / `SilverSymbol` → your broker's
     AUDJPY / XAUUSD / S&P500 / Nasdaq100 / XAGUSD names (blank = skip any)
   - `BaseLots`, `MaxHoldDays` (10), `PollSeconds` (300), `AuthBearerToken` (only if API requires it)

To run several strategies later, attach one EA instance per `StrategyTag` (all share the include).

## Behaviour
- Polls every `PollSeconds`; opens a long when its instrument appears in the feed for the
  selected strategy and no position is open (lots optionally scaled by `strength`).
- Closes when the signal clears **or** after `MaxHoldDays`.
- Positions tagged with `MagicNumber` so the EA only manages its own trades.
