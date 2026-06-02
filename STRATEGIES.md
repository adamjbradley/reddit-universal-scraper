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
| `retail_fear` (capitulation / bear-extreme / pessimism-fade) | Macro overlay | ✅ Validated | **excess vs buy-&-hold**, generalises across a risk-on basket: Gold +0.6 / AUDJPY +0.5 (win 88%) / SP500 +0.2pp robust; Silver +2.2pp lumpy; VIX gate ~4× | live via MT5 `RedditMacro_EA`; vol-target sizing; longer history |
| `rrai_momentum_long`, `froth_high_long` | Macro overlay | ❌ No edge | positive net but ~0 **excess vs buy-&-hold** (just bull drift) | dropped |
| `rrai_euphoria_short` + all macro shorts | Macro overlay | ❌ Failed | −1.5 to −3pp excess (shorting the bull) | dropped |
| `dump_fade_DELETION_short` | Equity | ✅ Thesis validated (forensic) / ⚠️ not yet PIT-tradeable | **gone≥0.20 → +14.3%/10d short, 83% win, t=2.57, +ve in all 3 regimes**; control proves the gate flips the trade (un-gated pump short *loses* −10.8%, t=−2.22) | make tradeable: finish author sweep to power the PIT `young_frac` gate (now n=3–7, inconclusive); express via puts |
| `distribution_short` (concentrated pump + sentiment–price divergence) | Equity | 🟡 Promising — **PIT-tradeable** | concentrated pump (per_author≥2, m≥10) + sentiment≥0.4 + 5d price already rolling over → **+7.2%/10d net short, t=2.70**; **date-clustered t=2.52 (47 indep. dates), dose-response in sentiment, +ve all 3 sub-periods, survives 2× costs**; control (same pumps, no divergence) *loses* −4.1% | OOS-collect forward; tighten `per_author` (some ETF/large-cap leak in); express via puts; promote on more episodes |
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
| Author profiling coverage | 🔄 20% | 30,015 / 152,040; 2,973 gone (2,087 del / 886 susp); target ~40% |
| Deletion-gated short testability | ✅ Forensically validated | gone≥0.20 short +14.3%/10d, t=2.57 (n=30); PIT `young_frac` proxy still sparse (n=3–7) |
| Short-interest / squeeze data (FINRA Reg SHO) | ✅ Done | `scraper/finra.py`: 475k rows / 5,693 tickers; **z-score** screen (abs level is MM noise, median 0.49) |
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
- **Instrument scan (17 MT5 instruments, 10d excess vs buy-&-hold):** the signal **generalises across the risk-on basket** — Silver +2.22pp (lumpy, one big rally → size small), Gold +0.59pp (robust, +ve every regime), AUDJPY +0.47pp (steadiest, win 88%), SP500 +0.17pp. Oil (+1.82pp but one-episode) and crypto (BTC net-negative) **dropped**. Generalisation across assets is itself evidence it's a real risk-appetite signal.
- **Expression:** a **diversified risk-on basket** — AUDJPY + Gold (XAUUSD) + US500/USTEC + small Silver (XAGUSD). Served via `/signals` (tag `retail_fear`); traded by `mql5/RedditMacro_EA.mq5` (demo).
- **Caveats:** single ~12mo bull regime; **46 cap-days = only ~13 distinct fear episodes** (clustered → effective n small); overlapping windows; edge small in absolute terms.
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

### ✅ `dump_fade_DELETION_short` — short a pump whose promoters vanished *(THE thesis — forensically validated)*
- **Thesis:** concentrated pump + promoters now **deleted/suspended** → manufactured → fade the collapse. The original A2 idea, and the *reason* we track account lifecycle.
- **Result (2026-06-02, 20% author coverage, h=10, market-neutral, 75bps + 8bps/day borrow):**
  | gate | n | net short | win | t | regimes |
  |---|---|---|---|---|---|
  | `gone_frac≥0.10` | 33 | **+10.0%** | 81.8% | 1.51 | −1.2 / +19.9 / +11.4 |
  | `gone_frac≥0.20` | 30 | **+14.3%** | 83.3% | **2.57** | +11.4 / +27.7 / +3.9 |
  | **control:** pumps w/ promoters *still here* (`gone=0`) | 202 | **−10.8%** | 36.1% | −2.22 | −30.7 / −0.6 / −1.1 |
  - The gate **flips the sign of the trade** (~+25pp spread). Un-gated concentrated pumps are *un-shortable* (they continue / squeeze); only the ones whose promoters vanish collapse. The deletion is doing the work, not pump-selection. Positive across all 3 regimes at gone≥0.20.
- **⚠️ Not yet point-in-time tradeable.** `gone_frac` uses *hindsight*: Reddit exposes no deletion timestamp, so "gone" means "gone as of our latest sweep" — unknowable at the pump date. The PIT-available leading proxy is `young_frac` (account age at post time), but it's currently too sparse to conclude (n=3–7, and the few events lean the wrong way).
- **Path to tradeable:** (1) **finish the author sweep** (20%→40%+) to power the `young_frac` PIT test; (2) failing that, a **live deletion-monitor** rule — `author_status_revalidate` already detects deletions forward; short on deletion-*confirmation* within K days of a fresh pump, capturing the back half of the collapse. Express via **puts** (defined risk vs squeeze tails; many are HTB/no-borrow).

### 🟡 `distribution_short` — concentrated pump + sentiment-price divergence *(NEW, point-in-time tradeable)*
- **Thesis:** the "bagholder distribution" moment — a *manufactured* pump (concentrated chatter) where **price has started rolling over but the crowd is still euphoric**. Insiders distribute into the retail bid; sentiment lags price. Every input (`per_author`, `mentions`, `sentiment`, trailing price return) is known at signal time → **unlike the deletion gate, this is actually tradeable live.** (`backtest/microstructure.py`)
- **Rule:** `per_author≥2 AND mentions≥10 AND sentiment≥0.4 AND 5d-trailing-return<0` → SHORT. h=10, market-neutral, 75bps + 8bps/day borrow.
- **Result (2026-06-02):** **+7.2%/10d net, t=2.70, win 52%, n=60** across **39 distinct tickers**.
  - **Date-clustered test** (1 obs/date, kills intra-day correlation): **+7.6%, t=2.52, 47 independent dates** — *not* a risk-off-day artifact (max 3 events on any date, spread over 12 months).
  - **Dose-response** in sentiment (s≥0.3/0.4/0.5 → +3.9/+7.2/+11.1%) and **monotone in horizon** (h=5/10/20 → +5.6/+7.2/+9.5%, t rising to 2.92) — the hallmarks of a real effect, not a fit.
  - **Survives 2× costs** (150bps spread + 16bps/day borrow → still +4.9%/10d).
  - **Control:** same concentrated pumps *without* the divergence (price up) → **−4.1%** — the divergence is doing the work. Positive in all 3 sub-periods.
- **Why it works where the others don't:** the gate is *precise* — it only fires inside the manufactured subset. The broad high-attention universe **drifts flat-to-up** (gross ≈ +0.04%), so generic pump-shorting just pays costs (see negatives below); the edge requires isolating the distributing pumps, which both this and the deletion gate do.
- **Caveats / next:** n still modest (47 indep. dates); single ~12-mo window (sub-periods, not true bull/bear regimes); small-cap-heavy so no multi-regime price history; loose `per_author≥2` lets some ETFs/large-caps (QQQ/IWM) leak in — tighten to ≥3 and/or split small-cap vs index. **Collect forward OOS** (the clock starts now); express via **puts**. Not yet promoted to the live `/signals` feed.

### ❌ Negatives from the same study (recorded so we don't re-run them)
- **Broad-universe microstructure timing does NOT survive costs.** Recruitment-exhaustion, mention-deceleration, and sentiment-price divergence applied to the *whole* high-attention set (mentions_z≥1) are all **net-negative** — the universe drifts flat-to-up, so shorting it generically just bleeds the ~2.3% friction. Precision (concentration filter) is mandatory.
- **Recruitment-exhaustion alone = null.** New-author inflow rolling over is directionally supportive *within* concentrated pumps (+1.8% net, but t=0.33) and adds to the stack, but is not a standalone edge at current coverage. The new-author feature is built and kept.
- **Up-leg continuation long = lottery.** "Ride while recruiting + accelerating" netted +1.6% but win 27% with regimes [+21/−7/−9] — one big winner, otherwise negative. Not stable; dropped.

---

## 3. Idea backlog (💡 not yet built)
- **Squeeze setup (long):** Reddit squeeze chatter (Shortsqueeze/SqueezePlays) **+ short pressure** → squeezable. *Partially built:* `scraper/finra.py` + `squeeze_screen()` flag tickers with an anomalous **z-score jump** in daily short-ratio vs their own baseline (HTZ/GME currently surface). *Still needs* true SI%/float/days-to-cover (FINRA **bi-monthly short-interest** report) + borrow-fee for the level; the daily file's absolute level is market-maker noise. *Not yet backtested.*
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
| **Short interest / float / borrow fee** | squeeze *level* (FINRA daily short-*volume* now in via `scraper/finra.py`, but it's MM noise at the level; need bi-monthly SI%/float + borrow) |
| **Options chains / IV** | options expression; options-flow signal |
| **Longer history** (data-limited to ~12mo) | true multi-regime validation of the RRAI overlay |
| **Author coverage** (20% → target ~40%+) | a *point-in-time tradeable* deletion short — thesis already forensically validated; PIT `young_frac` proxy needs more coverage |

## 5. Execution stack
- **`/signals` feed** (`analytics/signals.py`, `GET /signals` json\|mt5) — live, serves the capitulation overlay. ✅
- **MT5 EA** (`mql5/RedditRRAI_EA.mq5`) — thin client for the index/FX overlay; demo-only. ✅
- **Alpaca / IBKR paper client** — for the equity/options legs. 💡 planned.

---

## ★ MULTI-REGIME VALIDATION (2020-2026, incl. COVID crash + 2022 bear)
The deep-history backfill let us test across regimes — the result that actually moves confidence:
- **AUDJPY capitulation-long is REGIME-ROBUST** — positive in EVERY year 2020-2026, **including the
  2022 bear** (+0.7%, win 60%), win rates 60-93%. Graduates from preliminary → **validated**. This is
  the core tradeable edge.
- **Equity (SPY) capitulation-long FAILS in a sustained bear** (2022: -0.7%/10d, win 43%) — falling-knife
  problem → **must gate with the 200-DMA trend filter** (don't buy fear in a downtrend). Confirmed needed.
- **Euphoria-short only works in the bear** (2022 +1.3%; loses every bull year) → shorts are
  **regime-conditional**, switch on only in confirmed downtrends. (Vindicates not building shorts earlier.)
- Caveats: small n/year (18-60), overlapping windows, historical RRAI is a WSB-heavy sampled backfill.

## Session findings — sector / calendar / latency (2026-06-01)
- **Sector segmentation rescues the pump signal** (`backtest/sectors.py`). The broad pump was a
  wash because it *mixed opposites*: **small-cap concentrated pumps FADE** (−14.6%/10d excess,
  win 22%, **t=−3.93** — pump-and-dumps reliably reverse → a short/avoid), while **biotech pumps
  are a fat-tailed lottery** (+66% mean, win 48%, t=2.23 — a few catalyst moonshots). Thematic
  universes (ai/quantum/space/nuclear/meme/frontier) had too few concentrated-pump events to judge.
- **Pump detection is TOO SLOW** (`feature_daily`). Median `novelty_days` at flag = **152**; only
  **1%** of pumps flagged within 3 days. Root cause: the `min_total>=20` universe filter + trailing
  z-windows mean a ticker must accumulate ~20 mentions before it's even eligible → we catch
  *established-ticker spikes*, not fresh pump-and-dumps. Only **33 fresh day-0 bursts** exist in the
  data. **Fix:** a fresh-burst fast-path that scans raw mentions (ticker's first day with a burst),
  bypassing the 20-mention gate — for LIVE catching, not just post-hoc.
- **Calendar effects (10y, SPY/QQQ/Gold; absent in AUDJPY-FX):** turn-of-month is real
  (SPY +0.49 vs +0.20%/5d, QQQ +0.58 vs +0.35, Gold +0.38 vs +0.25); September weak (−0.2%);
  OpEx week underperforms. Generic equity-flow seasonality → useful as *overlays/filters*, not Reddit alpha.
- **AU sentiment track** building (`au_aggregate_daily`, post-level) to test AU-sentiment → AUDJPY.

## ★ MASTER BACKTEST vs BUY-AND-HOLD (`backtest/run_all.py`, full 2020-2026, 10d, net of costs)
Every signal judged on EXCESS over its instrument's unconditional drift (buy-and-hold-anytime).
| Strategy | n | net% | B&H% | excess | win | verdict |
|---|---|---|---|---|---|---|
| retail_fear **AUDJPY** | 338 | +0.53 | +0.27 | **+0.25** | 69% | beats (modest, most robust) |
| retail_fear SPY | 338 | +0.54 | +0.59 | −0.05 | 61% | **no edge** |
| retail_fear Gold | 338 | +0.83 | +0.72 | +0.10 | 57% | marginal |
| euphoria_short SPY | 351 | −1.26 | +0.59 | −1.84 | 27% | no edge |
| smallcap_pump_fade | 87 | +9.07 | +0.59 | +8.48 | 64% | beats but single-regime + brutal costs |
| biotech_catalyst | 91 | +22.2 | +0.59 | +21.6 | 36% | fat-tailed LOTTERY (low win) |
| attention_fade | 252 | −0.73 | +0.59 | −1.31 | 40% | no edge |

**Recalibration:** with a proper buy-and-hold benchmark over the full period, the AUDJPY edge is a
**modest +0.25pp/10d** (earlier raw "positive every year" overstated it - that was largely drift).
SPY capitulation does NOT beat buy-and-hold. The big-excess equity signals are single-regime
(smallcap) or low-win lotteries (biotech). Net: one modest-but-real macro edge, several promising-
but-unvalidated screens. Do NOT size up on these as-is.

## DATA WE NEED (to validate/strengthen — the binding constraints)
| Data | Unblocks | Why we lack it |
|---|---|---|
| **Intraday prices** (we have EOD only) | pump entry/exit timing; fresh-pump fade execution (pumps move intraday) | not collected |
| **Historical penny-stock prices + per-ticker features** | multi-regime validation of smallcap_pump_fade & biotech (only 2025-26 exists) | delisted pennies → Yahoo gaps; per-ticker feature_daily only recent |
| **Short interest / float / borrow fee** | squeeze setups; realistic short cost/feasibility | FINRA daily short-*volume* in (`scraper/finra.py`); still need SI%/float/borrow (Ortex / FINRA bi-monthly SI) |
| **Options chains / IV** | express fades via PUTS (only sane way to short pennies); options-flow signal | not collected |
| **More years of RRAI history** | tighten the macro edge (effective n ≈ a few dozen fear episodes) | Reddit/arctic-shift depth ~2017+ |
| **Broader author coverage** (~20% profiled) | a *PIT-tradeable* deletion/young-account short (thesis already validated forensically; `young_frac` proxy starved) | rate-limited Reddit /about sweep |
| **Independent sentiment sources** (StockTwits, news, Discord, crypto + AU subs) | independent episodes → real effective-sample growth without waiting calendar time | not integrated (AU track building) |
| **Real transaction-cost data** (per-instrument spreads/borrow) | trustworthy net returns (small-cap costs are modelled/optimistic) | not sourced |

## Update — complete-data confirmation (2026-06-01)
- US backfill finished: continuous 2020-2026 (2,344 days, 341 capitulation days). **Capitulation-long
  multi-regime result HOLDS on the complete series** — AUDJPY positive every year incl. 2022 bear (win 60-83%).
- `smallcap_pump_fade` is strong (+12.4% excess short, win 77%) but **only 2025-26 data exists** (n=87) —
  no historical penny-stock prices/features, so it **can't be multi-regime validated** → stays ADVISORY,
  NOT promoted to tradeable. (Single-regime; can't be fixed by backfill — penny price history doesn't exist.)

## Changelog
- **2026-06-02** — **New PIT-tradeable short found: `distribution_short`** (`backtest/microstructure.py`).
  Built the missing **new-author recruitment** feature and tested demand-quality timing signals. The
  decisive lesson: microstructure timing (recruitment-exhaustion / deceleration / divergence) is **worthless
  on the broad attention universe** (it drifts flat-to-up, costs dominate) but **powerful inside the
  concentrated/manufactured subset** — exactly where the deletion edge lives. Winner = concentrated pump +
  euphoric sentiment + price already rolling over → **+7.2%/10d net, t=2.70 (date-clustered t=2.52, 47 indep.
  dates), dose-response, survives 2× costs**; control loses −4.1%. Unlike the deletion gate it uses **no
  hindsight** → tradeable. Recorded negatives (broad timing, recruitment-alone, up-leg ride) to avoid re-runs.
- **2026-06-02** — **Deletion-gated short forensically validated.** Author coverage 8%→20% (2,973 gone).
  Re-backtest: `gone_frac≥0.20` → +14.3%/10d short (83% win, t=2.57, +ve all regimes); the *control*
  (un-gated concentrated pumps) *loses* −10.8% — so the deletion gate flips the trade, confirming the
  A2 thesis. **Caveat:** `gone_frac` is hindsight (no Reddit deletion timestamp) → not yet PIT-tradeable;
  the `young_frac` PIT proxy is still too sparse (n=3–7). Also shipped `scraper/finra.py` (FINRA Reg SHO
  daily short-volume, 475k rows) + z-score `squeeze_screen()` — learned the *absolute* short-ratio is
  market-maker noise (median 0.49), so the screen uses per-ticker z-score anomaly + chatter.
- **2026-06-01** — Instrument scan (17 MT5 instruments). `retail_fear` generalises across the
  risk-on basket; commodities express it best (Silver +2.2pp lumpy, Gold +0.6pp robust), AUDJPY
  steadiest. Oil/crypto dropped. Basket = AUDJPY + Gold + indices + small Silver; EA + feed updated.
- **2026-06-01** — Macro R&D + MT5 framework. Benchmarked candidates vs buy-and-hold: only the
  `retail_fear` contrarian family beats it (best AUDJPY +0.47pp/10d excess); momentum/froth = no
  edge, all shorts fail. Built `SignalClient.mqh` + `RedditMacro_EA.mq5` (strategy-selectable),
  multi-strategy `/signals` feed; retired `RedditRRAI_EA.mq5`.
- **2026-06-01** — Initial document. Phase 0 (feature store + friction-aware backtester) shipped.
  RRAI capitulation-long validated (best via AUDJPY, VIX-gated); pump-ride/naive-dump-short/organic-long failed;
  fade-organic marginal; deletion-gated short awaiting author coverage (8%). `/signals` + MT5 EA live.
