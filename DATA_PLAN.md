# Data Acquisition Plan — closing the data gaps

The research found **one modest validated edge** (AUDJPY capitulation, +0.25pp/10d vs buy-and-hold)
and several promising-but-unvalidated screens. The binding constraint to going further is **DATA,
not more signal ideas**. This plan prioritises by **value ÷ effort**, front-loading **free,
independent** data — because the single biggest lever on a thin edge is growing the *effective
sample* (independent observations), which cross-source data and forward collection both do.

## Guiding principles
1. **Independent sources beat more of the same.** A signal confirmed across Reddit *and* StockTwits
   *and* news is far stronger than one Reddit signal — and adds effective sample without waiting years.
2. **Free + forward first.** Most paid/historical data is expensive; start collecting free data *going
   forward* now, so the OOS clock starts today.
3. **Ratios / coverage-stationary** (the lesson from the RRAI backfill) — normalise everything.

---

## ✅ SOLVED — independent source = Wikipedia pageviews (2026-06-02)
After StockTwits (blocked) and GDELT (flaky) failed, **Wikipedia pageviews** works cleanly: free,
no key, no setup, historical to 2015, reliable REST API. `scraper/wikipedia.py` backfilled 40k days
of **fear-term pages** (Stock market crash / Recession / Bear market) + company pages.
**Independent cross-validation:** fear-attention is **1.32× higher on RRAI capitulation days** than
other days → the capitulation edge now corroborates across THREE independent reads (Reddit sentiment,
price +0.25pp, Wikipedia fear-attention). It's attention not sentiment, but that's a fit for our
attention-driven signals + macro fear. This is the chosen independent source; GDELT-BQ/paid only if
we later need news *sentiment* specifically.

## ⚠️ REALITY CHECK (2026-06-02) — free EXTERNAL sentiment is gated
Tested live: **StockTwits is Cloudflare-blocked** (403 "Just a moment"; their API went partner/paid-only).
**GDELT's free DOC API is unreliable** for us — historical date-range queries return empty, and even
recent queries parse inconsistently from our container (works via host `curl`, null from container
`requests`). So **free, independent, *historical* sentiment isn't available off-the-shelf.** Revised
options for an independent source: (a) **GDELT BigQuery** (free data, but needs a GCP project + SQL —
the robust historical path), (b) **paid ticker-news sentiment** (Finnhub free-tier *with key*, or
Polygon), or (c) **more/different subreddits** (crypto, AU — *not fully independent*, but free + works
via our own pipeline and adds episodes). Recommendation: **(c) now** (free, reliable), **(a) when we
want historical news validation.** `scraper/gdelt.py` is kept as a best-effort FORWARD collector.

## Phase 1 — Free quick wins (this week, $0) — DO FIRST
| # | Data | Source (free) | Integrate | Unblocks |
|---|---|---|---|---|
| 1 | **More subreddits** (crypto/AU) — *the free, reliable independent-ish expansion* | existing scraper | `add_subreddit` (live + archive) | more episodes + different communities (StockTwits/GDELT proved gated) |
| 1b | News sentiment (when ready) | **GDELT BigQuery** (GCP) or **Finnhub** (free key) | `external_sentiment` table (built) | truly independent sentiment cross-check |
| 2 | **Crypto + more subreddits** | existing scraper (`CryptoCurrency`, `Bitcoin`, `CryptoMoonShots`, `SatoshiStreetBets`) | `add_subreddit` flow (live + archive) | BTC/ETH sentiment (we trade neither well yet, but crypto is the most retail-driven) + more episodes |
| 3 | **Short interest + float** | FINRA short-interest files (bi-monthly, official) + yfinance `floatShares` | `data/short_interest.py` → `short_interest` table | **squeeze setups** (Reddit squeeze chatter + high SI / low float); short feasibility |
| 4 | **Extend RRAI to 2017** | arctic-shift (already wired) | `--aggregate-history-backfill --history-start 2017-01-01` | adds 2018-Q4 selloff regime + more fear episodes (cheap) |
| 5 | **Finish author coverage** | Reddit OAuth (sweep running, 32%) | already running; add ticker-prioritised order | deletion/young tells (1,622 gone already → the deletion-gated short becomes testable) |

## Phase 2 — Forward collection + current snapshots (weeks, $0–low)
| # | Data | Source | Integrate | Unblocks |
|---|---|---|---|---|
| 6 | **Intraday prices (forward)** | Alpaca free IEX bars / yfinance 1m (last 7d) | `prices_intraday` table; intraday velocity feature | **pump entry/exit timing** (pumps move intraday; EOD is too coarse) |
| 7 | **Options chains + IV (current)** | yfinance options / Tradier (free w/ account) | `options_snapshot` table; IV-rank feature | express fades via **puts** (only sane penny short); basic options-flow read |
| 8 | **News sentiment** | GDELT / Finnhub free news API / RSS | `external_sentiment` (source='news') | independent confirmation; catalyst detection |

## Phase 3 — Paid / heavy (only when a signal justifies it)
| # | Data | Source (paid) | Cost | Unblocks |
|---|---|---|---|---|
| 9 | **Historical penny + delisted prices** | Polygon.io (delisted coverage) | ~$30–200/mo | multi-regime validate `smallcap_fade` / `biotech` (only 2025-26 exists) |
| 10 | **Historical intraday** | Polygon | (same plan) | backtest intraday pump timing |
| 11 | **Borrow fees / live SI** | IBKR API (acct) / Ortex | acct / $ | realistic short cost; real-time squeeze |
| 12 | **Real transaction costs** | broker (MT5 spreads, IBKR fees) | acct | trustworthy net returns (small-cap costs currently modelled/optimistic) |

---

## Integration architecture (one place to add it all)
- **New tables** (`init_database`): `external_sentiment(source, ticker, ts, sentiment, bull, bear, n)`,
  `short_interest(ticker, settlement_date, si, float, days_to_cover, si_pct_float)`,
  `prices_intraday(ticker, ts, o, h, l, c, v)`, `options_snapshot(ticker, date, expiry, strike, type, iv, oi, vol)`.
- **New fetchers** under `data/` (or `scraper/`), each idempotent + resumable like the existing backfills.
- **Scheduler**: add periodic pulls (StockTwits/news each cycle; FINRA SI bi-monthly; options daily).
- **Feature store gains**: cross-source sentiment *agreement*, SI%/float, IV-rank, intraday velocity —
  all feeding the existing signal registry + backtester.

## Recommended sequence
**Start Phase 1 #1–3 immediately** — all free, all this week: StockTwits (independent sentiment),
crypto subs (existing pipeline), FINRA short-interest (squeeze data). Then #4–5 (RRAI extend, author
finish — already running). Phase 2 once Phase 1 lands. Phase 3 only if a signal earns the spend.

## What each gap actually buys us
- **StockTwits/news** → the cheapest path to *confidence* on the thin AUDJPY edge (independent episodes).
- **Short interest** → a genuinely *new* strategy (squeeze) we can't build today.
- **Intraday + options** → the only way to *trade* the pump/fade signals (timing + defined-risk shorts).
- **Historical penny prices** → the only way to *validate* the big-but-single-regime smallcap/biotech signals.
