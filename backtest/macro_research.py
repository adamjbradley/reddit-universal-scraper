"""Macro strategy R&D — derive candidate MT5-tradeable (index/FX CFD) strategies from the
Reddit aggregates and backtest each with the friction-aware engine. Only survivors
(positive net AND sign-stable across regimes) get promoted to a live EA.

This is exploration, kept out of the production engine. Run: python -m backtest.macro_research
"""
import statistics

from export.database import get_connection
from backtest.engine import _load_prices, run_event_study, _fwd, _net

# liquid instruments the MT5 EA can trade -> (symbol, round-trip spread bps)
INSTRUMENTS = [("SPY", 2), ("QQQ", 2), ("AUDJPY=X", 3)]


def _agg():
    c = get_connection()
    rows = [dict(r) for r in c.execute(
        """SELECT date, rrai_pct, rrai_raw, bull_frac, bear_frac, froth, tickers
           FROM aggregate_daily WHERE rrai_pct IS NOT NULL ORDER BY date""")]
    c.close()
    return rows


def _pct_rank(vals, win=90, min_hist=30):
    """Trailing percentile rank of each value within the prior `win` observations."""
    out = []
    for i, v in enumerate(vals):
        w = vals[max(0, i - win + 1): i + 1]
        out.append(sum(1 for x in w if x <= v) / len(w) if len(w) >= min_hist else None)
    return out


def _chg(series, k):
    return [None if i < k else series[i] - series[i - k] for i in range(len(series))]


def candidates():
    """Return {name: [(date, side), ...]} for each candidate macro signal."""
    rows = _agg()
    d = [r["date"] for r in rows]
    rrai = [r["rrai_raw"] for r in rows]
    rp = [r["rrai_pct"] for r in rows]
    froth_share = [(r["froth"] / r["tickers"] if r["tickers"] else 0.0) for r in rows]
    fpct = _pct_rank(froth_share)
    bpct = _pct_rank([r["bull_frac"] for r in rows])
    rpct = _pct_rank([r["bear_frac"] for r in rows])
    rrai_chg5 = _chg(rrai, 5)
    n = len(rows)

    def where(cond, side):
        return [(d[i], side) for i in range(n) if cond(i)]

    return {
        # baseline / control
        "capitulation_long":        where(lambda i: rp[i] is not None and rp[i] <= 0.15, +1),
        "euphoria_short":           where(lambda i: rp[i] is not None and rp[i] >= 0.85, -1),
        # RRAI trend / momentum (both directions)
        "rrai_up_momentum_long":    where(lambda i: rrai_chg5[i] is not None and rrai_chg5[i] > 0.08, +1),
        "rrai_up_fade_short":       where(lambda i: rrai_chg5[i] is not None and rrai_chg5[i] > 0.08, -1),
        "rrai_down_momentum_short": where(lambda i: rrai_chg5[i] is not None and rrai_chg5[i] < -0.08, -1),
        "rrai_down_fade_long":      where(lambda i: rrai_chg5[i] is not None and rrai_chg5[i] < -0.08, +1),
        # froth (speculation intensity) regime
        "froth_high_short":         where(lambda i: fpct[i] is not None and fpct[i] >= 0.85, -1),
        "froth_high_long":          where(lambda i: fpct[i] is not None and fpct[i] >= 0.85, +1),
        # sentiment-share extremes (contrarian)
        "bear_extreme_long":        where(lambda i: rpct[i] is not None and rpct[i] >= 0.85, +1),
        "bull_extreme_short":       where(lambda i: bpct[i] is not None and bpct[i] >= 0.85, -1),
    }


def run(horizon=10):
    px = _load_prices()
    out = []
    for name, sig in candidates().items():
        for instr, sp in INSTRUMENTS:
            s = [(instr, dt, side) for dt, side in sig]
            r = run_event_study(f"{name}", s, horizon=horizon, spread_bps=sp,
                                borrow_bps_day=0.0, market_neutral=False, px=px)
            r["instrument"] = instr
            out.append(r)
    return out


def benchmark(horizon=10, px=None):
    """Unconditional N-day drift per instrument over the signal window = the buy-and-hold
    bar a macro LONG must beat (a bull market makes 'long anything' look like a winner)."""
    px = px or _load_prices()
    c = get_connection()
    dates = [r["date"] for r in c.execute(
        "SELECT date FROM aggregate_daily WHERE rrai_pct IS NOT NULL ORDER BY date").fetchall()]
    c.close()
    out = {}
    for instr, _ in INSTRUMENTS:
        rs = [_fwd(px, instr, d, horizon) for d in dates]
        rs = [x for x in rs if x is not None]
        out[instr] = statistics.mean(rs) if rs else 0.0
    return out


def main(horizon=10):
    print(f"=== Macro strategy R&D (horizon={horizon}d, net of CFD spread) ===")
    print("    EXCESS = strategy net - buy-and-hold drift. SURVIVOR = EXCESS > 0 (beats long-only).\n")
    px = _load_prices()
    drift = benchmark(horizon=horizon, px=px)
    print("    buy-and-hold drift: " +
          "  ".join(f"{i.split('=')[0]}={drift[i]*100:+.2f}%" for i, _ in INSTRUMENTS) + "\n")
    rows = run(horizon=horizon)
    names = []
    for r in rows:
        if r["strategy"] not in names:
            names.append(r["strategy"])
    for name in names:
        print(name)
        for r in [x for x in rows if x["strategy"] == name]:
            if r.get("note"):
                print(f"    {r['instrument']:9s} n={r.get('tradeable',0):<4} {r['note']}")
                continue
            net = r["net_avg_pct"]
            excess = net - drift[r["instrument"]] * 100
            tag = "  SURVIVOR" if excess > 0 else ""
            print(f"    {r['instrument']:9s} n={r['tradeable']:<4} net={net:+.2f}% "
                  f"excess={excess:+.2f}pp win={r['win_pct']:.0f}% t={r['t_stat']:+.2f}{tag}")


if __name__ == "__main__":
    main()
