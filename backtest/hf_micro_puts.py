"""High-frequency micro-cap pump-fade via PUTS — the deployable, regime-robust, leverageable
variant of distribution_short (2026-06-04).

The tight `distribution_short` spec yields only ~1-2 deployable trades/yr. Loosening the gates
while restricting to the LIQUID universe (cap<$10B, sector!=Energy, price>=$5) gives ~100
signals/yr that STAY regime-robust — the liquidity filter strips the unborrowable squeeze-bombs
that caused the apparent "2021 dead regime" (BFRI -115% etc.), so the tradeable subset fades
reliably in every year. Decomposing by cap and expression, the DURABLE core is:

  MICRO-CAPS (<$300M) expressed via PUTS — positive EVERY year 2021-26 (+5.2%/notional avg;
  2025 +5.1% t=3.05, 2022 +8.6% t=2.60). Defined-risk → no borrow needed, squeeze tail capped at
  premium, ~58-100 trades/yr, and LEVERAGEABLE to mid-teens-20% true (mark-to-market) drawdown
  WITHOUT a ruin path (the dangerous leg can't lose more than premium).

The naked small-cap ($300M-2B) leg looks great on the full-sample average (+16.7%) but is a 2024
FLUKE — win 96% on n=23 that year carries its whole +3.4%; it is DEAD elsewhere (2021 +0.5%,
2022 -1.4%, 2025 -0.3%). So it is OPPORTUNISTIC upside, sized small, NOT load-bearing.

Spec (deployable core):
  per_author>=1.5 AND mentions>=4 AND sentiment>=0.3 AND trailing-5d-return<0  (rollover)
  AND market_cap<$300M AND sector!=Energy AND price>=$5  ->  buy ~2wk ATM put.
  Per-trade P&L per notional = max(0, -raw_10d_return) - premium.

Premium is the dominant sensitivity (these are HIGH-IV names): 8% -> ~+63%/yr, 12% -> ~+16%/yr,
15% -> loses. Realistic ~10-15%; model per-name IV before sizing (the open #2 work item).

Single-dataset (2021-26; 2020 has no pump signal — post-GME phenomenon). Forward-OOS pending.

  docker compose exec mcp python -m backtest.hf_micro_puts
"""
import sys
import bisect
import math
import statistics
from collections import defaultdict

from backtest.microstructure import _rows, _trailing_ret
from backtest.engine import _load_prices, _net, _fwd
from backtest.dist_short_oos import ETFS as BLOCK
from analytics.marketcap import market_cap, sector_class

PREM = 0.12                                   # ATM ~2wk put premium (model per-name IV before sizing)
PA, MN, SENT = 1.5, 4, 0.3                    # loose gates; the liquidity filter gives the robustness
MICRO, SMALL = 300e6, 2e9                     # <MICRO -> put core; MICRO-SMALL -> naked (opportunistic)


def _px_at(px, t, d):
    if t not in px:
        return None
    ds, cs = px[t]
    i = bisect.bisect_right(ds, d[:10]) - 1
    return cs[i] if 0 <= i < len(cs) else None


def signals(px, lookback_days=2400, micro_only=True):
    """Deployable HF signals. Each dict: date, ticker, cap, price, raw10 (10d stock return),
    naked (10d market-neutral net), instr ('put' for micro core, 'naked' for small-cap upside).
    micro_only=True returns just the regime-robust put core."""
    out = []
    for d in _rows(lookback_days=lookback_days, use_store=True):
        if d["new_frac_trail"] is None or d["ticker"] in BLOCK:
            continue
        pa, mn, s = d["per_author"] or 0, d["mentions"] or 0, d["sentiment"] or -9
        if pa < PA or mn < MN or s < SENT:
            continue
        if (_trailing_ret(px, d["ticker"], d["date"], 5) or 9) >= 0:    # must be rolling over
            continue
        p = _px_at(px, d["ticker"], d["date"])
        if p is None or p < 5:                                          # optionability floor
            continue
        cap = market_cap(d["ticker"])
        if not (cap is None or cap < 10e9):                            # drop large/mega (edge reverses)
            continue
        if sector_class(d["ticker"]) == "AVOID":                       # drop Energy (squeeze landmine)
            continue
        if cap is None or cap < MICRO:
            instr = "put"
        elif cap < SMALL:
            instr = "naked"
        else:
            continue                                                   # mid-caps fade -> drop
        if micro_only and instr != "put":
            continue
        out.append(dict(date=d["date"][:10], ticker=d["ticker"], cap=cap, price=p,
                        raw10=_fwd(px, d["ticker"], d["date"], 10),
                        naked=_net(px, d["ticker"], d["date"], -1, 10, 75, 8.0, True),
                        instr=instr))
    return out


def trade_ret(sig, premium=PREM):
    """Per-notional return for a signal under its expression. put: max(0,-raw)-prem (tail-capped);
    naked: market-neutral net. Returns None if the needed price window is missing."""
    if sig["instr"] == "put":
        return None if sig["raw10"] is None else max(0.0, -sig["raw10"]) - premium
    return sig["naked"]


def _stat(v):
    if len(v) < 3:
        return None
    m = statistics.mean(v); sd = statistics.pstdev(v) or 1e-9
    return dict(n=len(v), mean=m * 100, win=100 * sum(1 for x in v if x > 0) / len(v),
                t=m / (sd / math.sqrt(len(v))))


def regime(sig, premium=PREM):
    """Year-by-year return/notional per leg — the regime-robustness check (the core is positive 7/7)."""
    legs = {"put": defaultdict(list), "naked": defaultdict(list), "all": defaultdict(list)}
    for s in sig:
        r = trade_ret(s, premium)
        if r is None:
            continue
        legs[s["instr"]][s["date"][:4]].append(r)
        legs["all"][s["date"][:4]].append(r)
    for name in ("put", "naked", "all"):
        by = legs[name]
        if not by:
            continue
        pos = sum(1 for y in by if _stat(by[y]) and statistics.mean(by[y]) > 0)
        tot = sum(1 for y in by if len(by[y]) >= 3)
        print(f"  {name.upper()} leg:  positive {pos}/{tot} years")
        for y in sorted(by):
            st = _stat(by[y])
            if st is None:
                print(f"    {y}: n={len(by[y]):>3}  --")
            else:
                print(f"    {y}: n={st['n']:>3}  ret/notional={st['mean']:+5.1f}%  win={st['win']:>3.0f}%  t={st['t']:+.2f}")


def portfolio(sig, px, cap_concurrency=12, frac=0.10, premium=PREM, start=100000):
    """Event-driven defined-risk backtest with DAILY mark-to-market drawdown. Returns CAGR / true DD /
    trades-per-year. Puts capped at -premium → leverage (higher cap_concurrency) amplifies return
    without a ruin path."""
    rows = [s for s in sig if trade_ret(s, premium) is not None]
    for s in rows:
        ds, cs = px[s["ticker"]]
        i = bisect.bisect_right(ds, s["date"]); s["xd"] = ds[min(i + 10, len(ds) - 1)]
        s["spy0"] = _px_at(px, "SPY", s["date"])
    rows = [s for s in rows if s.get("spy0")]
    rows.sort(key=lambda s: s["date"])
    sigby = defaultdict(list)
    for s in rows:
        sigby[s["date"]].append(s)
    days = [d for d in px["SPY"][0] if rows[0]["date"] <= d <= rows[-1]["xd"]]

    def mtm(pos, day):
        st = _px_at(px, pos["ticker"], day)
        if st is None:
            return 0.0
        r = st / pos["price"] - 1
        if pos["instr"] == "put":
            return pos["notional"] * (max(0.0, -r) - premium)
        spt = _px_at(px, "SPY", day)
        return pos["notional"] * (-(r) + (spt / pos["spy0"] - 1 if spt else 0))

    cash = start; openp = []; peak = start; maxdd = 0.0; trades = 0
    yrs = (int(days[-1][:4]) * 12 + int(days[-1][5:7]) - int(days[0][:4]) * 12 - int(days[0][5:7])) / 12.0
    for day in days:
        openp2 = []
        for pos in openp:
            if pos["xd"] <= day:
                pnl = mtm(pos, pos["xd"])
                if pos["instr"] == "naked":
                    pnl -= pos["notional"] * 0.0155              # spread + borrow on exit
                cash += pnl
            else:
                openp2.append(pos)
        openp = openp2
        eq_now = cash + sum(mtm(p, day) for p in openp)
        for s in sigby.get(day, []):
            if len(openp) < cap_concurrency:
                s2 = dict(s); s2["notional"] = frac * eq_now
                openp.append(s2); trades += 1
        eq = cash + sum(mtm(p, day) for p in openp)
        peak = max(peak, eq); maxdd = max(maxdd, (peak - eq) / peak) if peak > 0 else maxdd
    for pos in openp:
        pnl = mtm(pos, pos["xd"]) - (pos["notional"] * 0.0155 if pos["instr"] == "naked" else 0)
        cash += pnl
    cagr = (cash / start) ** (1 / yrs) - 1 if cash > 0 else -1
    return dict(final=cash, cagr=cagr * 100, maxdd=maxdd * 100, trades=trades, peryr=trades / yrs)


def main():
    px = _load_prices()
    core = signals(px, micro_only=True)
    print(f"=== HF micro-cap pump-fade via PUTS (premium={PREM*100:.0f}%) ===")
    print(f"deployable core: {len(core)} signals  (~{len(core)/5.4:.0f}/yr)\n")
    print("REGIME ROBUSTNESS (per-notional return by year):")
    regime(signals(px, micro_only=False))          # show both legs for context
    print("\n$100K PORTFOLIO (daily mark-to-market, defined-risk puts):")
    print(f"  {'concurrency':12} {'trades/yr':>9} {'CAGR':>8} {'true maxDD':>11} {'final eq':>12}")
    for c in (8, 12, 20):
        r = portfolio([dict(s) for s in core], px, cap_concurrency=c)
        print(f"  cap={c:<8} {r['peryr']:>9.0f} {r['cagr']:>+7.1f}% {r['maxdd']:>10.1f}% ${r['final']:>11,.0f}")
    print(f"\n  premium sensitivity is the open risk: 8% ~+63%/yr, 12% ~+16%/yr, 15% loses. "
          f"Model per-name IV before sizing.")


if __name__ == "__main__":
    main()
