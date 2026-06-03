"""Drill 5: dilution-filing short on Reddit-pumped micro-caps (EDGAR x pump micro-structure).

Mechanism: pumps exist to enable dilution. The PIT-clean tradeable event is the OFFERING
FILING. Three tests, same discipline (clean universe, market-neutral, random-entry null):
  T1  offering-filing short: short when a recently-pumped micro-cap files an offering.
  T2  distribution_short + already-filing gate: the dilution is visibly in motion at signal.
  T3  descriptive: do pumps FOLLOWED by an offering drop more? (shows the mechanism)

  docker compose exec mcp python -m backtest.edgar_drill
"""
import time
import random
import statistics
from collections import defaultdict
from datetime import date

from backtest.drills import ROWS, CONC, PX, _study, _null
from backtest.engine import run_event_study, _net
import scraper.edgar as edgar


def _d(s):
    return date(int(s[:4]), int(s[5:7]), int(s[8:10]))


def _between(p, f, lo, hi):
    """True if filing f is in [p+lo, p+hi] days relative to pump p."""
    n = (_d(f) - _d(p)).days
    return lo <= n <= hi


def _fetch_offers(tickers):
    offer, insider = {}, {}
    for i, t in enumerate(tickers):
        offer[t] = edgar.offering_dates(t)
        insider[t] = edgar.insider_dates(t)
        time.sleep(0.12)                      # ~8 req/s, under SEC's 10/s
    return offer, insider


def run():
    pumpdays = defaultdict(list)
    for d in ROWS:
        if CONC(d):
            pumpdays[d["ticker"]].append(d["date"][:10])
    tickers = sorted(pumpdays)
    print(f"clean pump universe: {len(tickers)} tickers; fetching EDGAR offerings...")
    offer, insider = _fetch_offers(tickers)
    n_off = sum(len(v) for v in offer.values())
    have = sum(1 for t in tickers if offer[t])
    print(f"  {have}/{len(tickers)} tickers have offering filings; {n_off} offering dates total\n")

    # ---- T1: offering-filing SHORT, gated on a Reddit pump within the prior `back` days ----
    print("=== T1: offering-filing short (pumped within prior N days, enter at filing) ===")
    for back in (14, 30, 60):
        ev = []
        for t in tickers:
            for f in offer[t]:
                if any(_between(p, f, -back, 0) for p in pumpdays[t]):   # pump in [f-back, f]
                    ev.append((t, f, -1))
        # de-dup identical (ticker, date)
        ev = sorted(set(ev))
        if len(ev) < 5:
            print(f"  pump<= {back}d before filing: n={len(ev)} (too few)")
            continue
        r = run_event_study("x", ev, horizon=10, spread_bps=100, borrow_bps_day=8.0,
                            market_neutral=True, px=PX)
        reg = " ".join(f"{p[0]}={p[1]:+.0f}" for p in r["regime"]) or "-"
        # null: same tickers, random dates
        evd = [{"ticker": t, "date": d} for t, d, _ in ev]
        a, p = _null(evd, -1, 10, 100, 8.0)
        print(f"  pump<= {back}d before filing: n={r['tradeable']:>3} net={r['net_avg_pct']:+6.2f}% "
              f"win={r['win_pct']:>3.0f}% t={r['t_stat']:+.2f} null={p:.0f}th  [{reg}]")

    # ---- T2: distribution_short, gated on an offering already filed in the trailing window ----
    print("\n=== T2: distribution_short + offering already filed in trailing [-back,0] (PIT) ===")
    from backtest.microstructure import _trailing_ret
    dist = [d for d in ROWS if CONC(d) and (d["sentiment"] or -9) >= 0.4
            and (_trailing_ret(PX, d["ticker"], d["date"], 5) or 9) < 0]
    base = _study(dist, -1, 10, 75, 8.0)
    print(f"  base distribution_short: n={base['tradeable']} net={base['net_avg_pct']:+.2f}% t={base['t_stat']:+.2f}")
    for back in (30, 60, 90):
        ev = [d for d in dist
              if any(0 <= (_d(d["date"][:10]) - _d(f)).days <= back for f in offer.get(d["ticker"], []))]
        if len(ev) >= 5:
            r = _study(ev, -1, 10, 75, 8.0)
            print(f"  +offering in trailing {back}d: n={r['tradeable']:>3} net={r['net_avg_pct']:+6.2f}% "
                  f"win={r['win_pct']:>3.0f}% t={r['t_stat']:+.2f}")
        else:
            print(f"  +offering in trailing {back}d: n={len(ev)} (too few)")

    # ---- T3: descriptive — do pumps FOLLOWED by an offering within 10d drop more? ----
    print("\n=== T3: descriptive — pump forward return, split by offering filed within +10d ===")
    with_off, without = [], []
    for d in ROWS:
        if not CONC(d):
            continue
        fwd = _net(PX, d["ticker"], d["date"], 1, 10, 100, 0.0, True)   # forward LONG excess
        if fwd is None:
            continue
        hit = any(_between(d["date"][:10], f, 1, 10) for f in offer.get(d["ticker"], []))
        (with_off if hit else without).append(fwd)
    if with_off and without:
        print(f"  pump THEN offering<=10d: n={len(with_off):>3} fwd LONG excess={statistics.mean(with_off)*100:+.2f}%")
        print(f"  pump, no offering<=10d:  n={len(without):>3} fwd LONG excess={statistics.mean(without)*100:+.2f}%")
        print(f"  -> if 'THEN offering' is more negative, the imminent dilution drives the drop")


if __name__ == "__main__":
    run()
