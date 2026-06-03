"""Edge-hunt drills — four extensions of the one proven vein (micro-cap pump micro-structure).

All share the discipline that survived this project: CLEAN micro-cap universe (blocklist from
dist_short_oos), market-neutral excess vs SPY, realistic frictions, random-entry null, regime read.
Treat every headline as a hypothesis until it clears the null AND is sign-stable across the window.

  docker compose exec mcp python -m backtest.drills <1|2|3|4|all>
"""
import sys
import bisect
import random
import statistics
from collections import defaultdict

from export.database import get_connection
from backtest.engine import run_event_study, _load_prices, _net
from backtest.microstructure import _rows, _trailing_ret
from backtest.dist_short_oos import ETFS as BLOCK

PX = _load_prices()
ROWS = [d for d in _rows() if d["new_frac_trail"] is not None and d["ticker"] not in BLOCK]
CONC = lambda d: (d["per_author"] or 0) >= 2 and (d["mentions"] or 0) >= 10


def _study(ev, side, h=10, sp=100, bo=8.0):
    sig = [(d["ticker"], d["date"], side) for d in ev]
    return run_event_study("x", sig, horizon=h, spread_bps=sp, borrow_bps_day=bo,
                           market_neutral=True, px=PX)


def _null(ev, side, h, sp, bo, M=400):
    """Random-entry null: same tickers, random dates. Returns percentile of actual mean."""
    tdates = defaultdict(list)
    for d in ROWS:
        tdates[d["ticker"]].append(d["date"])
    rng = random.Random(42)

    def mnet(evlist):
        r = [_net(PX, t, dt, side, h, sp, bo, True) for t, dt in evlist]
        r = [x for x in r if x is not None]
        return statistics.mean(r) * 100 if len(r) >= 5 else None

    actual = mnet([(d["ticker"], d["date"]) for d in ev])
    if actual is None:
        return None, None
    nets = sorted(x for x in
                  (mnet([(d["ticker"], rng.choice(tdates[d["ticker"]])) for d in ev]) for _ in range(M))
                  if x is not None)
    pct = 100.0 * sum(1 for x in nets if x < actual) / len(nets)
    return actual, pct


# ---------------------------------------------------------------- DRILL 1: failed-pump short
def drill1():
    print("=== DRILL 1: FAILED-PUMP SHORT (concentrated pump, recruiting+accel, price flat) ===")
    print("    hypothesis flipped: longing recruitment LOSES (t=-4); so SHORT the manufactured pump\n")

    def setup(nz=0.5, tm=0.05, require_down=None):
        out = []
        for d in ROWS:
            if not CONC(d) or (d["new_z"] or -9) < nz:
                continue
            if d["accel"] is None or d["accel"] < 0:
                continue
            tr = _trailing_ret(PX, d["ticker"], d["date"], 5)
            if tr is None or tr > tm:
                continue
            if require_down is True and tr >= 0:
                continue
            if require_down is False and tr < 0:        # flat/up only = the NEW non-overlap part
                continue
            out.append(d)
        return out

    for lbl, rd in [("all trail<=0.05", None),
                    ("trail<0 (overlaps dist_short)", True),
                    ("trail in [0,0.05] (NEW, non-overlap)", False)]:
        ev = setup(require_down=rd)
        for h in (5, 10):
            if len(ev) >= 5:
                r = _study(ev, -1, h)
                reg = " ".join(f"{p[0]}={p[1]:+.0f}" for p in r["regime"]) or "-"
                print(f"  {lbl:36} h={h:>2}: n={r['tradeable']:>3} net={r['net_avg_pct']:+6.2f}% "
                      f"win={r['win_pct']:>3.0f}% t={r['t_stat']:+.2f}  [{reg}]")
    ev = setup()
    a, p = _null(ev, -1, 5, 100, 8.0)
    print(f"\n  random-entry null (h=5 short): actual={a:+.2f}% -> {p:.0f}th percentile "
          f"({'REAL' if p and p >= 95 else 'not significant'})")


# ---------------------------------------------------------------- DRILL 2: squeeze / short_volume
def _sr_lookup():
    c = get_connection()
    sv = {}
    for r in c.execute("SELECT ticker,date,short_ratio FROM short_volume ORDER BY ticker,date").fetchall():
        d, v = sv.setdefault(r["ticker"], ([], []))
        d.append(r["date"][:10]); v.append(r["short_ratio"])
    c.close()
    return sv


def _sr_asof(sv, t, date, k=5):
    if t not in sv:
        return None
    ds, rs = sv[t]
    i = bisect.bisect_right(ds, date[:10]) - 1
    if i < k:
        return None
    vals = [x for x in rs[i - k:i] if x is not None]
    return statistics.mean(vals) if vals else None


def drill2():
    print("=== DRILL 2: SQUEEZE / SHORT-VOLUME (short_ratio as timing signal + tail filter) ===\n")
    sv = _sr_lookup()
    dist = [d for d in ROWS if CONC(d) and (d["sentiment"] or -9) >= 0.4
            and (_trailing_ret(PX, d["ticker"], d["date"], 5) or 9) < 0]
    data = []
    for d in dist:
        sr = _sr_asof(sv, d["ticker"], d["date"])
        net = _net(PX, d["ticker"], d["date"], -1, 10, 75, 8.0, True)
        if sr is not None and net is not None:
            data.append((sr, net))
    data.sort()
    print(f"  distribution_short signals with short_ratio coverage: n={len(data)}")
    if len(data) >= 10:
        h = len(data) // 2
        lo = [n for _, n in data[:h]]; hi = [n for _, n in data[h:]]
        print(f"    LOW  short_ratio half: mean short net={statistics.mean(lo)*100:+.2f}% (n={len(lo)})")
        print(f"    HIGH short_ratio half: mean short net={statistics.mean(hi)*100:+.2f}% (n={len(hi)})")
        print(f"    -> if HIGH underperforms, short_ratio flags squeeze-prone names to SKIP (tail filter)")
    # standalone: does short_ratio predict forward LONG returns across all clean pumps?
    alld = []
    for d in ROWS:
        if not CONC(d):
            continue
        sr = _sr_asof(sv, d["ticker"], d["date"])
        fwd = _net(PX, d["ticker"], d["date"], 1, 10, 100, 0.0, True)
        if sr is not None and fwd is not None:
            alld.append((sr, fwd))
    alld.sort()
    if len(alld) >= 20:
        h = len(alld) // 2
        print(f"\n  [all clean pumps] forward LONG excess: low-SR={statistics.mean([f for _,f in alld[:h]])*100:+.2f}% "
              f"hi-SR={statistics.mean([f for _,f in alld[h:]])*100:+.2f}%  (n={len(alld)})")


# ---------------------------------------------------------------- DRILL 3: deletion overlay
def drill3():
    print("=== DRILL 3: DELETION OVERLAY (gate distribution_short on authors gone/suspended) ===")
    print("    CAVEAT: feature_daily gone_frac uses CURRENT account_status -> LOOKAHEAD-suspect.")
    print("    Treat any improvement as an upper bound until reconstructed point-in-time.\n")
    c = get_connection()
    gf = {}
    for r in c.execute("SELECT ticker,date,gone_frac,suspended_frac,young_frac FROM feature_daily").fetchall():
        gf[(r["ticker"], r["date"][:10])] = (r["gone_frac"], r["suspended_frac"], r["young_frac"])
    c.close()
    dist = [d for d in ROWS if CONC(d) and (d["sentiment"] or -9) >= 0.4
            and (_trailing_ret(PX, d["ticker"], d["date"], 5) or 9) < 0]
    base = _study(dist, -1, 10, 75, 8.0)
    print(f"  base distribution_short: n={base['tradeable']} net={base['net_avg_pct']:+.2f}% "
          f"win={base['win_pct']:.0f}% t={base['t_stat']:+.2f}")
    for name, idx in [("gone_frac", 0), ("suspended_frac", 1), ("young_frac", 2)]:
        for thr in (0.1, 0.2, 0.3):
            ev = [d for d in dist if gf.get((d["ticker"], d["date"][:10]))
                  and gf[(d["ticker"], d["date"][:10])][idx] is not None
                  and gf[(d["ticker"], d["date"][:10])][idx] >= thr]
            if len(ev) >= 5:
                r = _study(ev, -1, 10, 75, 8.0)
                print(f"    +{name}>={thr}: n={r['tradeable']:>3} net={r['net_avg_pct']:+6.2f}% "
                      f"win={r['win_pct']:>3.0f}% t={r['t_stat']:+.2f}")


# ---------------------------------------------------------------- DRILL 4: cross-sectional book
def drill4():
    print("=== DRILL 4: CROSS-SECTIONAL BOOK (daily market-neutral short basket) ===\n")
    # each signal -> a 10d short held from entry; aggregate into a daily equity curve
    dist = [d for d in ROWS if CONC(d) and (d["sentiment"] or -9) >= 0.4
            and (_trailing_ret(PX, d["ticker"], d["date"], 5) or 9) < 0]
    trades = []
    for d in dist:
        net = _net(PX, d["ticker"], d["date"], -1, 10, 75, 8.0, True)
        if net is not None:
            trades.append((d["date"][:10], net))
    trades.sort()
    if not trades:
        print("  (no trades)")
        return
    # daily P&L: each trade contributes net/10 per day over its 10-session life (approx, equal-weight)
    n = len(trades)
    rets = [t[1] for t in trades]
    mean = statistics.mean(rets); sd = statistics.pstdev(rets) or 1e-9
    # event-level Sharpe (per-trade, annualized assuming ~non-overlap at h=10)
    import math
    sharpe = (mean / sd) * math.sqrt(252.0 / 10)
    wins = [r for r in rets if r > 0]
    print(f"  n={n} trades  mean={mean*100:+.2f}%  win={100*len(wins)/n:.0f}%  "
          f"per-trade Sharpe(ann)~{sharpe:.2f}")
    # breadth: how many concurrent positions on an average signal day?
    bydate = defaultdict(int)
    for dt, _ in trades:
        bydate[dt] += 1
    print(f"  signal days={len(bydate)}  avg names/active-day={n/len(bydate):.1f}  "
          f"max same-day={max(bydate.values())}")
    print(f"  -> capacity is the binding constraint (micro-caps); breadth is thin but positive-mean")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    for k, fn in [("1", drill1), ("2", drill2), ("3", drill3), ("4", drill4)]:
        if which in (k, "all"):
            fn(); print()
