"""Three-regime test for distribution_short — the out-of-regime validation the 12-month window
could never provide. Requires the deep backfill (archive_backfill_deep -> 2021-2022) + features
recomputed at lookback_days>=2200 + deep prices (fetch_prices rng='5y') + a rebuilt research store.

Verdict (2026-06-03, 232k-post 5-sub backfill): distribution_short is REGIME-CONDITIONAL.
  2021 mania  : +0.06%  null 47th  -> NO edge (rolling-over pumps RE-SQUEEZE in mania)
  2022 bear   : +18.7%  null 91st  -> works (thin, n=7)
  2025-26 now : +21.0%  null 100th -> strong
It generalizes to bear + current (not a 2025 artifact) but is neutralized in meme-mania.
Deploy with a squeeze-regime gate + always via puts.

  docker compose exec mcp python -m backtest.regime_test
"""
import random
import statistics
from collections import defaultdict

from backtest.microstructure import _rows, _trailing_ret
from backtest.engine import run_event_study, _load_prices, _net
from backtest.dist_short_oos import ETFS as BLOCK

REGIME = {"2021": "2021 mania", "2022": "2022 bear", "2023": "2023", "2024": "2024",
          "2025": "2025-26 now", "2026": "2025-26 now"}


def run(lookback_days=2200):
    px = _load_prices()
    rows = [d for d in _rows(lookback_days=lookback_days, use_store=True)
            if d["new_frac_trail"] is not None and d["ticker"] not in BLOCK]
    sig = [d for d in rows if (d["per_author"] or 0) >= 2 and (d["mentions"] or 0) >= 10
           and (d["sentiment"] or -9) >= 0.4 and (_trailing_ret(px, d["ticker"], d["date"], 5) or 9) < 0]
    by = defaultdict(list)
    for d in sig:
        by[REGIME.get(d["date"][:4], "other")].append(d)
    tdates = defaultdict(list)
    for d in rows:
        tdates[d["ticker"]].append(d["date"])

    def study(ev):
        return run_event_study("x", [(d["ticker"], d["date"], -1) for d in ev], horizon=10,
                               spread_bps=75, borrow_bps_day=8.0, market_neutral=True, px=px)

    def nullpct(ev):
        def mn(e):
            r = [_net(px, t, dt, -1, 10, 75, 8.0, True) for t, dt in e]
            r = [x for x in r if x is not None]
            return statistics.mean(r) * 100 if len(r) >= 5 else None
        actual = mn([(d["ticker"], d["date"]) for d in ev])
        if actual is None:
            return None
        rng = random.Random(42)
        ns = sorted(x for x in (mn([(d["ticker"], rng.choice(tdates[d["ticker"]])) for d in ev])
                                for _ in range(300)) if x is not None)
        return 100 * sum(1 for x in ns if x < actual) / len(ns) if ns else None

    print(f"=== distribution_short three-regime test (signals total={len(sig)}) ===")
    for r in ("2021 mania", "2022 bear", "2025-26 now"):
        ev = by.get(r, [])
        res = study(ev)
        if "note" in res:
            print(f"  {r:13}: {len(ev)} events, {res['tradeable']} priced (too few)")
            continue
        p = nullpct(ev)
        ps = f" null={p:.0f}th" if p is not None else ""
        print(f"  {r:13}: n={res['tradeable']:>3} net={res['net_avg_pct']:+6.2f}% "
              f"win={res['win_pct']:>3.0f}% t={res['t_stat']:+.2f}{ps}")


if __name__ == "__main__":
    run()
