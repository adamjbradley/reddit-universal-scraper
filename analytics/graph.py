"""Graph analytics over ticker_mentions + the reply tree.

v2: co-mention is normalized by base rate (lift) and megathreads ("list your picks"
threads with many tickers) are excluded so edges reflect genuine association, not
co-appearance in a daily thread. Adds lead-lag (contagion direction), graph-based
coordination detection, and cross-subreddit propagation.
"""
from collections import defaultdict, Counter
from datetime import datetime, timedelta

import numpy as np
import networkx as nx

from export.database import get_connection

# Threads with more distinct tickers than this are "list your picks" megathreads and
# are excluded from co-mention edges (measured: 84 such threads drove 54% of pairs).
MAX_THREAD_TICKERS = 15


def _thread_rows(since_iso, subreddit=None):
    conn = get_connection()
    cur = conn.cursor()
    sub = "AND m.subreddit = ?" if subreddit else ""
    params = [since_iso] + ([subreddit] if subreddit else [])
    cur.execute(f"""
        SELECT m.ticker AS ticker, m.author AS author,
               CASE WHEN m.source_type='post' THEN m.source_id ELSE c.post_id END AS thread
        FROM ticker_mentions m
        LEFT JOIN comments c ON m.source_type='comment' AND c.comment_id = m.source_id
        WHERE m.created_utc >= ? {sub}
    """, params)
    rows = cur.fetchall()
    conn.close()
    return [(r["thread"], r["ticker"]) for r in rows if r["thread"]]


def _thread_map(since_iso, subreddit=None, max_thread_tickers=MAX_THREAD_TICKERS):
    """thread -> set(tickers), excluding megathreads. Plus per-ticker thread freq + N."""
    by_thread = defaultdict(set)
    for thread, tk in _thread_rows(since_iso, subreddit):
        by_thread[thread].add(tk)
    clean = {t: tks for t, tks in by_thread.items() if len(tks) <= max_thread_tickers}
    freq = defaultdict(int)
    for tks in clean.values():
        for tk in tks:
            freq[tk] += 1
    return clean, freq, len(clean)


def ticker_cooccurrence(ticker, window_days=14, top=15, min_shared=3, min_lift=1.3):
    """Tickers genuinely associated with `ticker`: megathreads excluded, then kept only
    if co-occurrence is above base rate (lift >= min_lift) with enough support
    (shared_threads >= min_shared), ranked by how often they're discussed together."""
    since = (datetime.now() - timedelta(days=window_days)).isoformat()
    t = ticker.upper().lstrip("$")
    tmap, freq, N = _thread_map(since)
    co = defaultdict(int)
    threads_with = 0
    for tks in tmap.values():
        if t in tks:
            threads_with += 1
            for o in tks:
                if o != t:
                    co[o] += 1
    out = []
    for o, c in co.items():
        lift = (c * N) / (freq[t] * freq[o]) if freq[t] and freq[o] else 0
        # support floor removes rare 2-thread coincidences; lift floor removes
        # base-rate co-occurrence (e.g. SPY appears with everything).
        if c >= min_shared and lift >= min_lift:
            out.append({"ticker": o, "shared_threads": c, "lift": round(lift, 2)})
    out.sort(key=lambda x: (-x["shared_threads"], -x["lift"]))
    return {"ticker": t, "window_days": window_days, "threads_with_ticker": threads_with,
            "co_mentioned": out[:top],
            "note": "above-baseline (lift>=%.1f) co-mentions w/ support; megathreads excluded" % min_lift}


def trending_clusters(window_days=7, top_tickers=60, min_edge=3):
    """Louvain communities of tickers, using lift-weighted edges (megathreads excluded,
    only above-base-rate associations kept) so clusters reflect genuine co-movement."""
    since = (datetime.now() - timedelta(days=window_days)).isoformat()
    tmap, freq, N = _thread_map(since)
    keep = {t for t, _ in sorted(freq.items(), key=lambda x: -x[1])[:top_tickers]}
    counts = defaultdict(int)
    for tks in tmap.values():
        ts = sorted(t for t in tks if t in keep)
        for i in range(len(ts)):
            for j in range(i + 1, len(ts)):
                counts[(ts[i], ts[j])] += 1
    g = nx.Graph()
    for (a, b), c in counts.items():
        if c < min_edge:
            continue
        lift = (c * N) / (freq[a] * freq[b]) if freq[a] and freq[b] else 0
        if lift >= 1.0:  # keep only above-base-rate associations
            g.add_edge(a, b, weight=lift)
    if g.number_of_nodes() == 0:
        return {"window_days": window_days, "clusters": []}
    communities = nx.community.louvain_communities(g, weight="weight", seed=42)
    clusters = []
    for comm in communities:
        if len(comm) < 2:
            continue
        members = sorted(comm, key=lambda t: -freq[t])
        clusters.append({"tickers": members, "size": len(members),
                         "total_mentions": sum(freq[t] for t in members)})
    clusters.sort(key=lambda c: -c["total_mentions"])
    return {"window_days": window_days, "clusters": clusters,
            "note": "lift-weighted, megathreads excluded"}


def _daily_series(since_iso):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""SELECT ticker, substr(created_utc,1,10) d, COUNT(*) n
                   FROM ticker_mentions WHERE created_utc >= ?
                   GROUP BY ticker, substr(created_utc,1,10)""", (since_iso,))
    series = defaultdict(dict)
    totals = defaultdict(int)
    dates = set()
    for r in cur.fetchall():
        series[r["ticker"]][r["d"]] = r["n"]
        totals[r["ticker"]] += r["n"]
        dates.add(r["d"])
    conn.close()
    return series, totals, sorted(dates)


def ticker_leadlag(ticker, window_days=45, max_lag_days=3, min_mentions=10, top=12, min_corr=0.5):
    """Contagion direction via lagged cross-correlation of daily mention series.
    followers = tend to spike AFTER this ticker (front-running candidates);
    leaders   = tend to spike BEFORE it."""
    since = (datetime.now() - timedelta(days=window_days)).isoformat()
    t = ticker.upper().lstrip("$")
    series, totals, dates = _daily_series(since)
    if totals.get(t, 0) < min_mentions or len(dates) < 6:
        return {"ticker": t, "window_days": window_days, "note": "insufficient history",
                "followers": [], "leaders": []}
    di = {d: i for i, d in enumerate(dates)}

    def vec(tk):
        v = np.zeros(len(dates))
        for d, n in series[tk].items():
            v[di[d]] = n
        return v

    def lagcorr(x, y, lag):  # corr of x[t] with y[t+lag]
        xa, ya = (x[:-lag], y[lag:]) if lag > 0 else (x, y)
        if len(xa) < 4 or xa.std() == 0 or ya.std() == 0:
            return 0.0
        return float(np.corrcoef(xa, ya)[0, 1])

    a = vec(t)
    followers, leaders = [], []
    for o in totals:
        if o == t or totals[o] < min_mentions:
            continue
        b = vec(o)
        bf = max(((lagcorr(a, b, L), L) for L in range(1, max_lag_days + 1)), key=lambda x: x[0])
        bl = max(((lagcorr(b, a, L), L) for L in range(1, max_lag_days + 1)), key=lambda x: x[0])
        if bf[0] >= min_corr:
            followers.append({"ticker": o, "lag_days": bf[1], "corr": round(bf[0], 2)})
        if bl[0] >= min_corr:
            leaders.append({"ticker": o, "lag_days": bl[1], "corr": round(bl[0], 2)})
    followers.sort(key=lambda x: -x["corr"])
    leaders.sort(key=lambda x: -x["corr"])
    return {"ticker": t, "window_days": window_days, "days_observed": len(dates),
            "followers": followers[:top], "leaders": leaders[:top],
            "note": "daily mention cross-correlation; short series = treat as directional hints"}


def coordination_detail(ticker, window_hours=168, max_rows=3000):
    """Multi-signal coordination score for a ticker: author concentration, temporal
    burst, duplicate text, and density of the author co-mention network."""
    since = (datetime.now() - timedelta(hours=window_hours)).isoformat()
    t = ticker.upper().lstrip("$")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""SELECT author, created_utc, source_type, source_id FROM ticker_mentions
                   WHERE ticker=? AND created_utc>=? ORDER BY created_utc LIMIT ?""",
                (t, since, max_rows))
    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        conn.close()
        return {"ticker": t, "window_hours": window_hours, "mentions": 0, "suspected": False}
    n = len(rows)
    authors = [r["author"] for r in rows if r["author"]]
    na = len(set(authors))
    mpa = n / max(na, 1)
    # temporal burst: largest share of mentions in any single hour
    hours = Counter(r["created_utc"][:13] for r in rows if r["created_utc"])
    burst = (max(hours.values()) / n) if hours else 0
    # duplicate text fraction
    texts = []
    for r in rows:
        if r["source_type"] == "post":
            row = cur.execute("SELECT (title||' '||coalesce(selftext,'')) tx FROM posts WHERE id=?",
                              (r["source_id"],)).fetchone()
        else:
            row = cur.execute("SELECT body tx FROM comments WHERE comment_id=?", (r["source_id"],)).fetchone()
        if row and row["tx"]:
            texts.append(" ".join(row["tx"].lower().split())[:300])
    conn.close()
    tc = Counter(texts)
    dup = (sum(v for v in tc.values() if v > 1) / len(texts)) if texts else 0
    # author co-mention network: edge if two authors mention within 30 min
    parsed = []
    for r in rows:
        if r["author"] and r["created_utc"]:
            try:
                parsed.append((r["author"], datetime.fromisoformat(r["created_utc"])))
            except ValueError:
                pass
    g = nx.Graph()
    for i in range(len(parsed)):
        ai, ti = parsed[i]
        for j in range(i + 1, len(parsed)):
            if (parsed[j][1] - ti).total_seconds() > 1800:
                break
            if parsed[j][0] != ai:
                g.add_edge(ai, parsed[j][0])
    lcc = (max((len(c) for c in nx.connected_components(g)), default=0) / na) if na else 0
    # new-account fraction: fresh accounts pushing a ticker is a classic ring signature
    from export.database import get_author_ages
    ages = get_author_ages(set(authors))
    known = [v for v in ages.values() if v is not None]
    new_frac = (sum(1 for v in known if v < 30) / len(known)) if known else None
    score, flags = 0, []
    if mpa >= 5:
        score += 1; flags.append("few authors / many mentions")
    if burst >= 0.5 and n >= 10:
        score += 1; flags.append("temporal burst")
    if dup >= 0.2:
        score += 1; flags.append("duplicate text")
    if lcc >= 0.5 and na >= 4:
        score += 1; flags.append("dense author cluster")
    if new_frac is not None and new_frac >= 0.4 and na >= 4:
        score += 1; flags.append("many fresh accounts")
    return {"ticker": t, "window_hours": window_hours, "mentions": n, "unique_authors": na,
            "mentions_per_author": round(mpa, 1), "burst_fraction": round(burst, 2),
            "duplicate_text_fraction": round(dup, 2), "author_cluster_fraction": round(lcc, 2),
            "new_account_fraction": (round(new_frac, 2) if new_frac is not None else None),
            "authors_with_age": len(known),
            "coordination_score": score, "flags": flags, "suspected": score >= 2}


def suspected_coordination(ticker=None, window_hours=72, min_mentions=20):
    """Scan all active tickers (or one) for pump-like author concentration. For a deep,
    graph-based verdict on a specific ticker, use coordination_detail()."""
    if ticker:
        d = coordination_detail(ticker, window_hours=window_hours)
        return d
    since = (datetime.now() - timedelta(hours=window_hours)).isoformat()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""SELECT ticker, COUNT(*) n, COUNT(DISTINCT author) a
                   FROM ticker_mentions WHERE created_utc>=?
                   GROUP BY ticker HAVING n >= ? ORDER BY n DESC""", (since, min_mentions))
    rows = cur.fetchall()
    conn.close()
    flagged = []
    for r in rows:
        ratio = r["n"] / max(r["a"], 1)
        if ratio >= 5 and r["a"] <= max(5, r["n"] * 0.2):
            flagged.append({"ticker": r["ticker"], "mentions": r["n"],
                            "unique_authors": r["a"], "mentions_per_author": round(ratio, 1)})
    flagged.sort(key=lambda x: -x["mentions_per_author"])
    return {"window_hours": window_hours, "flagged": flagged,
            "note": "quick scan; call suspected_coordination(ticker=...) for the full graph verdict"}


def ticker_propagation(ticker, window_days=30):
    """How a ticker's mentions spread across subreddits over time. Origin in a niche sub
    that later appears in a big sub (e.g. WSB) = a play 'going viral'."""
    since = (datetime.now() - timedelta(days=window_days)).isoformat()
    t = ticker.upper().lstrip("$")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""SELECT subreddit, MIN(created_utc) first_seen, COUNT(*) mentions
                   FROM ticker_mentions WHERE ticker=? AND created_utc>=?
                   GROUP BY subreddit ORDER BY first_seen""", (t, since))
    subs = [dict(r) for r in cur.fetchall()]
    conn.close()
    if not subs:
        return {"ticker": t, "window_days": window_days, "subreddits": []}
    origin_dt = datetime.fromisoformat(subs[0]["first_seen"])
    spread = []
    for s in subs:
        days = (datetime.fromisoformat(s["first_seen"]) - origin_dt).total_seconds() / 86400
        spread.append({"subreddit": s["subreddit"], "first_seen": s["first_seen"][:16],
                       "mentions": s["mentions"], "days_after_origin": round(days, 1)})
    return {"ticker": t, "window_days": window_days,
            "origin_subreddit": subs[0]["subreddit"], "subreddit_count": len(subs),
            "spread": spread}


def influential_authors(subreddit=None, window_days=14, top=20):
    """PageRank over the reply network (who replies to whom) within a subreddit."""
    since = (datetime.now() - timedelta(days=window_days)).isoformat()
    conn = get_connection()
    cur = conn.cursor()
    sub = "AND p.subreddit = ?" if subreddit else ""
    params = [since] + ([subreddit] if subreddit else [])
    cur.execute(f"""
        SELECT c.author AS src,
               CASE WHEN c.parent_id LIKE 't3\\_%' ESCAPE '\\' THEN p.author
                    ELSE pc.author END AS dst
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        LEFT JOIN comments pc ON pc.comment_id = REPLACE(c.parent_id, 't1_', '')
        WHERE c.created_utc >= ? {sub}
    """, params)
    g = nx.DiGraph()
    for r in cur.fetchall():
        src, dst = r["src"], r["dst"]
        if src and dst and src != dst and dst not in ("[deleted]", None) and src != "[deleted]":
            g.add_edge(src, dst, weight=g.get_edge_data(src, dst, {}).get("weight", 0) + 1)
    conn.close()
    if g.number_of_nodes() == 0:
        return {"subreddit": subreddit or "all", "window_days": window_days, "authors": []}
    pr = nx.pagerank(g, weight="weight")
    ranked = sorted(pr.items(), key=lambda x: -x[1])[:top]
    return {"subreddit": subreddit or "all", "window_days": window_days,
            "authors": [{"author": a, "score": round(s, 5), "replies_received": g.in_degree(a)}
                        for a, s in ranked]}
