"""Author credibility scoring - a conservative, multi-signal composite.

No single signal qualifies an author in/out (all are gameable/noisy). We combine
account attributes, behaviour over our own data (diversity, cadence, sentiment balance),
an early-mover "prescience" score, and - when price data exists - a validated hit-rate.
Output is a score in [-1, 1] PLUS a confidence, and a verdict only when confident.
"""
import bisect
import statistics
from datetime import datetime

from export.database import get_connection


def _author_attrs(author):
    conn = get_connection()
    r = conn.execute("SELECT * FROM authors WHERE username=?", (author,)).fetchone()
    conn.close()
    return dict(r) if r else {}


def _prescience(mentions):
    """Mean 'earliness' (0..1) of the author's first mention of each ticker vs. the crowd."""
    first = {}
    for m in mentions:
        tk, ts = m["ticker"], m["created_utc"]
        if ts and (tk not in first or ts < first[tk]):
            first[tk] = ts
    if not first:
        return None
    conn = get_connection()
    cur = conn.cursor()
    scores = []
    for tk, ts0 in first.items():
        rows = cur.execute("SELECT created_utc FROM ticker_mentions WHERE ticker=? ORDER BY created_utc", (tk,)).fetchall()
        series = [r["created_utc"] for r in rows]
        if len(series) < 5:
            continue
        rank = bisect.bisect_left(series, ts0)
        scores.append(1 - rank / len(series))  # earlier = higher
    conn.close()
    return round(sum(scores) / len(scores), 3) if scores else None


def _hit_rate(mentions, horizon=5):
    from analytics.prices import forward_return
    rets = []
    for m in mentions:
        r = forward_return(m["ticker"], m["created_utc"], horizon)
        if r is not None:
            rets.append(r)
    if not rets:
        return {"scored": 0, "avg_forward_return": None, "win_rate": None, "horizon_days": horizon}
    return {"scored": len(rets), "avg_forward_return": round(statistics.mean(rets), 4),
            "win_rate": round(sum(1 for r in rets if r > 0) / len(rets), 3), "horizon_days": horizon}


def author_credibility(author, with_hitrate=True):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT ticker, subreddit, created_utc, sentiment_score FROM ticker_mentions WHERE author=?", (author,))
    ms = [dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT MIN(created_utc) a, MAX(created_utc) b, COUNT(*) n FROM (
                     SELECT created_utc FROM posts WHERE author=?
                     UNION ALL SELECT created_utc FROM comments WHERE author=?)""", (author, author))
    act = dict(cur.fetchone())
    conn.close()

    attr = _author_attrs(author)
    n = len(ms)
    tickers = {m["ticker"] for m in ms}
    subs = {m["subreddit"] for m in ms if m["subreddit"]}
    sents = [m["sentiment_score"] for m in ms if m["sentiment_score"] is not None]
    sent_std = statistics.pstdev(sents) if len(sents) > 1 else 0
    sent_mean = statistics.mean(sents) if sents else 0
    span_days = None
    if act.get("a") and act.get("b"):
        try:
            span_days = (datetime.fromisoformat(act["b"]) - datetime.fromisoformat(act["a"])).total_seconds() / 86400
        except ValueError:
            pass
    age = attr.get("account_age_days")
    karma = attr.get("total_karma") or ((attr.get("comment_karma") or 0) + (attr.get("link_karma") or 0))
    prescience = _prescience(ms)
    hit = _hit_rate(ms) if with_hitrate else None

    out_flags, in_flags = [], []
    if age is not None and age < 30: out_flags.append("account <30d")
    if karma is not None and karma < 50: out_flags.append("very low karma")
    if len(tickers) == 1 and n >= 5: out_flags.append("single-ticker")
    if len(subs) == 1 and n >= 5: out_flags.append("single-subreddit")
    if span_days is not None and span_days < 2 and n >= 5: out_flags.append("burst activity")
    if sent_std < 0.05 and sent_mean > 0.3 and n >= 5: out_flags.append("uniformly bullish")
    if attr.get("is_mod"): in_flags.append("moderator")
    if age is not None and age > 730: in_flags.append("account >2y")
    if karma and karma > 10000: in_flags.append("high karma")
    if prescience is not None and prescience >= 0.6 and n >= 5: in_flags.append("early mover")
    if len(tickers) >= 8: in_flags.append("diverse coverage")
    if hit and hit["avg_forward_return"] is not None and hit["scored"] >= 5:
        if hit["avg_forward_return"] > 0.02:
            in_flags.append("positive track record")
        elif hit["avg_forward_return"] < -0.02:
            out_flags.append("negative track record")  # actual losses override good proxies

    # Weighted composite - validated outcome and behaviour weighted above static attributes.
    w_in = {"positive track record": .35, "early mover": .25, "moderator": .15,
            "account >2y": .10, "high karma": .10, "diverse coverage": .10}
    w_out = {"negative track record": .40, "burst activity": .30, "single-ticker": .25,
             "uniformly bullish": .20, "single-subreddit": .15, "account <30d": .15,
             "very low karma": .10}
    score = sum(w_in.get(f, 0) for f in in_flags) - sum(w_out.get(f, 0) for f in out_flags)
    score = max(-1.0, min(1.0, round(score, 3)))

    # Confidence: more mentions + known attributes + validated track record => more sure.
    conf = min(1.0, (min(n, 20) / 20) * 0.5
               + (0.3 if attr.get("fetched_at") else 0)
               + (0.2 if hit and hit["scored"] >= 5 else 0))
    conf = round(conf, 2)
    verdict = ("qualify_in" if score >= 0.4 and conf >= 0.5
               else "qualify_out" if score <= -0.4 and conf >= 0.4
               else "neutral")

    return {
        "author": author, "score": score, "confidence": conf, "verdict": verdict,
        "in_flags": in_flags, "out_flags": out_flags,
        "signals": {
            "account_age_days": age, "total_karma": karma, "is_mod": bool(attr.get("is_mod")),
            "has_verified_email": bool(attr.get("has_verified_email")),
            "mentions": n, "distinct_tickers": len(tickers), "distinct_subreddits": len(subs),
            "activity_span_days": round(span_days, 1) if span_days is not None else None,
            "sentiment_mean": round(sent_mean, 3), "sentiment_std": round(sent_std, 3),
            "prescience": prescience, "hit_rate": hit,
        },
        "note": "conservative composite; act on verdict only when confidence is high",
    }
