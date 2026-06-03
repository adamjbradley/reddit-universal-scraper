"""Drill 6: notable-holder / smart-money signals (EDGAR stake + insider filings).

Tests whether disclosures by market-moving holders predict forward returns on our priced
Reddit universe, with the usual discipline (market-neutral vs SPY, frictions, random null):
  A. 13D activist filings  — a >5% stake WITH intent to influence; often pops the stock.
  B. 13G passive filings   — a >5% passive stake; weaker, informational.
  C. Form-4 insider BUYS    — open-market purchases (code P); the classic documented edge.
  D. intersection           — does Reddit attention amplify the move?

Entry is the first close AFTER the filing date (PIT: the filing is public that day).

  docker compose exec mcp python -m backtest.holders_drill
"""
import time
import math
import statistics
import random
from collections import defaultdict
from datetime import date

from export.database import get_connection
from backtest.engine import run_event_study, _load_prices, _net
import scraper.edgar as edgar

PX = _load_prices()


def _universe(n=150):
    c = get_connection()
    rows = c.execute("SELECT ticker, COUNT(*) cnt FROM ticker_mentions GROUP BY ticker "
                     "ORDER BY cnt DESC LIMIT ?", (n * 3,)).fetchall()
    c.close()
    return [r["ticker"] for r in rows if r["ticker"] in PX][:n]


def _mentions_lookup(univ):
    """(ticker,date)->mentions for the Reddit-attention intersection."""
    c = get_connection()
    m = defaultdict(int)
    for r in c.execute("SELECT ticker, date(created_utc) d, COUNT(*) n FROM ticker_mentions "
                       "GROUP BY ticker, d").fetchall():
        m[(r["ticker"], r["d"])] = r["n"]
    c.close()
    return m


def _study(events, side, h, label, sp=75, bo=0.0):
    return run_event_study(label, events, horizon=h, spread_bps=sp, borrow_bps_day=bo,
                           market_neutral=True, px=PX)


def _clustered(events, side, h, sp=75, bo=0.0):
    """Date-CLUSTERED stats: collapse all events on the same filing date to ONE observation
    (their mean), then t across distinct dates. Defuses pseudo-replication from filing-deadline
    clustering (e.g. the Feb-14 13G stampede), which inflates the naive event-study t-stat."""
    bydate = defaultdict(list)
    for t, d, _ in events:
        nr = _net(PX, t, d, side, h, sp, bo, True)
        if nr is not None:
            bydate[d].append(nr)
    dmeans = [statistics.mean(v) for v in bydate.values()]
    if len(dmeans) < 2:
        return None
    m = statistics.mean(dmeans)
    sd = statistics.pstdev(dmeans) or 1e-9
    return len(dmeans), m * 100, m / (sd / math.sqrt(len(dmeans)))


def _null(events, side, h, sp, bo, M=300):
    """Random dates on the same tickers."""
    tickers = sorted({t for t, _, _ in events})
    pooldates = {}
    for t in tickers:
        if t in PX:
            pooldates[t] = PX[t][0]
    rng = random.Random(42)

    def mnet(evs):
        r = [_net(PX, t, d, side, h, sp, bo, True) for t, d in evs]
        r = [x for x in r if x is not None]
        return statistics.mean(r) * 100 if len(r) >= 5 else None

    actual = mnet([(t, d) for t, d, _ in events])
    if actual is None:
        return None, None
    nets = sorted(x for x in
                  (mnet([(t, rng.choice(pooldates[t])) for t, _, _ in events if t in pooldates])
                   for _ in range(M)) if x is not None)
    if not nets:
        return actual, None
    return actual, 100.0 * sum(1 for x in nets if x < actual) / len(nets)


def run():
    univ = _universe(150)
    print(f"universe: {len(univ)} priced Reddit tickers; fetching EDGAR stake filings...")
    act, pas = defaultdict(list), defaultdict(list)
    for t in univ:
        act[t] = edgar.stake_dates(t, edgar.ACTIVIST)
        pas[t] = edgar.stake_dates(t, edgar.PASSIVE)
        time.sleep(0.10)
    print(f"  13D activist filings: {sum(len(v) for v in act.values())} | "
          f"13G passive: {sum(len(v) for v in pas.values())}\n")

    for name, store, side, bo in [("13D activist LONG", act, 1, 0.0),
                                  ("13D activist SHORT", act, -1, 8.0),
                                  ("13G passive LONG", pas, 1, 0.0)]:
        events = [(t, d, side) for t in univ for d in store[t]]
        print(f"  {name}: {len({(t,d) for t,d,_ in events})} events")
        for h in (5, 10, 20):
            r = _study(events, side, h, name, bo=bo)
            if "note" in r:
                print(f"    h={h:>2}: (too few: {r['tradeable']})")
                continue
            reg = " ".join(f"{p[0]}={p[1]:+.0f}" for p in r["regime"]) or "-"
            cl = _clustered(events, side, h, bo=bo)
            clstr = f"  CLUSTERED: dates={cl[0]} net={cl[1]:+.2f}% t={cl[2]:+.2f}" if cl else ""
            print(f"    h={h:>2}: n={r['tradeable']:>3} net={r['net_avg_pct']:+6.2f}% "
                  f"win={r['win_pct']:>3.0f}% t={r['t_stat']:+.2f}  [{reg}]{clstr}", flush=True)
        print(flush=True)

    if "stakes" in __import__("sys").argv:
        return                          # fast path: 13D/13G + intersection only
    # ---- C. Form-4 insider BUYS (slower: parses XML; subset of the most-mentioned names) ----
    print("=== Form-4 insider open-market BUYS (top 40 names, parsed; bounded) ===", flush=True)
    buys = defaultdict(list)
    for i, t in enumerate(univ[:40]):
        buys[t] = edgar.form4_buys(t, limit=15)
        if (i + 1) % 10 == 0:
            print(f"    ...parsed {i+1}/40 tickers", flush=True)
        time.sleep(0.05)
    nb = sum(len(v) for v in buys.values())
    print(f"  insider-buy filing days found: {nb}")
    events = [(t, d, 1) for t in univ[:50] for d in buys[t]]
    for h in (5, 10, 20):
        r = _study(events, 1, h, "insider-buy LONG")
        if "note" in r:
            print(f"  h={h:>2}: (too few: {r['tradeable']})")
            continue
        extra = ""
        if h == 10:
            a, p = _null(events, 1, h, 75, 0.0)
            extra = f" null={p:.0f}th" if p is not None else ""
        print(f"  insider-buy LONG h={h:>2}: n={r['tradeable']:>3} net={r['net_avg_pct']:+6.2f}% "
              f"win={r['win_pct']:>3.0f}% t={r['t_stat']:+.2f}{extra}")

    # ---- D. intersection: 13D on names with HIGH Reddit attention near the filing ----
    print("\n=== intersection: 13D filings split by Reddit attention (mentions in [-5,+5]d) ===")
    ment = _mentions_lookup(univ)

    def att(t, d):
        base = date(int(d[:4]), int(d[5:7]), int(d[8:10]))
        tot = 0
        for k in range(-5, 6):
            from datetime import timedelta
            tot += ment.get((t, (base + timedelta(days=k)).strftime("%Y-%m-%d")), 0)
        return tot

    hi, lo = [], []
    for t in univ:
        for d in act[t]:
            (hi if att(t, d) >= 5 else lo).append((t, d, 1))
    for lbl, ev in [("HIGH attention", hi), ("LOW attention", lo)]:
        r = _study(ev, 1, 10, lbl)
        if "note" not in r:
            print(f"  {lbl}: n={r['tradeable']:>3} net={r['net_avg_pct']:+6.2f}% "
                  f"win={r['win_pct']:>3.0f}% t={r['t_stat']:+.2f}")
        else:
            print(f"  {lbl}: (too few: {r['tradeable']})")


if __name__ == "__main__":
    run()
