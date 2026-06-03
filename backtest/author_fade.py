"""Fade-the-bottom backtest — trade AGAINST consistently-wrong Reddit authors, walk-forward PIT.

The persistence test (author_alpha.py) showed bottom-quintile authors predate -9.14% directional
excess. This operationalises it as a tradeable, point-in-time signal:

  Walk forward through every call. Classify an author as BAD only from their MATURED past calls
  (calls whose 20-day outcome was already known at the decision date — a `mature_days` calendar
  buffer prevents lookahead). When a BAD author (trailing mean directional excess < badthr over
  >=K matured calls) makes a new call, FADE it (take the opposite side), market-neutral, after costs.

Guards: compare fade(BAD authors) vs fade(ALL calls) — does author quality add value beyond a
generic "fade retail" trade? — plus a regime split.

  docker compose exec mcp python -m backtest.author_fade
"""
import math
import datetime
import statistics
from collections import defaultdict

from backtest.author_alpha import _load, _dir_excess
from backtest.engine import _load_prices

H, COST = 20, 0.0155          # 20d hold; ~75bps round-trip spread + 80bps borrow (fades are mostly shorts)
REGIME = {"2021": "2021 mania", "2022": "2022 bear", "2023": "2023", "2024": "2024",
          "2025": "2025-26 now", "2026": "2025-26 now"}


def _d(s):
    return datetime.date(int(s[:4]), int(s[5:7]), int(s[8:10]))


def _stats(rs):
    if len(rs) < 2:
        return None
    m = statistics.mean(rs)
    sd = statistics.pstdev(rs) or 1e-9
    return len(rs), m * 100, 100 * sum(1 for x in rs if x > 0) / len(rs), m / (sd / math.sqrt(len(rs)))


def run(K=10, badthr=0.0, goodthr=0.02, mature_days=30):
    px, rows = _load(K)
    enriched = []
    for author, t, d, s, sub in sorted(rows, key=lambda r: r[2]):
        de = _dir_excess(px, t, d, s, H)
        if de is not None:
            enriched.append((author, d, de))

    hist = defaultdict(list)                      # author -> [(date, dir_excess)]
    fade_bad, fade_all, follow_good = [], [], []
    fade_bad_reg = defaultdict(list)
    for author, d, de in enriched:
        cutoff = _d(d) - datetime.timedelta(days=mature_days)
        matured = [x for dd, x in hist[author] if _d(dd) <= cutoff]
        if len(matured) >= K:
            fade_all.append(-de - COST)           # generic fade baseline (any matured author)
            tm = statistics.mean(matured)
            if tm < badthr:
                fade_bad.append(-de - COST)
                fade_bad_reg[REGIME.get(d[:4], "?")].append(-de - COST)
            elif tm > goodthr:
                follow_good.append(de - COST)
        hist[author].append((d, de))

    print(f"=== fade-the-bottom (walk-forward PIT, K={K} matured calls, {mature_days}d buffer, "
          f"hold {H}d, {COST:.1%} cost) ===\n")
    for lbl, rs in (("FADE bad authors (trailing<0)", fade_bad),
                    ("FADE all matured authors (baseline)", fade_all),
                    ("FOLLOW good authors (trailing>2%)", follow_good)):
        s = _stats(rs)
        if s:
            print(f"  {lbl:38}: n={s[0]:>5} net={s[1]:+6.2f}% win={s[2]:>3.0f}% t={s[3]:+.2f}")
    print(f"\n  -> author quality adds value if FADE-bad net > FADE-all net "
          f"({_stats(fade_bad)[1]:+.2f}% vs {_stats(fade_all)[1]:+.2f}%)")

    print("\n  FADE bad authors by regime:")
    for rg in ("2021 mania", "2022 bear", "2023", "2024", "2025-26 now"):
        s = _stats(fade_bad_reg.get(rg, []))
        if s:
            print(f"    {rg:12}: n={s[0]:>4} net={s[1]:+6.2f}% win={s[2]:>3.0f}% t={s[3]:+.2f}")


if __name__ == "__main__":
    run()
