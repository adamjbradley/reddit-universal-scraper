# Trading Strategies — Living Document

A running catalogue of every signal/strategy we've considered, built, tested, or shelved —
with **status and evidence**, so we refine over time instead of re-litigating. Update the
changelog at the bottom on every material change.

> **Golden rule:** nothing earns risk until it survives **point-in-time features +
> friction-aware, regime-split backtesting**. The broad-sentiment edge looked great and
> died exactly this way — see `backtest/engine.py`.

## Status legend
| | Meaning |
|---|---|
| ✅ **Validated** | Survived friction-aware, regime-split backtest (still preliminary — see caveats) |
| 🟡 **Marginal** | Real effect, but fragile net of costs / not sign-stable |
| ❌ **Failed** | Tested and does not work (kept here so we don't retry it) |
| 🧪 **Untested** | Built and wired, awaiting data to evaluate |
| 💡 **Idea** | Backlog — not yet built |
| 🔄 **In progress** | Data/build task underway |

## Progress at a glance
_Last updated: 2026-06-01. Update this section whenever a status, metric, or next-step changes._

### Strategy scoreboard
| Strategy | Class | Status | Headline result | Next action |
|---|---|---|---|---|
| `retail_fear` (capitulation / bear-extreme / pessimism-fade) | Macro overlay | ✅ Validated | **+0.2–0.47pp/10d excess vs buy-&-hold**; AUDJPY best (win 88%); VIX gate ~4× | live via MT5 `RedditMacro_EA`; longer history; vol-target sizing |
| `rrai_momentum_long`, `froth_high_long` | Macro overlay | ❌ No edge | positive net but ~0 **excess vs buy-&-hold** (just bull drift) | dropped |
| `rrai_euphoria_short` + all macro shorts | Macro overlay | ❌ Failed | −1.5 to −3pp excess (shorting the bull) | dropped |
| `dump_fade_DELETION_short` | Equity | 🧪 Untested | awaiting coverage (`gone_frac>0` in 267 rows) | re-backtest at ~30–40% author coverage; express via puts |
| `fade_organic_short` | Equity | 🟡 Marginal | +0.83%/5d but P3 flips | retry via puts + VIX/vol gate |
| `pump_long` (ride) | Equity | ❌ Failed | +3% but regime-flips, t=0.75 | — drop |
| `dump_fade_short` (naive) | Equity | ❌ Failed | −9.9% (squeezed) | superseded by deletion-gated |
| `organic_long` | Equity | ❌ Failed | −4.2%, sign-stable loser | inverse → `fade_organic_short` |

### Build & data progress
| Component | Status | Detail |
|---|---|---|
| Feature store (`feature_daily` / `aggregate_daily`) | ✅ Done | ~77k ticker-day rows, point-in-time |
| Friction-aware backtester (`backtest/engine.py`) | ✅ Done | costs + regime split + no-lookahead |
| `/signals` feed (multi-strategy, json\|mt5) | ✅ Done | strategy-tagged; live, currently **flat** |
| Macro strategy R&D (`backtest/macro_research.py`) | ✅ Done | only `retail_fear` beats buy-&-hold; momentum/froth no edge |
| MT5 framework (`SignalClient.mqh` + `RedditMacro_EA.mq5`) | ✅ Done | strategy-selectable thin client; demo-only |
| Author profiling coverage | 🔄 8% | 3,823 / 47,625; 184 gone (116 del / 68 susp); target ~40% |
| Deletion-gated short testability | 🔄 Surfacing | `gone_frac>0` in 267 feature rows; too sparse to trade yet |
| Idea backlog | 💡 7 ideas | squeeze · options-flow · hype-cycle · sentiment-extremes · rotation · novelty · coordination |
| Equity/options paper client (Alpaca) | 💡 Planned | not started |

## Methodology (non-negotiables)
1. **No lookahead** — features at date D use only data ≤ D; entries at the first close *after* D.
2. **Frictions** — round-trip spread (small-caps wide), short borrow, halts, capacity.
3. **Regime split** — net return per sub-period; sign-stability is the bar, not a big average.
4. **Shorts via PUTS**, not naked stock — penny shorts have squeeze/borrow/halt tails.
5. **Overlapping-window caveat** — daily signals with N-day forward returns inflate t-stats.
6. **Benchmark macro vs buy-and-hold** — in a bull market "long anything" looks like alpha; a macro long must beat *unconditional drift* (excess), not zero. (Cost us 5 false "survivors".)

## Asset-class reality (what this data can/can't trade)
- **Small/micro-cap & meme equities** — primary signal (retail flow moves price).
- **Single-name options** — primary *expression* (convex longs, defined-risk shorts).
- **Index / risk-FX** — *macro overlay only* (retail positioning as a contrarian factor).
- **FX majors / "Reddit sentiment on SPY as direction"** — no edge; don't build it.

---

## 1. Macro overlay — Retail Risk-Appetite Index (RRAI)
Aggregate retail positioning as a **contrarian** factor at extremes. Built posts-only +
ratios (coverage-stationary); 90-day trailing percentile. `analytics/features.py::aggregate_daily`.

> **Macro R&D verdict (`backtest/macro_research.py`, excess over buy-and-hold).** A bull
> market made *every* macro long look like a winner and *every* short a loser — until
> benchmarked against unconditional drift. Net of that, only the **contrarian retail-fear**
> family beat buy-and-hold. RRAI-momentum and froth had ~0 excess (pure drift); all shorts
> were −1.5 to −3pp (fighting the trend). **One real macro edge, three formulations of it.**

### ✅ `retail_fear` — buy risk when retail capitulates *(served live)*
- **Thesis:** retail max-bearish (RRAI pct low / bear-share extreme) + genuine fear (VIX high) → contrarian bounce. Captured 3 ways (capitulation, bear-extreme, pessimism-fade) — all positive-excess, mutually corroborating.
- **Spec (shipped):** long when `rrai_pct ≤ 0.15` **AND** `VIX ≥ 18`; hold ~10d; size by extremity; **fear-side only**.
- **Evidence (12mo, net, 10d):** **excess over buy-&-hold** SPY +0.17pp / QQQ +0.23pp / **AUDJPY +0.47pp** (win 88%); `bear_extreme` +0.24/+0.32/+0.36pp corroborates. **VIX gate ~4×'d it** (SPY +1.56%/10d in fear vs +0.42% calm). Modest **timing tilt**, not standalone alpha (buy-&-hold already won 66–75%).
- **Expression:** FX **AUDJPY** (best leg), index CFDs US500/USTEC. Served via `/signals` (strategy tag `retail_fear`); traded by `mql5/RedditMacro_EA.mq5` (demo).
- **Caveats:** single ~12mo bull regime, n≈43–66/bucket, overlapping windows; edge is small in absolute terms.
- **Next:** leverage-language enrichment (calls/puts, margin/YOLO); longer history (data-limited); vol-target sizing; combine capitulation+bear-extreme into one composite trigger.

### ❌ Macro candidates that failed (kept so we don't retry)
- `rrai_euphoria_short`, `bull_extreme_short`, `froth_high_short`, all momentum-shorts: **−1.5 to −3pp excess** — shorting a bull market.
- `rrai_up_momentum_long`, `froth_high_long`: **~0 excess** — just drift, no signal.

---

## 2. Equity cross-sectional (per ticker × day)
From `analytics/features.py::feature_daily` (velocity, accel, breadth, concentration/HHI,
sentiment trajectory, author young/gone/suspended mix, novelty).

### ❌ `pump_long` — ride concentrated promotion
- **Thesis:** concentrated mention spike (few authors, high volume) pops short-term.
- **Result:** net +3%/5d but **sign-flips** (P1 +15.8 → P3 −4.6), win 36%, t=0.75. The crude "+6.45%" was a no-lookahead/no-cost/single-regime artifact. **Not robust.**

### ❌ `dump_fade_short` — short a decelerating pump (naive)
- **Result:** net **−9.9%** (P1 −29%). Shorting pumps on deceleration gets squeezed/run over. Wrong trigger; brutal without defined risk.

### ❌ `organic_long` — buy broad attention spikes
- **Result:** sign-stable **−4.2%** net (t=−4.25, n=321). Buying broad/news-driven attention tops is a *consistent loser* → the inverse is the candidate (below).

### 🟡 `fade_organic_short` — fade broad attention spikes
- **Thesis:** broad attention spikes mean-revert down (inverse of the robust `organic_long` loss).
- **Result:** net +0.83%, but **flips in P3** (win 46%) — the reversion is real but shorting costs + recent weakening eat it. **Marginal.** Revisit via **puts** (cheaper than borrow) and with a VIX/vol gate.

### 🧪 `dump_fade_DELETION_short` — short a pump whose promoters vanished *(THE thesis)*
- **Thesis:** concentrated pump + promoters now **deleted/suspended** → manufactured → fade the collapse. This is the original A2 idea, and the *reason* we track account lifecycle.
- **Status:** built & wired; **awaiting author coverage**. As of 2026-06-01: 8% of 47k authors profiled, 184 gone accounts found (~4.7% base rate), `gone_frac>0` in 267 feature rows — surfacing but too sparse to intersect the pump filter yet. Background `author_age_backfill` sweep grinding; scheduler rebuilds features each cycle.
- **Next:** re-run `python main.py --backtest` as coverage climbs (~30–40%); consider prioritising profiling of authors on pump-candidate tickers to make it testable sooner. Express via puts.

---

## 3. Idea backlog (💡 not yet built)
- **Squeeze setup (long):** Reddit squeeze chatter (Shortsqueeze/SqueezePlays) **+ short-interest / float / borrow-fee** → squeezable. *Needs external SI/borrow data.*
- **Options-flow signal:** parse contract-level chatter (unusual_whales/options) → retail-gamma/positioning proxy. *Needs contract parsing.*
- **Hype-cycle lifecycle:** model attention as nascent→acceleration→peak→fade; long the *organic* acceleration, fade the *concentrated* peak. Breadth-vs-concentration is the discriminator.
- **Conditioned sentiment extremes (single-name):** linear sentiment is dead, but *extreme concentrated euphoria at a price high* (short) / *capitulation after a sharp drop* (bounce) may be non-linear edges.
- **Cross-sectional attention rotation:** rank meme universe by abnormal velocity z-score; long top-decile / short bottom-decile, beta-hedged with SPY; baskets from co-mention clusters.
- **Novelty / first-mention alpha:** the "discovery" moment (silent → sudden organic burst).
- **Coordination graph (short):** rigorous version of the deletion short — cluster density + temporal bursts + duplicate text + new-account% + gone%.

---

## 4. Data gaps blocking strategies
| Gap | Unblocks |
|---|---|
| **Intraday prices** (have EOD only) | timing pump/dump entries/exits (they move intraday) |
| **Short interest / float / borrow fee** | squeeze setup; short feasibility |
| **Options chains / IV** | options expression; options-flow signal |
| **Longer history** (data-limited to ~12mo) | true multi-regime validation of the RRAI overlay |
| **Author coverage** (8% → target ~40%+) | the deletion-gated short *(in progress)* |

## 5. Execution stack
- **`/signals` feed** (`analytics/signals.py`, `GET /signals` json\|mt5) — live, serves the capitulation overlay. ✅
- **MT5 EA** (`mql5/RedditRRAI_EA.mq5`) — thin client for the index/FX overlay; demo-only. ✅
- **Alpaca / IBKR paper client** — for the equity/options legs. 💡 planned.

---

## Changelog
- **2026-06-01** — Macro R&D + MT5 framework. Benchmarked candidates vs buy-and-hold: only the
  `retail_fear` contrarian family beats it (best AUDJPY +0.47pp/10d excess); momentum/froth = no
  edge, all shorts fail. Built `SignalClient.mqh` + `RedditMacro_EA.mq5` (strategy-selectable),
  multi-strategy `/signals` feed; retired `RedditRRAI_EA.mq5`.
- **2026-06-01** — Initial document. Phase 0 (feature store + friction-aware backtester) shipped.
  RRAI capitulation-long validated (best via AUDJPY, VIX-gated); pump-ride/naive-dump-short/organic-long failed;
  fade-organic marginal; deletion-gated short awaiting author coverage (8%). `/signals` + MT5 EA live.
