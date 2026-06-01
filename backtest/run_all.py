"""Master backtest: EVERY signal vs BUY-AND-HOLD.

The buy-and-hold column is the whole point - a bull market makes 'long anything' look like
alpha, so each strategy is judged on EXCESS over holding the benchmark, not raw return.
Costs: macro = tight CFD spread; per-ticker equity = wide small-cap spread + short borrow.
Run: python -m backtest.run_all
"""
import statistics

from export.database import get_connection
from backtest.engine import _load_prices, _fwd, _net
from backtest.sectors import universes

H = 10


def _dates(op):
    c = get_connection()
    r = [x["date"] for x in c.execute(
        f"SELECT date FROM aggregate_daily WHERE rrai_pct {op} ORDER BY date").fetchall()]
    c.close()
    return r


def _feat(where):
    c = get_connection()
    r = [(x["ticker"], x["date"]) for x in c.execute(
        f"SELECT ticker, date FROM feature_daily WHERE {where}").fetchall()]
    c.close()
    return r


def _all_dates():
    c = get_connection()
    r = [x["date"] for x in c.execute("SELECT date FROM aggregate_daily ORDER BY date").fetchall()]
    c.close()
    return r


def _run(px, name, events, side, bh_drift, spread):
    """bh_drift = the benchmark's UNCONDITIONAL mean fwd return (buy-and-hold-anytime baseline).
    excess = strategy return - bh_drift: does timing the signal beat just holding the benchmark?"""
    borrow = 8.0 if side < 0 else 0.0
    rets = []
    for instr, d in events:
        nr = _net(px, instr, d, side, H, spread, borrow, False)
        if nr is not None:
            rets.append(nr)
    if len(rets) < 5:
        return (name, len(rets), None, None, None, None)
    m = statistics.mean(rets)
    win = sum(1 for x in rets if x > 0) / len(rets) * 100
    return (name, len(rets), m * 100, bh_drift * 100, (m - bh_drift) * 100, win)


def run():
    px = _load_prices()
    U = universes()
    all_d = _all_dates()
    caps, euph = _dates("<=0.15"), _dates(">=0.85")

    def drift(instr):  # unconditional mean fwd H-day return over ALL days = buy-and-hold baseline
        rs = [_fwd(px, instr, d, H) for d in all_d]
        rs = [x for x in rs if x is not None]
        return statistics.mean(rs) if rs else 0.0

    DR = {s: drift(s) for s in ("AUDJPY=X", "SPY", "GC=F")}
    rows = [
        _run(px, "retail_fear AUDJPY (long)", [("AUDJPY=X", d) for d in caps], +1, DR["AUDJPY=X"], 3),
        _run(px, "retail_fear SPY (long)",    [("SPY", d) for d in caps],      +1, DR["SPY"], 2),
        _run(px, "retail_fear Gold (long)",   [("GC=F", d) for d in caps],     +1, DR["GC=F"], 4),
        _run(px, "euphoria_short SPY (short)", [("SPY", d) for d in euph],     -1, DR["SPY"], 2),
        _run(px, "smallcap_pump_fade (short)",
             [(t, d) for t, d in _feat("per_author>=3 AND mentions>=8") if t in U["smallcap"]],
             -1, DR["SPY"], 100),
        _run(px, "biotech_catalyst (long)",
             [(t, d) for t, d in _feat("per_author>=2.5 AND mentions>=5") if t in U["biotech"]],
             +1, DR["SPY"], 150),
        _run(px, "attention_fade (short)",
             _feat("mentions_z>=2.5 AND concentration<=0.15 AND breadth>=8"),
             -1, DR["SPY"], 75),
        _run(px, "organic_long (record/control)",
             _feat("mentions_z>=2.5 AND concentration<=0.15 AND breadth>=8"),
             +1, DR["SPY"], 75),
    ]
    print(f"=== MASTER BACKTEST vs BUY-AND-HOLD ({H}d horizon, net of costs) ===\n")
    print(f"{'STRATEGY':30s} {'n':>4} {'net%':>7} {'B&H%':>7} {'excess':>7} {'win%':>5}  verdict")
    print("-" * 78)
    for name, n, net, bh, exc, win in rows:
        if net is None:
            print(f"{name:30s} {n:>4}   (too few trades)")
            continue
        verdict = "BEATS B&H" if exc > 0 else "no edge"
        print(f"{name:30s} {n:>4} {net:>+7.2f} {bh:>+7.2f} {exc:>+7.2f} {win:>5.0f}  {verdict}")
    print("\nNote: B&H = mean fwd return of the benchmark over the SAME signal dates.")
    print("Shorts also pay borrow; small-cap spreads are punishing (100-150bps modelled).")


if __name__ == "__main__":
    run()
