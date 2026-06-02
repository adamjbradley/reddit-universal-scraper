"""Committed, reproducible study of the Wikipedia FEAR-GATE on capitulation-long.

This is the frame the MT5 tester can't measure: EXCESS over buy-and-hold (unconditional
drift), the only metric that can confirm or kill the long-standing "+0.36pp fear-gate
rescue" claim. It splits the capitulation dates four ways and judges each, long, vs the
benchmark's buy-and-hold-anytime drift (same methodology as backtest/run_all.py):

    baseline   : all rrai_pct<=0.15 days
    fear       : + Wikipedia fear_z>=0.5            (independent fear confirmation)
    trend      : + SPY 200-DMA uptrend              (the falling-knife guard)
    trendfear  : + both                             (the LIVE equity-leg gate)

All gates are point-in-time (fear_z and the 200-DMA computed as-of each date, no lookahead).
`capitulation_sets()` is the single source of truth for these date-sets - main.py's
--export-signals (the MT5 CSVs) imports it, so the MT5 and Python studies test the SAME dates.

Run: python -m backtest.fear_gate
"""
import bisect
import math
import statistics

from export.database import get_connection
from backtest.engine import _load_prices, _fwd, _net

H = 10
FEAR_MIN = 0.5


def _fear_z_fn():
    """Return fear_z_asof(day): PIT z-score of Wikipedia fear-page attention vs trailing ~30d
    (mirrors analytics.signals._fear_z but evaluated as-of an arbitrary date)."""
    c = get_connection()
    rows = c.execute("""SELECT date, SUM(volume) v FROM external_sentiment
                        WHERE source='wikipedia' AND ticker LIKE 'fear_%' AND volume IS NOT NULL
                        GROUP BY date ORDER BY date""").fetchall()
    c.close()
    ds = [r["date"][:10] for r in rows]
    vs = [r["v"] for r in rows]

    def fz(day, lookback=31):
        i = bisect.bisect_right(ds, day) - 1
        if i < 10:
            return 0.0
        win = vs[max(0, i - lookback + 1): i + 1]
        latest, base = win[-1], win[:-1]
        return (latest - statistics.mean(base)) / (statistics.pstdev(base) or 1.0)
    return fz


def _trend_up_fn(px):
    """Return trend_up_asof(day): PIT 200-DMA uptrend on SPY (close>SMA200 AND SMA50>=SMA200),
    matching the live _trend('US500') which maps US500->SPY."""
    dates, closes = px.get("SPY", ([], []))

    def tu(day):
        i = bisect.bisect_right(dates, day[:10]) - 1
        if i < 200:
            return False
        c = closes[i]
        sslow = statistics.mean(closes[i - 199: i + 1])
        sfast = statistics.mean(closes[i - 49: i + 1])
        return c > sslow and sfast >= sslow
    return tu


def _vix_on_fn(px):
    """Return vix_on(day): the latest ^VIX close as-of `day` (the live VIX gate input)."""
    import bisect
    vdates, vclose = px.get("^VIX", ([], []))

    def von(day):
        i = bisect.bisect_right(vdates, day[:10]) - 1
        return vclose[i] if i >= 0 else None
    return von


def capitulation_sets(px=None):
    """Point-in-time capitulation date-sets. Shared by the MT5 exporter and this study.
    `vixgated` = the LIVE trigger (rrai<=0.15 AND VIX>=18); the FX/gold basket trades this set."""
    px = px or _load_prices()
    c = get_connection()
    caps = [r["date"][:10] for r in c.execute(
        "SELECT date FROM aggregate_daily WHERE rrai_pct<=0.15 ORDER BY date").fetchall()]
    c.close()
    fz, tu, vix = _fear_z_fn(), _trend_up_fn(px), _vix_on_fn(px)
    return {
        "baseline":  caps,
        "vixgated":  [d for d in caps if (vix(d) or 0) >= 18.0],   # the LIVE trigger
        "fear":      [d for d in caps if fz(d) >= FEAR_MIN],
        "trend":     [d for d in caps if tu(d)],
        "trendfear": [d for d in caps if tu(d) and fz(d) >= FEAR_MIN],
    }


def _study(px, instr, dates, drift, spread):
    rets = [_net(px, instr, d, +1, H, spread, 0.0, False) for d in dates]
    rets = [r for r in rets if r is not None]
    if len(rets) < 5:
        return None
    m = statistics.mean(rets)
    sd = statistics.pstdev(rets) or 1e-9
    win = sum(1 for x in rets if x > 0) / len(rets) * 100
    # t-stat on the EXCESS (net - drift): does the timing beat buy-and-hold? (drift is a
    # constant, so sd(excess)=sd(net)). This is the honest test, NOT t on net (~always +ve in a bull).
    t_exc = (m - drift) / (sd / math.sqrt(len(rets)))
    return {"n": len(rets), "net": m * 100, "bh": drift * 100,
            "excess": (m - drift) * 100, "win": win, "t": t_exc}


def run():
    px = _load_prices()
    sets = capitulation_sets(px)
    c = get_connection()
    all_d = [r["date"][:10] for r in c.execute(
        "SELECT date FROM aggregate_daily ORDER BY date").fetchall()]
    c.close()

    def drift(instr):
        rs = [_fwd(px, instr, d, H) for d in all_d]
        rs = [x for x in rs if x is not None]
        return statistics.mean(rs) if rs else 0.0

    # SPY/QQQ = the live-gated equity legs; AUDJPY = the ungated leg (should we gate it too?)
    legs = [("SPY", 2), ("QQQ", 2), ("AUDJPY=X", 3)]
    DR = {s: drift(s) for s, _ in legs}

    print(f"=== FEAR-GATE study: capitulation-long, EXCESS vs buy-and-hold ({H}d, net of costs) ===")
    print("Does adding the fear_z / trend gate improve the EXCESS of capitulation-long?\n")
    for instr, spread in legs:
        gated = instr in ("SPY", "QQQ")
        print(f"--- {instr}{'  (LIVE equity leg: trend+fear)' if gated else '  (LIVE: ungated)'} ---")
        print(f"  {'variant':10s} {'n':>4} {'net%':>7} {'B&H%':>7} {'excess':>7} {'win%':>5} {'t(exc)':>6}")
        for name in ("baseline", "fear", "trend", "trendfear"):
            r = _study(px, instr, sets[name], DR[instr], spread)
            if r is None:
                print(f"  {name:10s}  (too few)")
                continue
            flag = "  <= live" if (gated and name == "trendfear") else ""
            print(f"  {name:10s} {r['n']:>4} {r['net']:>+7.2f} {r['bh']:>+7.2f} "
                  f"{r['excess']:>+7.2f} {r['win']:>5.0f} {r['t']:>+6.2f}{flag}")
        print()
    print("Verdict rule: the gate is real only if its EXCESS clearly beats the baseline's excess")
    print("AND stays positive. Counts of each set:",
          {k: len(v) for k, v in sets.items()})


if __name__ == "__main__":
    run()
