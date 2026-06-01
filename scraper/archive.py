"""Historical backfill via arctic-shift (Pushshift successor).

Reaches past Reddit's ~1000-post /new cap by querying an archive with time ranges.
Posts only (the ticker/sentiment signal source); deduped + enriched + stored like a
normal scrape. extract_fn is passed in to avoid a circular import with main.
"""
import time
import datetime

import requests

from export.database import (get_connection, get_post_permalinks,
                             save_posts_batch, save_comments_batch, save_ticker_mentions)

API = "https://arctic-shift.photon-reddit.com/api/posts/search"
COMMENT_API = "https://arctic-shift.photon-reddit.com/api/comments/search"
UA = {"User-Agent": "Mozilla/5.0 (compatible; reddit-scraper/1.0)"}


def _fetch(subreddit, after, before, limit=100):
    try:
        r = requests.get(API, params={"subreddit": subreddit, "after": str(after),
                                       "before": str(before), "limit": limit, "sort": "desc"},
                         headers=UA, timeout=45)
        if r.status_code == 429:
            time.sleep(5)
            return _fetch(subreddit, after, before, limit)
        return r.json().get("data", []) or []
    except Exception:
        return []


def archive_backfill(subreddit, extract_fn, days=180, dry_run=False, max_pages=3000):
    """Page the archive DEEPER than what we already have: start at the oldest stored post
    for this sub and go back to `days` ago, enriching + storing new posts. Starting from
    the oldest-stored point (not 'now') avoids re-paging tens of thousands of known posts."""
    from analytics.enrich import enrich_posts
    now = int(time.time())
    after = now - days * 86400
    conn = get_connection()
    row = conn.execute("SELECT MIN(created_utc) m FROM posts WHERE subreddit=?", (subreddit,)).fetchone()
    conn.close()
    before = now
    if row and row["m"]:
        try:
            before = int(datetime.datetime.fromisoformat(row["m"]).timestamp())
        except (ValueError, TypeError):
            before = now
    seen = set(get_post_permalinks(subreddit))
    total = pages = 0
    while before > after and pages < max_pages:
        pages += 1
        batch = _fetch(subreddit, after, before, 100)
        if not batch:
            break
        posts = []
        for p in batch:
            if not p.get("permalink") and p.get("id"):
                p["permalink"] = f"/r/{subreddit}/comments/{p['id']}/"
            post = extract_fn(p)
            if not post.get("permalink") or post["permalink"] in seen:
                continue
            seen.add(post["permalink"])
            posts.append(post)
        if posts and not dry_run:
            try:
                mentions = enrich_posts(posts, subreddit)
                save_posts_batch(posts, subreddit)
                save_ticker_mentions(mentions)
                total += len(posts)
            except Exception as e:
                print(f"   ⚠️ save failed: {e}")
        oldest = min(int(p.get("created_utc", before)) for p in batch)
        if oldest >= before:
            break  # no progress (boundary duplicates) -> done
        before = oldest - 1  # strictly page older; short pages are normal, keep going
        time.sleep(0.4)
        if pages % 10 == 0:
            print(f"   📜 r/{subreddit}: +{total} (back to {datetime.datetime.utcfromtimestamp(oldest).date()})")
    print(f"   📜 r/{subreddit}: {total} historical posts added (last {days}d)")
    return total


_AGG_COLS = """(date, total_mentions, tickers, authors, mkt_sent, bull_frac, bear_frac,
                young_frac, gone_frac, froth, rrai_raw, rrai_pct)"""
_AGG_DDL = """(date TEXT PRIMARY KEY, total_mentions INTEGER, tickers INTEGER, authors INTEGER,
               mkt_sent REAL, bull_frac REAL, bear_frac REAL, young_frac REAL, gone_frac REAL,
               froth INTEGER, rrai_raw REAL, rrai_pct REAL)"""


def _flush_agg(rows, table="aggregate_daily"):
    if not rows:
        return
    conn = get_connection()
    conn.execute(f"CREATE TABLE IF NOT EXISTS {table} {_AGG_DDL}")
    conn.executemany(f"INSERT OR IGNORE INTO {table} {_AGG_COLS} VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def archive_aggregate_backfill(subreddits, start_date, end_date, posts_per_day=100, sleep=0.35,
                               post_level=False, table="aggregate_daily"):
    """Extend a retail-sentiment history WITHOUT storing raw posts (sidesteps the firehose
    size/speed problem). For each day in [start, end), fetch up to `posts_per_day` posts per
    sub and write ONE aggregate row (bull/bear fractions are sample-invariant).

    post_level=False: count only ticker-mentioning posts (the US RRAI). post_level=True:
    count EVERY post's sentiment (for non-US subs whose tickers our US symbol list won't
    match - e.g. ASX). `table` lets a parallel series (e.g. au_aggregate_daily) be built.
    Resumable: days already present are skipped. Returns days written.
    """
    from analytics.sentiment_engine import score as _sentiment
    from analytics.tickers import extract_tickers

    conn = get_connection()
    conn.execute(f"CREATE TABLE IF NOT EXISTS {table} {_AGG_DDL}")
    conn.commit()
    have = {r["date"] for r in conn.execute(
        f"SELECT date FROM {table} WHERE total_mentions IS NOT NULL").fetchall()}
    conn.close()

    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    day, written, buf = start, 0, []
    while day < end:
        ds = day.isoformat()
        if ds in have:
            day += datetime.timedelta(days=1)
            continue
        a0 = int(datetime.datetime(day.year, day.month, day.day,
                                   tzinfo=datetime.timezone.utc).timestamp())
        sents, authors = [], set()
        for sub in subreddits:
            for p in _fetch(sub, a0, a0 + 86400 - 1, posts_per_day):
                text = f"{p.get('title', '')} {p.get('selftext', '')}"
                if post_level:
                    s, _ = _sentiment(text)
                    if p.get("author"):
                        authors.add(p["author"])
                    sents.append(s)                  # every post = one observation
                else:
                    tickers = extract_tickers(text)
                    if not tickers:
                        continue
                    s, _ = _sentiment(text)
                    if p.get("author"):
                        authors.add(p["author"])
                    sents.extend([s] * len(tickers))  # one mention per ticker
            time.sleep(sleep)
        n = len(sents)
        if n:
            bull = sum(1 for s in sents if s > 0.3) / n
            bear = sum(1 for s in sents if s < 0) / n
            buf.append((ds, n, None, len(authors), round(sum(sents) / n, 4),
                        round(bull, 4), round(bear, 4), None, None, 0, round(bull - bear, 4), None))
        else:
            buf.append((ds, 0, None, len(authors), None, None, None, None, None, 0, None, None))
        written += 1
        if written % 60 == 0:
            _flush_agg(buf, table); buf = []
            print(f"   📈 {table}: {written} days (at {ds}, last n={n})")
        day += datetime.timedelta(days=1)
    _flush_agg(buf, table)
    print(f"   📈 {table} backfill: {written} days written ({start_date}..{end_date})")
    return written


def _fetch_comments(link_id, before, limit=100):
    try:
        r = requests.get(COMMENT_API, params={"link_id": link_id, "before": str(before),
                                               "limit": limit, "sort": "desc"},
                         headers=UA, timeout=45)
        if r.status_code == 429:
            time.sleep(5)
            return _fetch_comments(link_id, before, limit)
        return r.json().get("data", []) or []
    except Exception:
        return []


def archive_comment_backfill(subreddit, days=180, mega_threshold=500, mega_page_cap=30,
                             score_floor=3, mega_score_floor=5, dry_run=False, max_threads=None):
    """Backfill historical COMMENTS for stored posts via arctic-shift (last `days`).

    Compromise design (the firehose is intractable - WSB alone is ~12M comments/yr and the
    archive has no server-side score filter): we only fetch comments for posts that already
    mention a ticker, PLUS megathreads (daily-discussion threads) sampled to the newest
    ~mega_page_cap*100 comments. A comment is STORED only if it mentions a ticker OR clears
    the score floor (3 for normal threads, 5 for megathreads), dropping the ~84%% score<=1
    junk tail. Resumable: each processed post is logged so reruns skip it. Returns kept count.
    """
    from analytics.enrich import enrich_comments
    from analytics.tickers import extract_tickers
    now = int(time.time())
    cutoff_iso = datetime.datetime.utcfromtimestamp(now - days * 86400).isoformat()

    conn = get_connection()
    conn.execute("""CREATE TABLE IF NOT EXISTS comment_backfill_log(
                      post_id TEXT PRIMARY KEY, subreddit TEXT, kept INTEGER, done_at TEXT)""")
    conn.commit()
    # COLLATE NOCASE: subreddits are stored with Reddit's canonical casing (e.g.
    # 'Daytrading'); a lowercase arg must still match, or we'd silently fetch nothing.
    posts = conn.execute(
        """SELECT id, permalink, num_comments FROM posts
           WHERE subreddit=? COLLATE NOCASE AND created_utc >= ?
             AND id NOT IN (SELECT post_id FROM comment_backfill_log)
           ORDER BY created_utc DESC""", (subreddit, cutoff_iso)).fetchall()
    ticker_posts = {r["source_id"] for r in conn.execute(
        """SELECT DISTINCT source_id FROM ticker_mentions
           WHERE subreddit=? COLLATE NOCASE AND source_type='post'""", (subreddit,)).fetchall()}

    def _mark(pid, kept):
        conn.execute("""INSERT OR REPLACE INTO comment_backfill_log(post_id, subreddit, kept, done_at)
                        VALUES(?,?,?,?)""", (pid, subreddit, kept, datetime.datetime.now().isoformat()))
        conn.commit()

    total_posts = total_comments = 0
    for i, p in enumerate(posts):
        if max_threads and total_posts >= max_threads:
            break
        pid, permalink = p["id"], p["permalink"]
        nc = p["num_comments"] or 0
        is_mega = nc >= mega_threshold
        # Only ticker threads + megathreads are worth fetching.
        if not is_mega and pid not in ticker_posts:
            _mark(pid, 0)
            continue
        floor = mega_score_floor if is_mega else score_floor
        page_cap = mega_page_cap if is_mega else 10000
        link_id = "t3_" + pid
        before, pages, kept = now, 0, []
        while pages < page_cap:
            batch = _fetch_comments(link_id, before, 100)
            if not batch:
                break
            pages += 1
            for c in batch:
                body = c.get("body") or ""
                if body in ("[removed]", "[deleted]", ""):
                    continue
                score = c.get("score") or 0
                if score >= floor or extract_tickers(body):
                    kept.append({
                        "comment_id": c.get("id"),
                        "post_permalink": permalink,
                        "parent_id": c.get("parent_id"),
                        "author": c.get("author"),
                        "body": body,
                        "score": score,
                        "created_utc": datetime.datetime.utcfromtimestamp(
                            int(c.get("created_utc", 0))).isoformat(),
                        "depth": 0,
                        "is_submitter": c.get("is_submitter", False),
                    })
            oldest = min(int(x.get("created_utc", before)) for x in batch)
            if oldest >= before:
                break
            before = oldest - 1
            time.sleep(0.4)
        if kept and not dry_run:
            try:
                mentions = enrich_comments(kept, subreddit)
                save_comments_batch(kept, pid)
                save_ticker_mentions(mentions)
                total_comments += len(kept)
            except Exception as e:
                print(f"   ⚠️ comment save failed for {pid}: {e}")
        _mark(pid, len(kept))
        total_posts += 1
        if total_posts % 25 == 0:
            print(f"   💬 r/{subreddit}: {total_comments} kept from {total_posts} threads")
    conn.close()
    print(f"   💬 r/{subreddit}: {total_comments} historical comments from {total_posts} threads (last {days}d)")
    return total_comments
