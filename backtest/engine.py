"""Event-study backtester over the point-in-time feature store.

Reads feature_daily / aggregate_daily + prices, applies a strategy's entry rule, and
measures NET forward returns with realistic frictions. Three disciplines baked in:

  1. NO LOOKAHEAD - a signal on date D is entered at the first close STRICTLY AFTER D
     (we can't trade on D's close using mentions that accrue through day D).
  2. FRICTIONS - round-trip spread (small-caps are wide) + per-day short borrow.
  3. REGIME SPLIT - net return per sub-period, because the sentiment edge looked great
     until it flipped sign across regimes. Sign-stability is the bar, not a big average.

Costs are deliberately conservative defaults; penny-stock reality can be worse.
"""
import bisect
import math
import statistics

from export.database import get_connection


def _load_prices():
    conn = get_connection()
    rows = conn.execute("SELECT ticker, date, close FROM prices ORDER BY ticker, date").fetchall()
    conn.close()
    px = {}
    for r in rows:
        d, c = px.setdefault(r["ticker"], ([], []))
        d.append(r["date"])
        c.append(r["close"])
    return px


def _fwd(px, ticker, signal_date, horizon):
    """Gross return: enter at first close STRICTLY AFTER signal_date, exit `horizon`
    sessions later. Returns None if prices are missing or the window runs off the end."""
    if ticker not in px:
        return None
    dates, closes = px[ticker]
    i = bisect.bisect_right(dates, signal_date[:10])  # first index with date > signal_date
    if i >= len(dates) or i + horizon >= len(dates):
        return None
    p0, p1 = closes[i], closes[i + horizon]
    return (p1 / p0 - 1) if p0 > 0 else None


def _net(px, ticker, date, side, horizon, spread_bps, borrow_bps_day, market_neutral):
    """Side-adjusted, cost-adjusted, optionally market-neutral (excess vs SPY) return."""
    g = _fwd(px, ticker, date, horizon)
    if g is None:
        return None
    if market_neutral:
        m = _fwd(px, "SPY", date, horizon)
        if m is None:
            return None
        ret = (g - m) * side
    else:
        ret = g * side
    cost = 2 * spread_bps / 10000.0                       # round-trip spread
    if side < 0:
        cost += borrow_bps_day * horizon / 10000.0        # short borrow over the hold
    return ret - cost


def _stats(rets):
    n = len(rets)
    if n < 2:
        return None
    m = statistics.mean(rets)
    sd = statistics.pstdev(rets) or 1e-9
    return {"n": n, "avg_net": m, "win": sum(1 for x in rets if x > 0) / n,
            "t": m / (sd / math.sqrt(n)), "sd": sd}


def run_event_study(name, signals, horizon=5, spread_bps=75, borrow_bps_day=5.0,
                    market_neutral=True, px=None):
    """signals: list of (ticker, signal_date, side). Returns a result dict with overall
    net stats, a gross-vs-net comparison, and a 3-way regime split (sign stability)."""
    px = px or _load_prices()
    dated = []  # (date, net_ret)
    gross = []
    for ticker, date, side in signals:
        nr = _net(px, ticker, date, side, horizon, spread_bps, borrow_bps_day, market_neutral)
        if nr is None:
            continue
        g = _fwd(px, ticker, date, horizon)
        gm = _fwd(px, "SPY", date, horizon) if market_neutral else 0.0
        if g is None or gm is None:
            continue
        gross.append(((g - (gm or 0)) * side))
        dated.append((date, nr))
    dated.sort()
    rets = [r for _, r in dated]
    s = _stats(rets)
    if not s:
        return {"strategy": name, "tradeable": len(rets), "note": "too few trades"}
    # annualized Sharpe estimate (assumes ~non-overlapping; approximate)
    sharpe = (s["avg_net"] / s["sd"]) * math.sqrt(252.0 / horizon)
    # regime split into thirds by signal date
    thirds = []
    k = len(rets) // 3
    if k >= 5:
        for lbl, lo, hi in (("P1", 0, k), ("P2", k, 2 * k), ("P3", 2 * k, len(rets))):
            seg = [r for _, r in dated[lo:hi]]
            ss = _stats(seg)
            thirds.append((lbl, ss["avg_net"] if ss else None, len(seg)))
    return {
        "strategy": name, "horizon": horizon, "market_neutral": market_neutral,
        "tradeable": s["n"],
        "gross_avg": round(statistics.mean(gross) * 100, 3) if gross else None,
        "net_avg_pct": round(s["avg_net"] * 100, 3),
        "win_pct": round(s["win"] * 100, 1),
        "t_stat": round(s["t"], 2),
        "sharpe_approx": round(sharpe, 2),
        "regime": [(l, round(v * 100, 3) if v is not None else None, n) for l, v, n in thirds],
        "costs": {"spread_bps": spread_bps, "borrow_bps_day": borrow_bps_day},
    }


# ---- strategies (entry rules over the feature store) ----

def sig_pump_long(conn, min_z=2.0, min_pa=3.0, min_m=10):
    """Concentrated promotion spike - the validated +edge bucket. LONG (ride the ramp)."""
    return [(r["ticker"], r["date"], +1) for r in conn.execute(
        "SELECT ticker, date FROM feature_daily WHERE per_author>=? AND mentions>=? AND mentions_z>=?",
        (min_pa, min_m, min_z))]


def sig_dump_fade_short(conn, min_pa=3.0, min_m=10):
    """Concentrated pump that is now DECELERATING (accel<0) - fade the dump. SHORT."""
    return [(r["ticker"], r["date"], -1) for r in conn.execute(
        "SELECT ticker, date FROM feature_daily WHERE per_author>=? AND mentions>=? AND accel<0",
        (min_pa, min_m))]


def sig_organic_long(conn, min_z=2.0, max_conc=0.15, min_breadth=8):
    """Broad, low-concentration attention surge (many distinct authors) - organic. LONG."""
    return [(r["ticker"], r["date"], +1) for r in conn.execute(
        "SELECT ticker, date FROM feature_daily WHERE mentions_z>=? AND concentration<=? AND breadth>=?",
        (min_z, max_conc, min_breadth))]


def sig_fade_organic_short(conn, min_z=2.0, max_conc=0.15, min_breadth=8):
    """Inverse of organic_long: broad attention spikes mean-revert DOWN (buying the top is
    a robust loser), so FADE them. SHORT. Distinct from fading a PUMP - broad/news-driven
    spikes revert rather than squeeze."""
    return [(r["ticker"], r["date"], -1) for r in conn.execute(
        "SELECT ticker, date FROM feature_daily WHERE mentions_z>=? AND concentration<=? AND breadth>=?",
        (min_z, max_conc, min_breadth))]


def sig_dump_fade_deletion_short(conn, min_pa=3.0, min_m=10, min_gone=0.10):
    """THE actual A2 thesis: short a concentrated pump whose promoters are now gone
    (deleted/suspended). Needs author coverage - sparse until the profiling sweep fills
    young_frac/gone_frac. SHORT, via puts in practice (defined risk vs squeeze tails)."""
    return [(r["ticker"], r["date"], -1) for r in conn.execute(
        "SELECT ticker, date FROM feature_daily WHERE per_author>=? AND mentions>=? AND gone_frac>=?",
        (min_pa, min_m, min_gone))]


def sig_rrai_extreme(conn, instrument, lo=None, hi=None, side=+1):
    """Macro: fire on RRAI percentile extremes. lo => capitulation (retail fear),
    hi => euphoria (retail greed). instrument is whatever we have prices for (SPY/QQQ/FX)."""
    if lo is not None:
        rows = conn.execute("SELECT date FROM aggregate_daily WHERE rrai_pct IS NOT NULL AND rrai_pct<=?", (lo,))
    else:
        rows = conn.execute("SELECT date FROM aggregate_daily WHERE rrai_pct IS NOT NULL AND rrai_pct>=?", (hi,))
    return [(instrument, r["date"], side) for r in rows.fetchall()]


def run_all(horizon=5):
    """Run the v1 strategy slate and return a list of result dicts."""
    conn = get_connection()
    px = _load_prices()
    out = []
    # --- equity (cross-sectional), small-cap frictions, market-neutral vs SPY ---
    out.append(run_event_study("pump_long (concentrated, ride)", sig_pump_long(conn),
                               horizon=horizon, spread_bps=75, market_neutral=True, px=px))
    out.append(run_event_study("dump_fade_short (decelerating)", sig_dump_fade_short(conn),
                               horizon=horizon, spread_bps=75, borrow_bps_day=8.0, market_neutral=True, px=px))
    out.append(run_event_study("organic_long (broad breadth)", sig_organic_long(conn),
                               horizon=horizon, spread_bps=75, market_neutral=True, px=px))
    out.append(run_event_study("fade_organic_short (survivor)", sig_fade_organic_short(conn),
                               horizon=horizon, spread_bps=75, borrow_bps_day=8.0, market_neutral=True, px=px))
    out.append(run_event_study("dump_fade_DELETION_short (thesis)", sig_dump_fade_deletion_short(conn),
                               horizon=horizon, spread_bps=75, borrow_bps_day=8.0, market_neutral=True, px=px))
    # --- macro overlay: liquid instruments -> tiny spread, no borrow, NOT market-neutral ---
    for instr, sp in (("SPY", 2), ("QQQ", 2), ("AUDJPY=X", 3)):  # AUDJPY = risk-on FX proxy
        out.append(run_event_study(f"rrai_capitulation_long ({instr})",
                                   sig_rrai_extreme(conn, instr, lo=0.15, side=+1),
                                   horizon=horizon, spread_bps=sp, borrow_bps_day=0.0,
                                   market_neutral=False, px=px))
    out.append(run_event_study("rrai_euphoria_short (SPY, control)",
                               sig_rrai_extreme(conn, "SPY", hi=0.85, side=-1),
                               horizon=horizon, spread_bps=2, borrow_bps_day=0.0,
                               market_neutral=False, px=px))
    conn.close()
    return out


def _fmt(r):
    if r.get("note"):
        return f"  {r['strategy']:38s} n={r.get('tradeable',0):<5} {r['note']}"
    reg = "  ".join(f"{l}={v:+.2f}" if v is not None else f"{l}=NA" for l, v, _ in r["regime"])
    sign_stable = all((v is not None and (v > 0) == (r["net_avg_pct"] > 0)) for l, v, _ in r["regime"]) if r["regime"] else False
    flag = "OK " if sign_stable else "!! "
    return (f"  {flag}{r['strategy']:38s} n={r['tradeable']:<5} "
            f"net={r['net_avg_pct']:+.2f}% (gross {r['gross_avg']:+.2f}) win={r['win_pct']:.0f}% "
            f"t={r['t_stat']:+.2f} Sharpe~{r['sharpe_approx']:+.2f} | regime {reg}")


def main(horizon=5):
    print(f"=== Event-study backtest (horizon={horizon}d, net of frictions, market-neutral vs SPY where noted) ===")
    print("    flag: OK = sign-stable across all 3 regimes, !! = flips sign (suspect)\n")
    for r in run_all(horizon=horizon):
        print(_fmt(r))


if __name__ == "__main__":
    main()
