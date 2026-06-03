"""Author alpha — find Reddit users whose calls PREDATED real moves, with the luck filter built in.

For each ticker mention we score the DIRECTIONAL excess forward return: (stock - SPY) over `horizon`
sessions, signed by the post's sentiment (bullish-then-up OR bearish-then-down both count as a hit).
Per author: mean directional excess, hit rate, t-stat.

The naive "top authors by t-stat" list is a TRAP — with tens of thousands of authors, the extremes are
mostly luck (multiple testing). Two guards:
  1. NULL: how many authors clear |t|>2 vs the ~2.5% expected by chance.
  2. PERSISTENCE (decisive): split each author's calls in half chronologically; does first-half skill
     predict second-half skill? If the P1->P2 correlation is ~0, the leaderboard is noise.

  docker compose exec mcp python -m backtest.author_alpha
"""
import math
import statistics
from collections import defaultdict

from export.database import get_connection
from backtest.engine import _load_prices, _fwd

BOTS = ("[deleted]", "AutoModerator", "None", "")


def _load(min_calls):
    px = _load_prices()
    c = get_connection()
    rows = c.execute(
        """SELECT author, ticker, substr(created_utc,1,10) d, sentiment_score s, subreddit
           FROM ticker_mentions
           WHERE author IS NOT NULL AND author NOT IN ('[deleted]','AutoModerator','None')
             AND author IN (SELECT author FROM ticker_mentions GROUP BY author HAVING COUNT(*)>=?)""",
        (min_calls,)).fetchall()
    c.close()
    return px, rows


def _dir_excess(px, t, d, s, h):
    """Directional market-neutral forward return: (stock-SPY)*sign(sentiment)."""
    direction = 1 if (s or 0) >= 0.1 else (-1 if (s or 0) <= -0.1 else 0)
    if direction == 0:
        return None
    g = _fwd(px, t, d, h)
    m = _fwd(px, "SPY", d, h)
    if g is None or m is None:
        return None
    return (g - m) * direction


def _stats(rs):
    m = statistics.mean(rs)
    sd = statistics.pstdev(rs) or 1e-9
    return m, 100 * sum(1 for x in rs if x > 0) / len(rs), m / (sd / math.sqrt(len(rs)))


def run(horizon=20, min_calls=25):
    px, rows = _load(min_calls)
    byauth = defaultdict(list)
    subs = defaultdict(lambda: defaultdict(int))
    # chronological per author for the persistence split
    for author, t, d, s, sub in sorted(rows, key=lambda r: r[2]):
        r = _dir_excess(px, t, d, s, horizon)
        if r is not None:
            byauth[author].append((d, r))
            subs[author][sub] += 1

    scored = []
    for a, lst in byauth.items():
        if len(lst) < min_calls:
            continue
        rs = [r for _, r in lst]
        m, win, t = _stats(rs)
        topsub = max(subs[a].items(), key=lambda x: x[1])[0]
        scored.append((a, len(rs), m * 100, win, t, topsub))
    scored.sort(key=lambda x: -x[4])
    print(f"=== author alpha: horizon={horizon}d, min_calls={min_calls}, "
          f"directional excess vs SPY ({len(scored)} qualifying authors) ===\n")
    print(f"  top 15 by t-stat (IN-SAMPLE — likely contains luck):")
    print(f"  {'author':22} {'n':>4} {'mean%':>7} {'win%':>5} {'t':>6}  sub")
    for a, n, m, win, t, sub in scored[:15]:
        print(f"  {a:22} {n:>4} {m:>+7.2f} {win:>5.0f} {t:>+6.2f}  r/{sub}")

    # --- NULL: how many clear |t|>2 vs chance ---
    nt2 = sum(1 for x in scored if x[4] > 2)
    print(f"\n  authors with t>2: {nt2} / {len(scored)} = {100*nt2/max(len(scored),1):.1f}% "
          f"(chance ~2.5%) -> {'excess skill present' if nt2/max(len(scored),1) > 0.05 else 'consistent with luck'}")

    # --- PERSISTENCE (decisive): P1 rank -> P2 outcome ---
    pairs = []
    for a, lst in byauth.items():
        if len(lst) < 2 * 10:                      # need >=10 calls per half
            continue
        half = len(lst) // 2
        p1 = statistics.mean(r for _, r in lst[:half])
        p2 = statistics.mean(r for _, r in lst[half:])
        pairs.append((a, p1, p2))
    print(f"\n  PERSISTENCE test ({len(pairs)} authors with >=10 calls per half):")
    if len(pairs) >= 10:
        p1s = [p[1] for p in pairs]; p2s = [p[2] for p in pairs]
        mp1, mp2 = statistics.mean(p1s), statistics.mean(p2s)
        cov = sum((a - mp1) * (b - mp2) for a, b in zip(p1s, p2s)) / len(pairs)
        corr = cov / ((statistics.pstdev(p1s) or 1e-9) * (statistics.pstdev(p2s) or 1e-9))
        pairs.sort(key=lambda x: -x[1])
        k = max(len(pairs) // 5, 5)
        top_p2 = statistics.mean(p[2] for p in pairs[:k]) * 100
        bot_p2 = statistics.mean(p[2] for p in pairs[-k:]) * 100
        print(f"    corr(P1 score, P2 score) = {corr:+.3f}")
        print(f"    P2 return of TOP-quintile-by-P1 authors:    {top_p2:+.2f}%")
        print(f"    P2 return of BOTTOM-quintile-by-P1 authors: {bot_p2:+.2f}%")
        print(f"    -> {'SKILL PERSISTS (tradeable)' if corr > 0.15 and top_p2 > bot_p2 else 'NO persistence — leaderboard is luck'}")
    else:
        print("    too few authors with enough calls in both halves")


if __name__ == "__main__":
    run()
