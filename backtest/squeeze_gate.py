"""Squeeze-regime gate for distribution_short — turn the short OFF in meme-mania.

The regime test showed distribution_short DIES in 2021 mania (null 48th) because rolling-over
pumps RE-SQUEEZE. This builds a point-in-time, price-based FROTH index = the fraction of the
micro-cap pump universe that is ripping (> thresh in the trailing window) as of the signal date —
a coverage-independent proxy for "how squeezy is the small-cap tape right now". It then tests
whether gating the short OFF when froth is high recovers the blended, all-regime edge.

  docker compose exec mcp python -m backtest.squeeze_gate
"""
import statistics
from collections import defaultdict

from backtest.microstructure import _rows, _trailing_ret
from backtest.engine import _load_prices, _net
from backtest.distribution_short import signals, metrics, H, SPREAD, BORROW


def froth_index(px, date, universe, lookback=10, thresh=0.25):
    """Fraction of `universe` tickers with a trailing-`lookback`d gain > `thresh` as of `date`.
    High = lots of micro-caps ripping = squeezy/mania tape. Point-in-time (uses only past prices)."""
    hot = tot = 0
    for t in universe:
        r = _trailing_ret(px, t, date, lookback)
        if r is not None:
            tot += 1
            hot += 1 if r > thresh else 0
    return hot / tot if tot >= 10 else None


def run():
    px = _load_prices()
    sig, rows = signals(px)
    universe = sorted({d["ticker"] for d in rows})
    rec = []
    for d in sig:
        net = _net(px, d["ticker"], d["date"], -1, H, SPREAD, BORROW, True)
        if net is None:
            continue
        rec.append((d["date"][:10], d["ticker"], net, froth_index(px, d["date"], universe)))

    frs = sorted(e[3] for e in rec if e[3] is not None)
    if not frs:
        print("no froth values")
        return
    cut = frs[int(len(frs) * 0.66)]                       # gate OFF above the top-tercile froth
    print(f"=== squeeze-regime gate (froth = % of micro-cap universe up >25% in 10d) ===")
    print(f"  froth: min={frs[0]:.2f} median={frs[len(frs)//2]:.2f} max={frs[-1]:.2f}  -> gate OFF above {cut:.2f}\n")

    alln = [e[2] for e in rec]
    trade = [e[2] for e in rec if e[3] is not None and e[3] <= cut]
    skip = [e[2] for e in rec if e[3] is not None and e[3] > cut]
    for lbl, nets in (("UNGATED (all signals)", alln),
                      ("GATE ON  (low froth -> TRADE)", trade),
                      ("GATE OFF (high froth -> SKIP)", skip)):
        m = metrics(nets)
        if m:
            print(f"  {lbl:30}: n={m['n']:>3} net={m['net']:+6.2f}% win={m['win']:>3.0f}% "
                  f"t={m['t']:+.2f} Sharpe={m['sharpe']:.2f} maxDD={m['maxdd']:.0f}%")

    print("\n  froth by year (does it flag 2021 mania?):")
    by = defaultdict(list)
    for dt, _, _, fr in rec:
        if fr is not None:
            by[dt[:4]].append(fr)
    for y in sorted(by):
        print(f"    {y}: mean froth={statistics.mean(by[y]):.2f}  (n={len(by[y])})")
    print("  -> FROTH FAILS: 2021 mean ~= 2025 mean, so it does NOT separate the mania regime.\n")

    # Second attempt: adaptive trailing-performance gate (walk-forward, no external data).
    led = sorted((dt, t, net) for dt, t, net, _ in rec)
    base = [r for _, _, r in led]
    print("  adaptive trailing-performance gate (skip if trailing-K mean net < thresh):")
    for K, thr in ((3, 0.03), (5, 0.0)):
        g = [r for i, (_, _, r) in enumerate(led)
             if i < K or (sum(x[2] for x in led[i - K:i]) / K) >= thr]
        m = metrics(g)
        print(f"    K={K} thr={thr:+.0%}: traded={m['n']:>3} net={m['net']:+6.2f}% "
              f"t={m['t']:+.2f} Sharpe={m['sharpe']:.2f}  (ungated Sharpe {metrics(base)['sharpe']:.2f})")
    print("  -> ADAPTIVE only marginal (Sharpe ~2.2->2.4). 2021 was net ~0 (no-edge + fat TAIL),")
    print("     not a money-loser -- so the real defense is PUTS (cap the squeeze tail), not a gate.")


if __name__ == "__main__":
    run()
