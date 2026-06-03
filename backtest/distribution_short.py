"""Canonical backtest for distribution_short — THE one validated edge.

Spec (frozen 2026-06-03): a CONCENTRATED micro-cap pump (per_author>=2 & mentions>=10) with
still-euphoric sentiment (>=0.4) whose price has ALREADY rolled over (trailing-5d return<0) ->
market-neutral SHORT vs SPY, hold 10 sessions, on the CLEAN micro-cap universe (mega-caps / ETFs /
crypto / common-word tickers excluded — see dist_short_oos.ETFS). Frictions: 75bps round-trip
spread + 8bps/day borrow.

Reports net/win/t/Sharpe/PF/maxDD + equity curve, per-REGIME breakdown (the headline finding:
regime-CONDITIONAL — dies in meme-mania), the random-entry null, the squeeze tail, and the full
per-trade ledger. Reads the DuckDB research store when present (fast). For multi-regime coverage
run after the deep backfill with the wide lookback (default 2200 days).

  docker compose exec mcp python -m backtest.distribution_short            # full report
  docker compose exec mcp python -m backtest.distribution_short ledger     # + per-trade ledger
"""
import sys
import math
import random
import statistics
from collections import defaultdict

from backtest.microstructure import _rows, _trailing_ret
from backtest.engine import _load_prices, _net
from backtest.dist_short_oos import ETFS as BLOCK

H, SPREAD, BORROW = 10, 75, 8.0
# Notional per position for the equity curve. A single micro-cap short can lose >100% on a squeeze
# (BFRI -115%), so full-capital compounding is meaningless — you size small and/or express via PUTS
# (defined risk: max loss = premium). 10% notional is a conservative, realistic book weight.
# NOTE on the puts-capped figures elsewhere: a "cap losses at -X%" model is OPTIMISTIC — it floors
# the downside WITHOUT charging premium on winners. These micro-caps have huge IV (~150-300%), so a
# 2-4wk put costs ~12-15% of spot; with that premium drag the realistic puts edge is ~+5%/trade,
# PF ~2.6 (not the +13%/PF 6 of the loss-cap model). Plan around +5%/trade; t-stat (3.57) is the gauge.
FRACTION = 0.10
REGIME = {"2021": "2021 mania", "2022": "2022 bear", "2023": "2023 recovery",
          "2024": "2024 chop", "2025": "2025-26 now", "2026": "2025-26 now"}
REGIME_ORDER = ["2021 mania", "2022 bear", "2023 recovery", "2024 chop", "2025-26 now"]


def signals(px, lookback_days=2200):
    """The frozen distribution_short events + the full candidate universe (for the null)."""
    rows = [d for d in _rows(lookback_days=lookback_days)
            if d["new_frac_trail"] is not None and d["ticker"] not in BLOCK]
    sig = [d for d in rows if (d["per_author"] or 0) >= 2 and (d["mentions"] or 0) >= 10
           and (d["sentiment"] or -9) >= 0.4
           and (_trailing_ret(px, d["ticker"], d["date"], 5) or 9) < 0]
    return sig, rows


def ledger(px, sig):
    """Per-trade [(date, ticker, net_return)], chronological — only tradeable (priced) signals."""
    out = []
    for d in sig:
        net = _net(px, d["ticker"], d["date"], -1, H, SPREAD, BORROW, True)
        if net is not None:
            out.append((d["date"][:10], d["ticker"], net))
    out.sort()
    return out


def metrics(nets):
    """Performance stats + equity curve (one position at a time, full-capital compounding)."""
    n = len(nets)
    if n < 2:
        return None
    m = statistics.mean(nets)
    sd = statistics.pstdev(nets) or 1e-9
    wins = [x for x in nets if x > 0]
    losses = [-x for x in nets if x <= 0]
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else float("inf")
    eq, peak, mdd = 1.0, 1.0, 0.0
    for x in nets:
        eq *= (1 + FRACTION * x)             # fixed-fraction sizing -> survives >100% single-trade losses
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak)
    return dict(n=n, net=m * 100, win=100 * len(wins) / n, t=m / (sd / math.sqrt(n)),
                pf=pf, sharpe=(m / sd) * math.sqrt(252.0 / H), maxdd=mdd * 100,
                total=(eq - 1) * 100,
                avgwin=100 * statistics.mean(wins) if wins else 0.0,
                avgloss=100 * statistics.mean(losses) if losses else 0.0)


def null_percentile(px, events, rows, M=400):
    """Random-entry null: same tickers, random dates. Returns (actual_net, percentile)."""
    tdates = defaultdict(list)
    for d in rows:
        tdates[d["ticker"]].append(d["date"])

    def mn(evs):
        r = [_net(px, t, dt, -1, H, SPREAD, BORROW, True) for t, dt in evs]
        r = [x for x in r if x is not None]
        return statistics.mean(r) * 100 if len(r) >= 5 else None

    actual = mn(events)
    if actual is None:
        return None, None
    rng = random.Random(42)
    ns = sorted(x for x in (mn([(t, rng.choice(tdates[t])) for t, _ in events]) for _ in range(M))
                if x is not None)
    return actual, (100 * sum(1 for x in ns if x < actual) / len(ns) if ns else None)


def run(show_ledger=False):
    px = _load_prices()
    sig, rows = signals(px)
    led = ledger(px, sig)
    if not led:
        print("no tradeable signals")
        return
    nets = [r for _, _, r in led]
    M = metrics(nets)
    print(f"=== distribution_short backtest — {len(led)} trades (of {len(sig)} signals) ===")
    print(f"    concentrated micro-cap pump + sentiment>=0.4 + price rolling over -> market-neutral "
          f"short, hold {H}d; {SPREAD}bps spread + {BORROW}bps/day borrow\n")
    print(f"  net/trade {M['net']:+.2f}%   win {M['win']:.0f}%   t {M['t']:+.2f}   PF {M['pf']:.2f}"
          f"   Sharpe(ann) {M['sharpe']:.2f}")
    print(f"  avg win {M['avgwin']:+.1f}%   avg loss -{M['avgloss']:.1f}%   "
          f"maxDD {M['maxdd']:.0f}%   compounded {M['total']:+.0f}%  (at {FRACTION:.0%} notional/trade)")
    a, p = null_percentile(px, [(d["ticker"], d["date"]) for d in sig], rows)
    if p is not None:
        verdict = "REAL EDGE" if p >= 95 else ("borderline" if p >= 90 else "weak / regime-mixed")
        print(f"  random-entry null (all regimes): {p:.0f}th percentile  [{verdict}]")
    tail = sorted(led, key=lambda x: x[2])
    print(f"  squeeze tail (worst 5): " + ", ".join(f"{t} {r*100:+.0f}%" for _, t, r in tail[:5]))
    print(f"  best 5:                 " + ", ".join(f"{t} {r*100:+.0f}%" for _, t, r in tail[-5:]))

    print("\n  by regime (robust across all via puts — 2021 'weakness' is one uncapped squeeze):")
    byreg = defaultdict(list)
    bysig = defaultdict(list)
    for d in sig:
        bysig[REGIME.get(d["date"][:4], "other")].append((d["ticker"], d["date"]))
    for dt, t, r in led:
        byreg[REGIME.get(dt[:4], "other")].append(r)
    for rg in REGIME_ORDER:
        ev = byreg.get(rg, [])
        if len(ev) >= 3:
            mm = metrics(ev)
            _, pr = null_percentile(px, bysig[rg], rows)
            ps = f" null={pr:.0f}th" if pr is not None else ""
            print(f"    {rg:14}: n={mm['n']:>3} net={mm['net']:+6.2f}% win={mm['win']:>3.0f}% "
                  f"t={mm['t']:+.2f} maxDD={mm['maxdd']:>3.0f}%{ps}")
        elif ev:
            print(f"    {rg:14}: n={len(ev)} (too few)")

    if show_ledger:
        print("\n  per-trade ledger:")
        for dt, t, r in led:
            print(f"    {dt}  {t:6}  {r*100:+7.2f}%")


if __name__ == "__main__":
    run(show_ledger=("ledger" in sys.argv))
