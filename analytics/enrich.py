"""Enrichment - the single choke point applied to scraped posts/comments.

Adds sentiment_score/sentiment_label to each dict (in place) and returns the
ticker-mention rows to persist. Called from the scrape pipeline and the backfill.
"""
from analytics.sentiment_engine import score as _sentiment
from analytics.tickers import extract_tickers


def _mention(ticker, source_type, source_id, subreddit, author, created_utc, score_val, sent):
    return {
        "ticker": ticker,
        "source_type": source_type,
        "source_id": source_id,
        "subreddit": subreddit,
        "author": author,
        "created_utc": created_utc,
        "score": score_val or 0,
        "sentiment_score": sent,
    }


def enrich_posts(posts, subreddit):
    """Add sentiment to each post; return list of ticker_mention rows."""
    mentions = []
    for p in posts:
        text = f"{p.get('title', '')} {p.get('selftext', '')}"
        s, label = _sentiment(text)
        p["sentiment_score"] = s
        p["sentiment_label"] = label
        for t in extract_tickers(text):
            mentions.append(_mention(t, "post", p.get("id"), subreddit,
                                     p.get("author"), p.get("created_utc"), p.get("score"), s))
    return mentions


def enrich_comments(comments, subreddit):
    """Add sentiment to each comment; return list of ticker_mention rows."""
    mentions = []
    for c in comments:
        text = c.get("body", "")
        s, label = _sentiment(text)
        c["sentiment_score"] = s
        c["sentiment_label"] = label
        for t in extract_tickers(text):
            mentions.append(_mention(t, "comment", c.get("comment_id"), subreddit,
                                     c.get("author"), c.get("created_utc"), c.get("score"), s))
    return mentions


def enrich_backfill(batch=1000):
    """One-time pass: enrich existing rows lacking sentiment, populate ticker_mentions,
    and rebuild the FTS indexes. Idempotent and resumable (filters on NULL sentiment)."""
    from export.database import get_connection, save_ticker_mentions
    conn = get_connection()
    cur = conn.cursor()
    total_p = total_c = total_m = 0

    # Posts
    while True:
        cur.execute("""SELECT id, subreddit, title, selftext, author, created_utc, score
                       FROM posts WHERE sentiment_score IS NULL LIMIT ?""", (batch,))
        rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            break
        mentions = []
        for p in rows:
            text = f"{p.get('title', '')} {p.get('selftext', '')}"
            s, label = _sentiment(text)
            cur.execute("UPDATE posts SET sentiment_score=?, sentiment_label=? WHERE id=?",
                        (s, label, p['id']))
            for t in extract_tickers(text):
                mentions.append(_mention(t, "post", p['id'], p['subreddit'],
                                         p['author'], p['created_utc'], p['score'], s))
        conn.commit()
        save_ticker_mentions(mentions)
        total_p += len(rows); total_m += len(mentions)
        print(f"  posts enriched: {total_p} (+{len(mentions)} mentions)")

    # Comments (subreddit via parent post)
    while True:
        cur.execute("""SELECT c.comment_id, c.body, c.author, c.created_utc, c.score, p.subreddit
                       FROM comments c LEFT JOIN posts p ON c.post_id = p.id
                       WHERE c.sentiment_score IS NULL LIMIT ?""", (batch,))
        rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            break
        mentions = []
        for c in rows:
            text = c.get('body', '')
            s, label = _sentiment(text)
            cur.execute("UPDATE comments SET sentiment_score=?, sentiment_label=? WHERE comment_id=?",
                        (s, label, c['comment_id']))
            for t in extract_tickers(text):
                mentions.append(_mention(t, "comment", c['comment_id'], c.get('subreddit'),
                                         c['author'], c['created_utc'], c['score'], s))
        conn.commit()
        save_ticker_mentions(mentions)
        total_c += len(rows); total_m += len(mentions)
        print(f"  comments enriched: {total_c} (+{len(mentions)} mentions)")

    # Rebuild full-text indexes from the content tables
    try:
        cur.execute("INSERT INTO posts_fts(posts_fts) VALUES('rebuild')")
        cur.execute("INSERT INTO comments_fts(comments_fts) VALUES('rebuild')")
        conn.commit()
    except Exception as e:
        print(f"  FTS rebuild warning: {e}")
    conn.close()
    result = {"posts_enriched": total_p, "comments_enriched": total_c, "mentions": total_m}
    print(f"✅ Enrich backfill complete: {result}")
    return result
