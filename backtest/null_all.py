"""Exhaustive random-entry null across EVERY strategy — the mandatory final gate.

For each strategy: compare its mean net return to (A) the same tickers on RANDOM dates and
(B) RANDOM ticker-days from the broad feature universe (same side/costs). A real edge sits at
the >95th percentile of the random distribution; ~50th = drift; <50th = worse than random.

  python -m backtest.null_all
"""
import random
import statistics
from collections import defaultdict

from export.database import get_connection
from backtest.engine import _net, _load_prices
from backtest.sectors import universes
from backtest.microstructure import _rows, _trailing_ret
from backtest.mt5_sim import random_entry_test
from backtest.fear_gate import capitulation_sets

H, M = 10, 400


def _equity():
    px = _load_prices()
    c = get_connection()
    U = universes()
    allfeat = [(r["ticker"], r["date"]) for r in c.execute("SELECT ticker, date FROM feature_daily").fetchall()]
    tdates = defaultdict(list)
    for t, d in allfeat:
        tdates[t].append(d)

    def feat(where):
        return [(r["ticker"], r["date"]) for r in
                c.execute(f"SELECT ticker, date FROM feature_daily WHERE {where}").fetchall()]

    mrows = [d for d in _rows() if d["new_frac_trail"] is not None]
    CONC = lambda d: (d["per_author"] or 0) >= 2.0 and (d["mentions"] or 0) >= 10
    dist = [(d["ticker"], d["date"]) for d in mrows if CONC(d) and (d["sentiment"] or -9) >= 0.4
            and (_trailing_ret(px, d["ticker"], d["date"], 5) or 9) < 0]

    # (name, events, side, spread_bps, borrow_bps_day)
    strats = [
        ("distribution_short", dist, -1, 75, 8.0),
        ("smallcap_pump_fade", [e for e in feat("per_author>=3 AND mentions>=8") if e[0] in U["smallcap"]], -1, 100, 8.0),
        ("biotech_catalyst", [e for e in feat("per_author>=2.5 AND mentions>=5") if e[0] in U["biotech"]], 1, 150, 0.0),
        ("attention_fade", feat("mentions_z>=2.5 AND concentration<=0.15 AND breadth>=8"), -1, 75, 8.0),
        ("organic_long", feat("mentions_z>=2.5 AND concentration<=0.15 AND breadth>=8"), 1, 75, 0.0),
        ("fade_organic_short", feat("mentions_z>=2 AND concentration<=0.15 AND breadth>=8"), -1, 75, 8.0),
        ("pump_long", feat("mentions_z>=2 AND per_author>=3 AND mentions>=10"), 1, 75, 0.0),
        ("dump_fade_short", feat("per_author>=3 AND mentions>=10"), -1, 75, 8.0),
        ("deletion_short", feat("per_author>=3 AND mentions>=10 AND gone_frac>=0.20"), -1, 75, 8.0),
    ]
    rng = random.Random(42)

    def mnet(ev, side, sp, bo):
        r = [_net(px, t, d, side, H, sp, bo, True) for t, d in ev]
        r = [x for x in r if x is not None]
        return (statistics.mean(r) * 100, len(r)) if len(r) >= 5 else (None, len(r))

    def pct(gen, side, sp, bo, actual):
        xs = sorted(v for v in (mnet(gen(), side, sp, bo)[0] for _ in range(M)) if v is not None)
        return 100.0 * sum(1 for x in xs if x < actual) / len(xs) if xs else 0.0

    print(f"  {'strategy':20} {'n':>4} {'net%':>7} {'randB(sel)':>10} {'randA(time)':>11}  verdict")
    for name, ev, side, sp, bo in strats:
        actual, n = mnet(ev, side, sp, bo)
        if actual is None:
            print(f"  {name:20} {n:>4}   (too few)")
            continue
        pb = pct(lambda: rng.sample(allfeat, min(len(ev), len(allfeat))), side, sp, bo, actual)
        pa = pct(lambda: [(t, rng.choice(tdates[t])) for t, d in ev if tdates[t]], side, sp, bo, actual)
        v = "REAL EDGE" if min(pa, pb) >= 95 else ("borderline" if min(pa, pb) >= 85 else
                                                   "no edge" if max(pa, pb) >= 40 else "WORSE than random")
        print(f"  {name:20} {n:>4} {actual:>+7.2f} {pb:>9.0f}th {pa:>10.0f}th  {v}")
    c.close()


def _macro():
    vix = capitulation_sets()["vixgated"]
    cfg = dict(turn=True, arm=5, stopATR=3.0, hold=11)
    print(f"\n  capitulation-long (per instrument), random-DATE null:")
    print(f"  {'instrument':12} {'n':>4} {'net%':>7} {'percentile':>11}  verdict")
    for sym in ("XAUUSD", "AUDJPY", "AUDUSD", "XAGUSD", "US500", "USTEC"):
        r = random_entry_test(sym, vix, M=400, start="2018-01-01", **cfg)
        if not r:
            print(f"  {sym:12} (no result)")
            continue
        v = "REAL EDGE" if r["percentile"] >= 95 else ("weak" if r["percentile"] >= 75 else
                                                       "no edge" if r["percentile"] >= 40 else "WORSE than random")
        print(f"  {sym:12} {r['n']:>4} {r['strat_net']:>+7.1f} {r['percentile']:>10.0f}th  {v}")


def run():
    print("=== EXHAUSTIVE RANDOM-ENTRY NULL — every strategy (1=beats random, the mandatory gate) ===\n")
    print(" EQUITY cross-sectional (randB=random tickers+dates, randA=random dates same tickers):")
    _equity()
    _macro()


if __name__ == "__main__":
    run()
