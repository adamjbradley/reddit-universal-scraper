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
_Last updated: 2026-06-02. Update this section whenever a status, metric, or next-step changes._

### Strategy scoreboard
| Strategy | Class | Status | Headline result | Next action |
|---|---|---|---|---|
| `retail_fear` (capitulation / bear-extreme / pessimism-fade) | Macro overlay | ✅ Excess-validated + **MT5-tradeable (turn entry)** | **EXCESS vs B&H** robust (AUDJPY t(exc)=2.93, +ve 9/10 yrs; generalises across AUD/NZD FX + gold). Naive long-on-trigger *failed* in MT5 (PF 0.77), **but a turn-based entry + ATR stop fixes it: AUDJPY PF 1.06, Gold PF 1.54 / Sharpe 0.41** (ATR/Fixed sizing; Kelly oversizes). | forward OOS + small demo allocation; gold is the strongest leg |
| `rrai_momentum_long`, `froth_high_long` | Macro overlay | ❌ No edge | positive net but ~0 **excess vs buy-&-hold** (just bull drift) | dropped |
| `rrai_euphoria_short` + all macro shorts | Macro overlay | ❌ Failed | −1.5 to −3pp excess (shorting the bull) | dropped |
| `dump_fade_DELETION_short` | Equity | ❌ FAILS at full coverage (was a coverage-bias artifact) | After profiling **all 487 pump-authors (25%→100%)** the edge **flips negative**: gone≥0.20 → **−19.3%/10d (n=87)**, dominated by **squeeze tails** (IXHL −625%); PIT `young_frac` short is **−39% to −87%** (young pumps rip *hardest*). The earlier +14.3% (n=36 @ 25% cov) simply hadn't profiled the squeezers. Win rate still 64% but uncapped stock-short tails kill it. | **dead as a stock short**; only conceivable via **puts** (caps the −625% tail) — `distribution_short` is the better-behaved version (price-rolling-over filter dodges live squeezes) |
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
| Author profiling coverage | 🔄 24% overall / **100% of pump-authors** | 46,637 profiled, 6,153 gone; sweep now skips crypto-only + front-loads ticker-mentioners. **Targeted-profiled all 487 equity pump-authors.** |
| Deletion-gated short | ❌ **Failed at full coverage** | The +14.3% was a coverage artifact (25% sample missed squeezers). At 100% pump-author coverage: gone≥0.20 **−19.3%/10d** (IXHL −625% tail); young_frac short −39%→−87%. Stock-short dead; puts-only if at all. |
| Short-interest / squeeze data (FINRA Reg SHO) | ✅ Done | `scraper/finra.py`: 475k rows / 5,693 tickers; **z-score** screen (abs level is MM noise, median 0.49) |
| Independent fear-confirmation gate (Wikipedia) | 🟡→🟢 Strengthened on 2016-26 | **Extended series (525 caps, `backtest/fear_gate.py`):** **fear-only** excess SPY +0.57 (day t=1.64, **episode t=2.77**) / QQQ +0.80 (day t=1.91, **episode t=2.37**) — effect *grew* with the 2018 episodes & SPY/QQQ now **agree** ⇒ likely real (day-level still marginal). The LIVE **trend+fear still ≈0** (SPY +0.10/QQQ +0.11) → **trend filter should be dropped, use fear-only**. AUDJPY stays ungated (fear *hurts* it, +0.21→+0.09). |
| Live screens in `/signals` (`tradeable:false`) | ✅ Done | `smallcap_pump_fade` · `fresh_pump_alert` · `coordination_flag` · `biotech_catalyst` · `attention_fade` · `calendar_overlay` |
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
- **Risk-FX (AUD/NZD complex)** — the **primary macro expression**: retail capitulation → contrarian bounce, validated across the cross-section (AUDJPY/NZDJPY/AUDUSD/AUDCHF, t 2.7-3.2). The cleanest tradeable read of the risk-appetite factor RRAI measures.
- **Index / metals** — *macro overlay* (Gold robust; equities the weak expression, t≈1).
- **Haven/non-risk FX (USDJPY, CHFJPY, EURUSD) / "Reddit sentiment on SPY as direction"** — no edge; don't build it (they're the controls that confirm the factor).

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
- **★ Cross-sectional FX validation (2026-06-03, 2016-26, VIX-gated) — proves it's a RISK-ON-CURRENCY factor, not an AUDJPY fluke.** Ran capitulation-long across the yen-cross + risk-pair complex. The edge travels with the **risk-on currency (AUD/NZD)**, *independent of the funding leg* — and the **controls fail**, which is the real proof:

  | pair | character | excess | t(exc) |
  |---|---|---|---|
  | **NZDJPY** | risk-on JPY cross | +0.43% | **+3.18** |
  | **AUDJPY** | risk-on JPY cross | +0.48% | **+2.93** |
  | **AUDCHF** | risk vs CHF-haven | +0.41% | **+2.83** |
  | **AUDUSD** | risk pair (no JPY) | +0.43% | **+2.72** |
  | EURJPY | risk-ish | +0.25% | +2.70 |
  | NZDUSD | risk pair | +0.35% | +2.33 |
  | Nikkei ^N225 | high-beta JP *equity* | +0.62% | +1.93 |
  | **USDJPY** | JPY cross, USD~haven | +0.12% | **+1.02** (flat) |
  | **CHFJPY** | CONTROL haven/haven | +0.13% | **+1.37** (flat) |
  | **EURUSD** | CONTROL non-risk | +0.14% | **+1.44** (flat) |

  **Read:** every AUD/NZD pair is significant (t 2.7–3.2) regardless of funding leg; the three pairs with *no* risk-on leg (USDJPY/CHFJPY/EURUSD) are the three weakest. **It's not a JPY thing** — JPY is just a clean funding leg; the Nikkei works because it's high-beta *risk*, not because it's Japanese. Equities stay the weak expression (SPY t=0.90, QQQ t=1.13). The clean cross-section (right pairs fire, controls don't) is strong evidence of a genuine factor → **trade the basket, not one pair.**
- **🟡 Independent fear-confirmation gate (Wikipedia, LIVE — but now DOUBTFUL):** the **equity** legs (US500/USTEC) additionally require an *independent* fear spike — `fear_z ≥ 0.5`, the z-score of Wikipedia fear-page attention (Stock_market_crash / Recession / Bear_market views) vs the trailing ~30d — **AND** a 200-DMA uptrend. Original rationale: SPY capitulation *alone* doesn't beat buy-and-hold (master backtest excess −0.05); an *ad-hoc* run reported **+0.36pp/10d when `fear_z` confirms vs ~−0.01 ungated**, and fear-attention is **1.32× higher on capitulation days** (`DATA_PLAN.md`). Wired live in `analytics/signals.py` (`_fear_z`, `FEAR_MIN`; `fear_z` in `/signals`).
  - **🔬 MT5 Strategy-Tester A/B (US500, D1, 2020–2026, long-only, real spread/swap; `mql5/RedditMacro_US500_*.ini`, dates via `python main.py --export-signals`).** This tests the gate as an **absolute long-only rule** (NOT excess-vs-buy-&-hold):

    | variant | signals | trades | win% | net $ | PF | maxDD% |
    |---|---|---|---|---|---|---|
    | baseline (all caps) | 335 | 100 | 57.0 | **+12** | 1.00 | 13.6 |
    | `fear_z≥0.5` only | 95 | 45 | 53.3 | **−1045** | 0.68 | 13.0 |
    | trend up (200-DMA) only | 223 | 71 | 56.3 | **+21** | 1.01 | 9.0 |
    | trend **AND** fear (the live gate) | 54 | 27 | 55.6 | **−372** | 0.73 | 7.1 |

    **Result: the `fear_z` gate does NOT help — it hurts.** Adding fear makes US500 *worse* both alone (−$1045) and with trend (−$372 vs trend-only +$21). Fear spikes cluster *at/just before tops* where the 200-DMA still reads up (e.g. Feb-2020) → `trend AND fear` fires **long into the COVID falling knife**. The **trend filter** is the only component that helps, and mostly by cutting drawdown (13.6→7.1%), not return. Baseline ≈ breakeven re-confirms equity capitulation has no absolute edge.
  - **✅ Committed EXCESS study (`backtest/fear_gate.py`, `python -m backtest.fear_gate`) — this settles it.** The frame MT5 can't measure: mean EXCESS over buy-&-hold (the +0.36pp's own metric). Same point-in-time date-sets as the MT5 run (shared `capitulation_sets()`). 10d, net of costs:

    | leg | variant | n | excess% | win% | t(exc) |
    |---|---|---|---|---|---|
    | **SPY** | baseline | 332 | −0.05 | 61 | −0.21 |
    | SPY | **fear** | 94 | **+0.32** | 68 | +0.64 |
    | SPY | trend | 220 | −0.03 | 60 | −0.16 |
    | SPY | trend+fear *(live)* | 53 | +0.09 | 64 | +0.19 |
    | **QQQ** | baseline | 332 | +0.06 | 62 | +0.20 |
    | QQQ | **fear** | 94 | **+0.41** | 65 | +0.71 |
    | QQQ | trend+fear *(live)* | 53 | **−0.20** | 60 | −0.33 |
    | AUDJPY *(ungated)* | baseline | 333 | **+0.26** | 68 | **+2.04** |

    **Verdict (three honest parts):** (1) **The +0.36pp REPRODUCES as a point estimate** — fear-only SPY +0.32 / QQQ +0.41 excess — so it was *not* fabricated; the fear gate does lift equity-capitulation excess. (2) **But it is NOT statistically significant** (excess t≈0.6, n≈94) — a thin, unproven edge. (3) **The LIVE `trend+fear` combo is the wrong way to apply it**: the trend filter *removes the buy-fear-in-a-downtrend bounces that carry the excess*, diluting SPY (+0.32→+0.09) and flipping QQQ negative (+0.41→**−0.20**).
  - **Reconciliation (why MT5 said "loss" and this says "small win"):** MT5 sums *absolute* P&L and holds one position at a time, so a few COVID-cluster trades dominate the dollar total (−$1045); the excess frame means-averages independent trades, where fear is mildly +ve. Both are correct for their question — for *signal research* the excess frame is the right one, so the earlier "fear-gate is a drag" was a frame artifact.
  - **The genuine tension:** fear → better *timing/excess* but worse *drawdown* (COVID knives); trend → better *drawdown* but worse *excess*. The live `trend AND fear` gets the worst of both for excess.
  - **Recommendation:** keep the gate 🟡 (positive but insignificant). If we ever optimise the equity legs for excess, use **fear-only, not trend+fear**; AUDJPY (the one significant edge, +0.26 t=2.04) stays **ungated** — confirmed correct. (User chose to leave the live gate unchanged for now; this is logged for when we revisit.)
  - **🔢 Power / effective sample (day vs episode — the divergence IS the verdict):** the 94 fear-days are *not* 94 independent obs — overlapping 10d windows + episode clusters. Treated as **days**, fear-only is t=0.64 (SPY)/0.71 (QQQ); collapsed to **independent episodes** (~40, one vote each) it's **t=2.28 (SPY)/1.85 (QQQ)**. The gap is driven almost entirely by the **COVID-2020 cluster** (~10+ losing fear-days → one episode vote), so the effect is *positive across most episodes but fragile* — and SPY (2.28) vs QQQ (1.85) disagreeing on near-identical instruments over the same episodes says we're at the **noise floor**, not a stable edge. **Data to settle it:** day-level needs ~**9× more** obs for p<0.05 (~19× for 80% power) — impractical (~40+ yrs at ~15-20 fear-days/yr); the real lever is **independent fear episodes**, and the cheapest source is *backward* (Wikipedia fear data already reaches 2015) — **extending the RRAI/capitulation series to 2016-2017 adds the 2018 Volmageddon + Q4-2018 episodes** (price coverage starts 2016-05).
  - **✅ DONE — RRAI extended to 2016-06 (525 caps, +190).** Re-ran the study on the longer series, and the fear-gate **strengthened** (effect *grew* out-of-sample — the hallmark of a real edge):

    | leg | fear excess (94d → 141d) | day t (→) | **episode t** (→) |
    |---|---|---|---|
    | SPY | +0.32 → **+0.57** | 0.64 → 1.64 | 2.28 → **2.77** |
    | QQQ | +0.41 → **+0.80** | 0.71 → 1.91 | 1.85 → **2.37** |

    **Both equity legs are now episode-level significant (t≈2.4-2.8) AND agree** — the earlier SPY≠QQQ noise-floor red flag is gone. Day-level is still only marginal (1.6-1.9), so not fully nailed, but this is a real upgrade from "fragile/unproven." The trend filter conclusion **hardens**: `trend+fear` (live) is still ≈0 excess (SPY +0.10/QQQ +0.11) ⇒ **drop `trend`, use fear-only** on the equity legs. AUDJPY confirmed best ungated (baseline +0.21 t=2.00; fear-gating *hurts* → +0.09).
- **Expression (ALPHA-ONLY basket, trimmed 2026-06-03):** **AUDJPY + NZDJPY + AUDUSD + Gold (XAUUSD)** — the only legs with statistically-tradable excess (all t≥2). **Dropped:** US500/USTEC (t≈1, no alpha) and Silver (t=1.72, lumpy). All four are ungated. `euphoria_short` **parked** (it traded the dropped indices; −1.84 all-period excess). Served via `/signals` (tag `retail_fear`); traded by `mql5/RedditMacro_EA.mq5` (demo). The fear-gate is now moot for the live basket (no equity legs) but kept as research.
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

### ❌ `dump_fade_DELETION_short` — short a pump whose promoters vanished *(THE thesis — DIED at full coverage)*
> **⚠️ REVERSAL (2026-06-03).** This was "forensically validated" at +14.3%/10d (n=30-36) — but that ran at only **~25% pump-author coverage**. We then **targeted-profiled all 487 equity pump-authors (→100%)** and re-tested: the edge **flips to −19.3%/10d (n=87)**, and the PIT `young_frac` short is −39% to −87%. Cause: **squeeze tails** — a single ticker (IXHL) squeezed **−625%/−404%/−377%** in July-2025; the partial sample simply hadn't profiled its authors, so those events were invisible. Lesson logged: **small-n + partial-coverage results are dangerous**; shorting live manipulated micro-caps has *uncapped* left-tail risk. Win rate is still ~64% (most pumps fade), so the thesis isn't *wrong* — but it's only expressible via **puts** (defined risk caps the −625%). The price-rolling-over filter is what makes `distribution_short` survive where this dies.
- **Thesis (original):** concentrated pump + promoters now **deleted/suspended** → manufactured → fade the collapse. The original A2 idea, and the *reason* we track account lifecycle.
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
- **Up-leg longs both fail.** (a) "Ride while recruiting + accelerating" (concentrated) netted +1.6% but win 27%, regimes [+21/−7/−9] — one big winner, otherwise negative. (b) "Attention-leads-price coil" (broad: recruitment surging while price still flat) was **net −1.4 to −1.9%** across horizons, sign-stable loser. Neither is a tradeable long; dropped.

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

## Crypto — coincident, NOT a leading indicator (tested 2026-06-03)
Added CryptoCurrency/Bitcoin/CryptoMoonShots/SatoshiStreetBets (~1yr; Bitcoin 61k posts) and tested
whether crypto leads equities / risk-appetite. **It doesn't — it's coincident:**
- **Price lead-lag (BTC↔SPY, 2,577d):** same-day corr **+0.17 (t=8.8)** but every lead/lag k≠0 is noise (|t|<1.3) — neither leads.
- **Weekend test** (crypto's best shot — BTC trades Sat/Sun while equities are closed; 490 weekends): corr +0.05, **t=1.2** — no lead.
- **Crypto sentiment → equity RRAI:** corr **peaks at k=0 (+0.20, t=3.9)** then decays (a lead would peak at k>0). Barely predicts even BTC's *own* next-day return (+0.05).
- **Combined crypto+equity RRAI capitulation (1yr overlap, n=55):** does **NOT** help — for the validated AUDJPY edge it *dilutes* it (equity-only excess **+0.55%** > combined +0.45% > crypto-only −0.03%). Crypto sentiment is redundant with (and noisier than) the equity RRAI.
- One weak whiff: crypto *euphoria* → mildly negative SPY 3d (t=−2.4, but overlapping windows → true t≈−1.4) — *contrarian*, not leading. Watch, not an edge.

**Takeaway:** crypto's value is NOT macro timing (it's a redundant coincident risk-appetite read). It's worth keeping only as its own **pump/meme universe** (CryptoMoonShots/SatoshiStreetBets) for the `distribution_short`/pump signals. Do not fold it into the RRAI.

## ★ Cross-asset generalization map (2026-06-03) — it's a RISK-APPETITE / FEAR-REVERSAL factor
Capitulation-long (rrai≤0.15 & VIX≥18, 10d, excess vs buy-and-hold) tested across asset classes.
The signal fires wherever the instrument is a clean **risk-on** or **fear-bid** play, and is flat
everywhere else — the pattern of winners *and* losers is mechanistically coherent (= a real factor):

| asset class | works? | evidence |
|---|---|---|
| **Risk-on FX** (AUD/NZD) | ✅ **strongest** | AUDJPY t=2.93, NZDJPY t=3.18, AUDCHF 2.83, AUDUSD 2.72, NZDCHF 2.58, ZARJPY 2.32 (EM, noisier) |
| **Safe-haven metals** | ✅ gold | Gold +0.43% t=2.03; Silver +0.76% t=1.72 (lumpy) |
| Equity indices (US/AUD/JP) | ❌ weak | Nikkei +0.62 t=1.93 (best); S&P t=0.90, Nasdaq 1.13; **ASX 200 t=0.08 (flat)** |
| Growth commodities | ❌ none | Copper t=0.68, Platinum 0.87, **Oil −0.10** |
| **Controls** (should be flat) | ✅ flat | **AUDNZD (risk/risk) t=1.11**; USDJPY 1.02, CHFJPY 1.37, EURUSD 1.44 |

**Punchlines:** (1) the AUD *currency* carries the edge (t=2.9) but the AUD *equity index* (ASX) is flat
(t=0.08) — it's an FX/metals phenomenon, not equities. (2) **AUDNZD flat** is the cleanest proof: two
risk-on currencies cancel the factor. (3) It's ONE factor, so more instruments = diversification *within*
it (diminishing), not new alpha; EM/growth-commodity legs add cost/tail-risk for little gain.
**MT5 absolute check** (long-only, ungated, BaseLots=1.0): AUDJPY net +$10.4k (PF 1.14) but AUDUSD ~flat in
dollars (it *declined*, so +excess ≠ +absolute) and 60%+ DD → deployable version needs the VIX gate + vol-target sizing.

## ⚠️ MT5 EXECUTION REALITY CHECK (2026-06-03) — the excess edge does NOT survive naive execution
After fixing the EA's position sizing (ATR fixed-fractional, risk 1%/trade) and hold (trading days),
the MT5 tester is finally trustworthy (FX-leg drawdowns fell from ~90% to ~17-20%). It tells a sobering story:

| leg (VIX-gated entry, ATR-sized, 10 trading-day hold, 2016-26) | trades | win% | PF | maxDD% |
|---|---|---|---|---|
| AUDJPY | 67 | 41.8 | **0.85** | 20 |
| AUDUSD | 67 | 55.2 | **0.91** | 17 |
| Gold | 67 | 56.7 | **1.01** | 93 ⚠️ (no stop → trending losses run) |

**The validated EXCESS edge (AUDJPY t=2.93, etc.) does NOT translate into a profitable naive
long-on-trigger MT5 strategy.** Why the gap:
1. **Entry timing.** Python averages over *all* capitulation days (overlapping); the EA enters on the
   *first* trigger of a cluster — i.e. while the market is *still falling* (the cluster-start = falling
   knife). The bounce the excess metric sees is *averaged over the cluster*, not capturable by entering at its start.
2. **Excess ≠ absolute.** The FX legs have ~0 unconditional drift, so a positive *excess* (+0.48% vs B&H)
   nets ~0 in *dollars*. The Python t-stat measures "higher than drift," not "profitable."
3. **No stop.** Holding 10 days with no stop lets losses run (gold DD 93%).

**Honest status (before the fix):** the edge is a real *statistical* property but was **not deployable** as a
naive long-on-trigger trade. Predicted fix: catch the *turn*, not the first fall, + a stop.

### ✅ RESOLVED — turn-based entry + stop makes it tradeable (2026-06-03)
Added to the EA: **arm on a capitulation signal, then enter on the first up-bar (the turn) within `ArmWindow`,
with a `StopATR×ATR` stop**; plus selectable sizing (Fixed / ATR / **Kelly**). MT5 results (VIX-gated, 2016-26):

| symbol | sizing | entry | win% | PF | DD% | Sharpe |
|---|---|---|---|---|---|---|
| AUDJPY | ATR | naive | 42.5 | **0.77** | 9.6 | −0.44 |
| AUDJPY | ATR | **turn** | 51.4 | **1.06** | 7.0 | 0.10 |
| Gold | ATR | naive | 53.5 | 1.25 | 39.7 | 0.29 |
| Gold | Fixed | **turn** | 55.1 | **1.54** | 33.3 | **0.41** |
| Gold | ATR | turn | 55.1 | 1.39 | 44.9 | 0.40 |

- **The turn entry works** — it flips AUDJPY from PF 0.77 (losing, naive) to **PF 1.06** (win 42.5→51.4%), and lifts Gold to **PF 1.54 / Sharpe 0.41**. Waiting for the bounce instead of catching the knife is the difference between losing and winning.
- **Gold is the strongest deployable leg** (PF 1.4-1.5, Sharpe 0.4). AUDJPY is now marginally positive (PF ~1.06) — thin but no longer a loser.
- **Sizing verdict: ATR/Fixed win; Kelly OVERSIZES.** Even half-Kelly hit its cap on these thin edges and blew drawdowns to 30% (AUDJPY) / 80% (Gold) with PF≈1.0 — no return benefit. The textbook result: **Kelly is too aggressive for thin/uncertain edges; fixed-fractional ATR (~1%/trade) is the right default.**
- **Still preliminary:** PFs are modest, gold DD is high (33-45%), and this is in-sample on the same series the signal was built on. Forward OOS + a small live demo allocation are the next validation steps. But the basket is now **tradeable, not just a statistical curiosity.**

## Changelog
- **2026-06-03** — **Turn entry + stop makes it tradeable; sizing methods backtested.** Added to the EA: arm
  on capitulation → enter on the first up-bar (turn) within ArmWindow, with a StopATR×ATR stop; plus
  selectable sizing (Fixed/ATR/Kelly). The turn entry **rescues the strategy**: AUDJPY PF 0.77→1.06, Gold
  PF 1.25→1.54 (Sharpe 0.41). Sizing: ATR/Fixed best; **Kelly oversizes** (caps out on thin edges → 30-80%
  DD, no return gain). Gold is the strongest leg. Still in-sample/preliminary → forward OOS + demo next.
- **2026-06-03** — **MT5 reality check: excess edge ≠ tradeable.** Fixed the EA (ATR fixed-fractional sizing
  + trading-day holds; compiled clean), which collapsed the fixed-lot drawdown artifact (90%→17-20% on FX)
  and revealed the truth: under realistic single-entry execution the capitulation longs are breakeven-to-
  negative (AUDJPY PF 0.85, AUDUSD 0.91, gold 1.01 but 93% DD). The validated *excess* edge doesn't survive
  naive long-on-trigger execution — gap is entry timing (cluster-start = falling knife) + excess≠absolute +
  no stop. Needs a turn-based entry + stop to deploy. Basket downgraded to research-validated, not live-tradeable.
- **2026-06-03** — **Trimmed live basket to tradable-alpha only.** Per the cross-asset map, kept the four
  legs with significant excess (t≥2): **AUDJPY (2.93), NZDJPY (3.18), AUDUSD (2.72), Gold (2.03)**. Dropped
  US500/USTEC (t≈1) and Silver (1.72, lumpy); parked `euphoria_short` (traded the dropped indices, −1.84
  excess). `signals.py` INSTRUMENTS + MT5 EA basket trimmed 7→4; GATE_TREND cleared. Deployed live.
- **2026-06-03** — **Cross-asset generalization map + MT5 FX backtest.** Capitulation-long tested across
  FX/indices/commodities: it's a **risk-appetite/fear-reversal factor** — strong in risk-on FX (AUD/NZD,
  EM-ish), works in gold/silver, **flat in equity indices (incl. ASX t=0.08) and growth commodities
  (copper/oil)**; AUDNZD control flat. MT5 tester (D1, 2016-26, ungated): AUDJPY +$10.4k/PF 1.14, AUDUSD
  ~flat-$ (declined, +excess only), 60% DD → needs VIX gate + vol-target to deploy.
- **2026-06-03** — **Cross-sectional FX validation → broadened the basket.** Tested capitulation-long across
  the yen-cross + risk-pair complex (2016-26, VIX-gated). The edge is a **risk-on-currency factor**: every
  AUD/NZD pair is significant (NZDJPY t=3.18, AUDJPY 2.93, AUDCHF 2.83, AUDUSD 2.72) *independent of the
  funding leg*, while the no-risk-leg controls are flat (USDJPY t=1.02, CHFJPY 1.37, EURUSD 1.44) — the
  controls failing is the proof it's real, not data-mining. It's NOT a JPY thing (JPY is just a clean
  funding leg; Nikkei works as high-beta risk, not as "Japanese"). **Broadened the live `retail_fear` FX
  basket to AUDJPY + NZDJPY + AUDUSD** (`signals.py` + MT5 EA).
- **2026-06-03** — **Deletion short DIED at full coverage; AUDJPY re-confirmed; sweep de-crypto'd.**
  (1) Targeted-profiled all 487 equity pump-authors (25%→100% relevant coverage). The deletion short
  **flipped negative** — gone≥0.20 → −19.3%/10d (n=87), young_frac short −39%→−87% — dominated by squeeze
  tails (IXHL −625%) the 25% sample had missed. The +14.3% was a **coverage-bias artifact**. Dead as a
  stock short; puts-only. `distribution_short` (price-rolling-over filter) is the survivor.
  (2) **AUDJPY capitulation re-validated on 2016-26**: +0.48%/10d VIX-gated, **t(exc)=2.93**, +ve 9/10 years
  (2018 the lone falling-knife miss). Now the most significant edge in the system; unaffected by author work (pure macro).
  (3) `get_authors_needing_age` now skips crypto-only authors + front-loads ticker-mentioners.
- **2026-06-03** — **Crypto leading-indicator test: COINCIDENT, not leading.** Price lead-lag BTC↔SPY ≈0
  (same-day +0.17 but k≠0 noise), weekend test t=1.2, crypto sentiment coincident with equity RRAI (peaks
  k=0). Combined crypto+equity RRAI does NOT improve capitulation — *dilutes* the AUDJPY edge (+0.55%→+0.45%).
  Crypto's value = its own pump/meme universe, not macro timing. (See "Crypto — coincident" section.)
- **2026-06-03** — **RRAI extended to 2016-06 (525 caps, +190) → fear-gate STRENGTHENED.** Backfill landed
  (aggregate_daily 2016-06→2026, 3,653 days; +2018 Volmageddon/Q4 episodes). Re-ran `backtest.fear_gate`:
  fear-only excess grew SPY +0.32→+0.57, QQQ +0.41→+0.80 (effect *grew* OOS = real-edge signature);
  episode-level now **significant on BOTH legs and they agree** (SPY t=2.77, QQQ t=2.37, k=63) — the prior
  SPY≠QQQ noise-floor flag is resolved. Day-level still marginal (1.6-1.9). The LIVE `trend+fear` gate is
  still ≈0 (SPY +0.10/QQQ +0.11) ⇒ **drop the trend filter, use fear-only**. AUDJPY confirmed best ungated.
- **2026-06-02** — **Power analysis + RRAI history extension (in progress).** Quantified the fear-gate's
  data need: the 94 fear-days are ~40 independent episodes; day-level it's t≈0.6 (needs ~9× more obs for
  p<0.05, ~19× for power — impractical), but episode-level it's t=2.28 (SPY)/1.85 (QQQ) — significant-but-
  fragile (COVID-cluster-driven; SPY≠QQQ ⇒ noise floor). Cheapest lever = more *independent fear episodes*
  via **backward** history (Wikipedia fear data already reaches 2015). Extended study prices to max and
  **kicked off the RRAI aggregate backfill 2016-06→2020** (wallstreetbets/stocks/pennystocks) to add the
  2018 Volmageddon + Q4-2018 episodes; will re-run `backtest.fear_gate` on the longer series when it lands.
- **2026-06-02** — **Committed EXCESS study settles the fear-gate (`backtest/fear_gate.py`).** The proper
  frame (mean excess vs buy-&-hold, the +0.36pp's own metric). Three findings: (1) the +0.36pp
  **reproduces** as a point estimate (SPY +0.32 / QQQ +0.41 fear-only excess) — not fabricated; (2) it's
  **not significant** (excess t≈0.6, n≈94); (3) the LIVE **trend+fear** combo is *worse* — dilutes SPY
  (→+0.09), flips QQQ negative (→−0.20) — because the trend filter strips the downtrend bounces that carry
  the excess. Reconciles the MT5 "loss": MT5 sums absolute P&L (COVID-cluster dominates), excess
  means-averages — so the earlier "drag" was a frame artifact. Gate stays 🟡; fear-only > trend+fear for
  excess; AUDJPY stays ungated (the only significant edge). Exporter refactored to share the study's
  `capitulation_sets()` (MT5 + Python test identical dates).
- **2026-06-02** — **MT5 backtest of the fear-gate → it's a DRAG, not a rescue.** Extended `--export-signals`
  to emit 4 point-in-time capitulation date-sets (baseline / `fear_z≥0.5` / 200-DMA-trend / trend+fear)
  and ran each through the MT5 Strategy Tester on US500 (2020-26) via `RedditMacro_US500_*.ini`. Adding
  `fear_z` made absolute returns *worse* (alone −$1045, with trend −$372 vs trend-only +$21) — fear spikes
  fire long into tops (Feb-2020). Only the trend filter helps (DD 13.6→7.1%). Caveat: MT5 = absolute
  long-only, the +0.36pp was *excess* — so not a direct refutation, but the gate is downgraded to
  🟡 doubtful and needs a committed *excess-based* study to settle (maybe drop `fear_z`, keep trend).
- **2026-06-02** — **Doc-completeness pass.** Audited the doc against the live registry + all backtest
  modules. Found one missing edge — the **Wikipedia fear-confirmation gate** (live: gates the equity legs
  of `retail_fear`, `fear_z≥0.5`, +0.36pp rescue) — now documented, *honestly flagged as not yet
  committed-backtested*. Also added the full live-screen list (`fresh_pump_alert`, `coordination_flag`, …)
  to the scoreboard and recorded the broad "attention-leads-price" long as a failed up-leg test.
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
