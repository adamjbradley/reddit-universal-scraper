"""
Database module - SQLite storage for scraped data
"""
import sqlite3
from pathlib import Path
from datetime import datetime
import json
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_PATH, DATA_DIR

def get_connection():
    """Get database connection (WAL + tuned pragmas for concurrent read/write)."""
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL lets the always-on scheduler write while the API/MCP read concurrently.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA cache_size=-64000")   # ~64 MB page cache
    conn.execute("PRAGMA mmap_size=268435456")  # 256 MB memory-mapped I/O
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn

def init_database():
    """Initialize database tables."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Posts table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY,
            subreddit TEXT,
            title TEXT,
            author TEXT,
            created_utc TEXT,
            permalink TEXT UNIQUE,
            url TEXT,
            score INTEGER DEFAULT 0,
            upvote_ratio REAL DEFAULT 0,
            num_comments INTEGER DEFAULT 0,
            num_crossposts INTEGER DEFAULT 0,
            selftext TEXT,
            post_type TEXT,
            is_nsfw BOOLEAN DEFAULT 0,
            is_spoiler BOOLEAN DEFAULT 0,
            flair TEXT,
            total_awards INTEGER DEFAULT 0,
            has_media BOOLEAN DEFAULT 0,
            media_downloaded BOOLEAN DEFAULT 0,
            source TEXT,
            scraped_at TEXT DEFAULT CURRENT_TIMESTAMP,
            sentiment_score REAL,
            sentiment_label TEXT
        )
    """)
    
    # Comments table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id TEXT UNIQUE,
            post_id TEXT,
            post_permalink TEXT,
            parent_id TEXT,
            author TEXT,
            body TEXT,
            score INTEGER DEFAULT 0,
            created_utc TEXT,
            depth INTEGER DEFAULT 0,
            is_submitter BOOLEAN DEFAULT 0,
            scraped_at TEXT DEFAULT CURRENT_TIMESTAMP,
            sentiment_score REAL,
            sentiment_label TEXT,
            FOREIGN KEY (post_id) REFERENCES posts(id)
        )
    """)
    
    # Subreddits table (for tracking)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subreddits (
            name TEXT PRIMARY KEY,
            last_scraped TEXT,
            total_posts INTEGER DEFAULT 0,
            total_comments INTEGER DEFAULT 0,
            total_media INTEGER DEFAULT 0
        )
    """)
    
    # Scheduled jobs table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT,
            is_user BOOLEAN DEFAULT 0,
            mode TEXT DEFAULT 'full',
            limit_posts INTEGER DEFAULT 100,
            cron_expression TEXT,
            last_run TEXT,
            next_run TEXT,
            enabled BOOLEAN DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Alerts table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT,
            subreddit TEXT,
            alert_type TEXT DEFAULT 'discord',
            webhook_url TEXT,
            enabled BOOLEAN DEFAULT 1,
            last_triggered TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Job history table for observability
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS job_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT UNIQUE,
            target TEXT,
            is_user BOOLEAN DEFAULT 0,
            mode TEXT,
            status TEXT,
            started_at TEXT,
            completed_at TEXT,
            duration_seconds REAL,
            posts_scraped INTEGER DEFAULT 0,
            comments_scraped INTEGER DEFAULT 0,
            media_downloaded INTEGER DEFAULT 0,
            errors TEXT,
            error_count INTEGER DEFAULT 0,
            dry_run BOOLEAN DEFAULT 0
        )
    """)
    
    # Ticker mentions (one row per ticker per post/comment) - the core trading signal
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ticker_mentions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            source_type TEXT,          -- 'post' | 'comment'
            source_id TEXT,            -- posts.id or comments.comment_id
            subreddit TEXT,
            author TEXT,
            created_utc TEXT,
            score INTEGER DEFAULT 0,
            sentiment_score REAL,
            UNIQUE(ticker, source_type, source_id)
        )
    """)

    # Author account metadata (age/karma/mod status = credibility + pump signals)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS authors (
            username TEXT PRIMARY KEY,
            created_utc TEXT,
            account_age_days REAL,
            comment_karma INTEGER,
            link_karma INTEGER,
            total_karma INTEGER,
            awardee_karma INTEGER,
            is_mod INTEGER,
            is_gold INTEGER,
            is_employee INTEGER,
            has_verified_email INTEGER,
            verified INTEGER,
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # EOD close prices (for forward-return / author hit-rate validation)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            ticker TEXT,
            date TEXT,
            close REAL,
            PRIMARY KEY (ticker, date)
        )
    """)
    cursor.execute("CREATE TABLE IF NOT EXISTS price_meta (ticker TEXT PRIMARY KEY, fetched_at TEXT, ok INTEGER)")

    # Incremental daily rollups for instant time-series
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            subreddit TEXT,
            ticker TEXT,
            date TEXT,
            post_count INTEGER DEFAULT 0,
            comment_count INTEGER DEFAULT 0,
            mention_count INTEGER DEFAULT 0,
            avg_sentiment REAL,
            PRIMARY KEY (subreddit, ticker, date)
        )
    """)

    # Migrate existing authors table: add new columns if missing.
    existing_cols = {r[1] for r in cursor.execute("PRAGMA table_info(authors)")}
    for col in ("total_karma", "awardee_karma", "is_mod", "is_gold", "is_employee",
                "has_verified_email", "verified"):
        if col not in existing_cols:
            cursor.execute(f"ALTER TABLE authors ADD COLUMN {col} INTEGER")
    # Account-lifecycle status (deletion/suspension as a manipulation signal, not for erasure):
    #   account_status: 'active' | 'suspended' (Reddit-banned) | 'deleted' (404) | 'unknown'
    #   status_changed_at stamps the transition we OBSERVED (detection time, not true upstream time).
    for col, decl in (("account_status", "TEXT DEFAULT 'active'"),
                      ("status_checked_at", "TEXT"),
                      ("status_changed_at", "TEXT")):
        if col not in existing_cols:
            cursor.execute(f"ALTER TABLE authors ADD COLUMN {col} {decl}")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_authors_status ON authors(account_status)")

    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_subreddit ON posts(subreddit)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_utc)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_score ON posts(score)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_sub_created ON posts(subreddit, created_utc)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_sentiment ON posts(sentiment_label)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_comments_author ON comments(author)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_mentions_ticker ON ticker_mentions(ticker, created_utc)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_mentions_sub ON ticker_mentions(subreddit, created_utc)")

    # FTS5 full-text search (external-content tables mirror posts/comments)
    cursor.execute("CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(title, selftext, content='posts', content_rowid='rowid')")
    cursor.execute("CREATE VIRTUAL TABLE IF NOT EXISTS comments_fts USING fts5(body, content='comments', content_rowid='id')")
    # INSERT-only triggers index new rows immediately (always safe for external-content
    # FTS5). Updates/deletes are handled by rebuild_fts() (backfill + scheduler cycle) to
    # avoid the 'delete'-of-stale-content corruption that UPDATE/DELETE triggers can cause.
    cursor.executescript("""
        CREATE TRIGGER IF NOT EXISTS posts_ai AFTER INSERT ON posts BEGIN
            INSERT INTO posts_fts(rowid, title, selftext) VALUES (new.rowid, new.title, new.selftext);
        END;
        CREATE TRIGGER IF NOT EXISTS comments_ai AFTER INSERT ON comments BEGIN
            INSERT INTO comments_fts(rowid, body) VALUES (new.id, new.body);
        END;
    """)

    conn.commit()
    conn.close()
    print("✅ Database initialized")

def save_post(post_data, subreddit):
    """Save a single post to database."""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT OR REPLACE INTO posts 
            (id, subreddit, title, author, created_utc, permalink, url, score, 
             upvote_ratio, num_comments, num_crossposts, selftext, post_type,
             is_nsfw, is_spoiler, flair, total_awards, has_media, media_downloaded, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            post_data.get('id'),
            subreddit,
            post_data.get('title'),
            post_data.get('author'),
            post_data.get('created_utc'),
            post_data.get('permalink'),
            post_data.get('url'),
            post_data.get('score', 0),
            post_data.get('upvote_ratio', 0),
            post_data.get('num_comments', 0),
            post_data.get('num_crossposts', 0),
            post_data.get('selftext', ''),
            post_data.get('post_type'),
            post_data.get('is_nsfw', False),
            post_data.get('is_spoiler', False),
            post_data.get('flair', ''),
            post_data.get('total_awards', 0),
            post_data.get('has_media', False),
            post_data.get('media_downloaded', False),
            post_data.get('source', '')
        ))
        conn.commit()
        return True
    except Exception as e:
        print(f"DB Error: {e}")
        return False
    finally:
        conn.close()

def save_posts_batch(posts, subreddit, upsert=False):
    """Save multiple posts efficiently.

    upsert=True refreshes volatile fields (score, comments, awards, etc.) for posts
    that already exist, instead of ignoring them.
    """
    conn = get_connection()
    cursor = conn.cursor()
    saved = 0

    cols = """(id, subreddit, title, author, created_utc, permalink, url, score,
                 upvote_ratio, num_comments, num_crossposts, selftext, post_type,
                 is_nsfw, is_spoiler, flair, total_awards, has_media, media_downloaded, source,
                 sentiment_score, sentiment_label)"""
    values = "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    if upsert:
        sql = f"""INSERT INTO posts {cols} {values}
            ON CONFLICT(id) DO UPDATE SET
                score=excluded.score, upvote_ratio=excluded.upvote_ratio,
                num_comments=excluded.num_comments, num_crossposts=excluded.num_crossposts,
                total_awards=excluded.total_awards, selftext=excluded.selftext,
                flair=excluded.flair, sentiment_score=excluded.sentiment_score,
                sentiment_label=excluded.sentiment_label"""
    else:
        sql = f"INSERT OR IGNORE INTO posts {cols} {values}"

    for post in posts:
        try:
            cursor.execute(sql, (
                post.get('id'),
                subreddit,
                post.get('title'),
                post.get('author'),
                post.get('created_utc'),
                post.get('permalink'),
                post.get('url'),
                post.get('score', 0),
                post.get('upvote_ratio', 0),
                post.get('num_comments', 0),
                post.get('num_crossposts', 0),
                post.get('selftext', ''),
                post.get('post_type'),
                post.get('is_nsfw', False),
                post.get('is_spoiler', False),
                post.get('flair', ''),
                post.get('total_awards', 0),
                post.get('has_media', False),
                post.get('media_downloaded', False),
                post.get('source', ''),
                post.get('sentiment_score'),
                post.get('sentiment_label')
            ))
            if cursor.rowcount > 0:
                saved += 1
        except:
            continue
    
    conn.commit()
    conn.close()
    return saved

def save_comments_batch(comments, post_id, upsert=False):
    """Save multiple comments efficiently.

    upsert=True refreshes score/body for comments that already exist.
    """
    conn = get_connection()
    cursor = conn.cursor()
    saved = 0

    cols = """(comment_id, post_id, post_permalink, parent_id, author, body,
                 score, created_utc, depth, is_submitter, sentiment_score, sentiment_label)"""
    values = "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    if upsert:
        sql = f"""INSERT INTO comments {cols} {values}
            ON CONFLICT(comment_id) DO UPDATE SET
                score=excluded.score, body=excluded.body,
                sentiment_score=excluded.sentiment_score, sentiment_label=excluded.sentiment_label"""
    else:
        sql = f"INSERT OR IGNORE INTO comments {cols} {values}"

    for comment in comments:
        try:
            cursor.execute(sql, (
                comment.get('comment_id'),
                post_id,
                comment.get('post_permalink'),
                comment.get('parent_id'),
                comment.get('author'),
                comment.get('body'),
                comment.get('score', 0),
                comment.get('created_utc'),
                comment.get('depth', 0),
                comment.get('is_submitter', False),
                comment.get('sentiment_score'),
                comment.get('sentiment_label')
            ))
            if cursor.rowcount > 0:
                saved += 1
        except:
            continue
    
    conn.commit()
    conn.close()
    return saved

def search_posts(query=None, subreddit=None, author=None, min_score=None, 
                 start_date=None, end_date=None, post_type=None, limit=100):
    """Search posts with filters."""
    conn = get_connection()
    cursor = conn.cursor()
    
    sql = "SELECT * FROM posts WHERE 1=1"
    params = []
    
    if query:
        sql += " AND (title LIKE ? OR selftext LIKE ?)"
        params.extend([f"%{query}%", f"%{query}%"])
    
    if subreddit:
        sql += " AND subreddit = ?"
        params.append(subreddit)
    
    if author:
        sql += " AND author = ?"
        params.append(author)
    
    if min_score:
        sql += " AND score >= ?"
        params.append(min_score)
    
    if start_date:
        sql += " AND created_utc >= ?"
        params.append(start_date)
    
    if end_date:
        sql += " AND created_utc <= ?"
        params.append(end_date)
    
    if post_type:
        sql += " AND post_type = ?"
        params.append(post_type)
    
    sql += " ORDER BY created_utc DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(sql, params)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results

def search_comments(query=None, post_id=None, author=None, min_score=None, limit=100):
    """Search comments with filters."""
    conn = get_connection()
    cursor = conn.cursor()
    
    sql = "SELECT * FROM comments WHERE 1=1"
    params = []
    
    if query:
        sql += " AND body LIKE ?"
        params.append(f"%{query}%")
    
    if post_id:
        sql += " AND post_id = ?"
        params.append(post_id)
    
    if author:
        sql += " AND author = ?"
        params.append(author)
    
    if min_score:
        sql += " AND score >= ?"
        params.append(min_score)
    
    sql += " ORDER BY score DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(sql, params)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results

def get_subreddit_stats(subreddit):
    """Get statistics for a subreddit."""
    conn = get_connection()
    cursor = conn.cursor()
    
    stats = {}
    
    # Post stats
    cursor.execute("""
        SELECT 
            COUNT(*) as total_posts,
            AVG(score) as avg_score,
            MAX(score) as max_score,
            SUM(num_comments) as total_comments,
            AVG(upvote_ratio) as avg_upvote_ratio
        FROM posts WHERE subreddit = ?
    """, (subreddit,))
    row = cursor.fetchone()
    if row:
        stats.update(dict(row))
    
    # Post type distribution
    cursor.execute("""
        SELECT post_type, COUNT(*) as count 
        FROM posts WHERE subreddit = ? 
        GROUP BY post_type
    """, (subreddit,))
    stats['post_types'] = {row['post_type']: row['count'] for row in cursor.fetchall()}
    
    # Top authors
    cursor.execute("""
        SELECT author, COUNT(*) as post_count, SUM(score) as total_score
        FROM posts WHERE subreddit = ? AND author != '[deleted]'
        GROUP BY author ORDER BY post_count DESC LIMIT 10
    """, (subreddit,))
    stats['top_authors'] = [dict(row) for row in cursor.fetchall()]
    
    # Activity by hour
    cursor.execute("""
        SELECT strftime('%H', created_utc) as hour, COUNT(*) as count
        FROM posts WHERE subreddit = ?
        GROUP BY hour ORDER BY hour
    """, (subreddit,))
    stats['hourly_activity'] = {row['hour']: row['count'] for row in cursor.fetchall()}
    
    conn.close()
    return stats

def get_all_subreddits():
    """Get list of all scraped subreddits."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT subreddit, COUNT(*) as post_count, 
               MAX(created_utc) as latest_post,
               MIN(created_utc) as oldest_post
        FROM posts GROUP BY subreddit ORDER BY post_count DESC
    """)
    
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results

def update_subreddit_tracking(subreddit, posts=0, comments=0, media=0):
    """Upsert the subreddits tracking row (last_scraped + running totals)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO subreddits (name, last_scraped, total_posts, total_comments, total_media)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            last_scraped = excluded.last_scraped,
            total_posts = total_posts + excluded.total_posts,
            total_comments = total_comments + excluded.total_comments,
            total_media = total_media + excluded.total_media
    """, (subreddit, datetime.now().isoformat(), posts, comments, media))
    conn.commit()
    conn.close()

def update_daily_stats():
    """Recompute the daily ticker rollup (ticker x subreddit x date) from ticker_mentions.
    Cheap at current scale; gives instant cross-ticker time-series for dashboards."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM daily_stats")
    cursor.execute("""
        INSERT INTO daily_stats(subreddit, ticker, date, mention_count, avg_sentiment)
        SELECT subreddit, ticker, date(created_utc), COUNT(*), AVG(sentiment_score)
        FROM ticker_mentions
        GROUP BY subreddit, ticker, date(created_utc)
    """)
    conn.commit()
    n = cursor.execute("SELECT COUNT(*) FROM daily_stats").fetchone()[0]
    conn.close()
    return {"rows": n}

def rebuild_fts():
    """Rebuild FTS indexes from the content tables (picks up updates/deletes)."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO posts_fts(posts_fts) VALUES('rebuild')")
        cursor.execute("INSERT INTO comments_fts(comments_fts) VALUES('rebuild')")
        conn.commit()
    finally:
        conn.close()

def search_fts(query, kind='posts', limit=50):
    """Fast full-text search via FTS5 (relevance-ranked). kind: 'posts' | 'comments'."""
    # Quote as a phrase to avoid FTS5 syntax errors from arbitrary input.
    q = '"' + (query or '').replace('"', '') + '"'
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if kind == 'comments':
            cursor.execute("""
                SELECT c.* FROM comments_fts f JOIN comments c ON c.id = f.rowid
                WHERE comments_fts MATCH ? ORDER BY rank LIMIT ?
            """, (q, limit))
        else:
            cursor.execute("""
                SELECT p.* FROM posts_fts f JOIN posts p ON p.rowid = f.rowid
                WHERE posts_fts MATCH ? ORDER BY rank LIMIT ?
            """, (q, limit))
        rows = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()
    return rows

def save_author(username, created_utc, age_days, comment_karma=None, link_karma=None,
                total_karma=None, awardee_karma=None, is_mod=None, is_gold=None,
                is_employee=None, has_verified_email=None, verified=None):
    """Upsert author account metadata (creation, karma, mod/verified status)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO authors (username, created_utc, account_age_days, comment_karma, link_karma,
                             total_karma, awardee_karma, is_mod, is_gold, is_employee,
                             has_verified_email, verified, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(username) DO UPDATE SET
            created_utc=excluded.created_utc, account_age_days=excluded.account_age_days,
            comment_karma=excluded.comment_karma, link_karma=excluded.link_karma,
            total_karma=excluded.total_karma, awardee_karma=excluded.awardee_karma,
            is_mod=excluded.is_mod, is_gold=excluded.is_gold, is_employee=excluded.is_employee,
            has_verified_email=excluded.has_verified_email, verified=excluded.verified,
            fetched_at=CURRENT_TIMESTAMP
    """, (username, created_utc, age_days, comment_karma, link_karma, total_karma,
          awardee_karma, is_mod, is_gold, is_employee, has_verified_email, verified))
    conn.commit()
    conn.close()


def set_author_status(username, status):
    """Record an author's lifecycle status ('active'|'suspended'|'deleted'|'unknown').
    Stamps status_changed_at only on an actual transition; always bumps status_checked_at.
    Upserts, so an already-gone author we never profiled still gets a row."""
    conn = get_connection()
    cur = conn.cursor()
    row = cur.execute("SELECT account_status FROM authors WHERE username=?", (username,)).fetchone()
    if row is None:
        cur.execute("""INSERT INTO authors(username, account_status, status_checked_at, status_changed_at)
                       VALUES(?, ?, datetime('now'), datetime('now'))""", (username, status))
    elif row["account_status"] != status:
        cur.execute("""UPDATE authors SET account_status=?, status_checked_at=datetime('now'),
                       status_changed_at=datetime('now') WHERE username=?""", (status, username))
    else:
        cur.execute("UPDATE authors SET status_checked_at=datetime('now') WHERE username=?", (username,))
    conn.commit()
    conn.close()


def get_authors_needing_status_recheck(limit=500, stale_days=14):
    """Active/never-checked authors due for a status re-check, prioritizing those who
    mentioned tickers most recently (where a pump-then-delete would show up). Skips
    accounts already confirmed deleted/suspended. NULL status_checked_at sorts last in
    DESC, so the recently-active checked-stale authors come first."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT a.username FROM authors a
        LEFT JOIN (SELECT author, MAX(created_utc) last_seen
                   FROM ticker_mentions GROUP BY author) m ON m.author = a.username
        WHERE COALESCE(a.account_status,'active') NOT IN ('deleted','suspended')
          AND (a.status_checked_at IS NULL OR a.status_checked_at < datetime('now', ?))
          AND a.username NOT IN ('[deleted]','AutoModerator')
        ORDER BY m.last_seen DESC
        LIMIT ?
    """, (f'-{int(stale_days)} days', limit))
    res = [r["username"] for r in cur.fetchall()]
    conn.close()
    return res


def pump_suspects(window_days=90, min_mentions=10, limit=30):
    """Tickers whose mentions concentrate in young and/or now-gone accounts - the
    throwaway-pump signature (the only bucket with real excess return in backtests).
    Coarse on timing: 'gone' = CURRENTLY deleted/suspended, observed at recheck time,
    not necessarily right after the pump. The ordering weight is a v1 heuristic."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT m.ticker,
               COUNT(*) AS mentions,
               COUNT(DISTINCT m.author) AS authors,
               ROUND(1.0*COUNT(*)/NULLIF(COUNT(DISTINCT m.author),0), 2) AS per_author,
               ROUND(AVG(CASE WHEN a.account_status IN ('deleted','suspended') THEN 1.0 ELSE 0 END), 3) AS gone_frac,
               ROUND(AVG(CASE WHEN a.account_status='suspended' THEN 1.0 ELSE 0 END), 3) AS suspended_frac,
               ROUND(AVG(CASE WHEN a.account_age_days IS NOT NULL AND a.account_age_days <= 365 THEN 1.0 ELSE 0 END), 3) AS young_frac,
               ROUND(AVG(m.sentiment_score), 3) AS avg_sent
        FROM ticker_mentions m
        LEFT JOIN authors a ON a.username = m.author
        WHERE m.created_utc >= datetime('now', ?)
          AND m.author NOT IN ('[deleted]','AutoModerator')
        GROUP BY m.ticker
        HAVING mentions >= ?
        ORDER BY (per_author * (0.5 + gone_frac + 0.5*young_frac)) DESC
        LIMIT ?
    """, (f'-{int(window_days)} days', min_mentions, limit))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def fresh_pump_suspects(window_hours=48, baseline_days=7, min_recent=4,
                        min_per_author=2.0, burst_mult=3.0, limit=25):
    """FAST pump detector: scans RAW recent mentions to catch FRESH pump-and-dumps within
    HOURS - bypassing feature_daily's min_total>=20 gate + 14d trailing windows that make the
    standard signal ~152 days late. Flags tickers whose last `window_hours` show a concentrated
    burst far above their short trailing baseline (or that are brand-new to the data). Carries
    gone/young author fractions (the manipulation tells). For LIVE catching/fading, not backtest."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        WITH recent AS (
            SELECT m.ticker, COUNT(*) AS rc, COUNT(DISTINCT m.author) AS ra,
                   AVG(m.sentiment_score) AS sent,
                   AVG(CASE WHEN a.account_status IN ('deleted','suspended') THEN 1.0 ELSE 0 END) AS gone_frac,
                   AVG(CASE WHEN a.account_age_days IS NOT NULL AND a.account_age_days<=365 THEN 1.0 ELSE 0 END) AS young_frac
            FROM ticker_mentions m
            LEFT JOIN authors a ON a.username = m.author
            WHERE m.created_utc >= datetime('now', ?)
              AND m.author NOT IN ('[deleted]','AutoModerator')
            GROUP BY m.ticker
        ),
        base AS (
            SELECT ticker, COUNT(*) AS bc FROM ticker_mentions
            WHERE created_utc >= datetime('now', ?) AND created_utc < datetime('now', ?)
            GROUP BY ticker
        )
        SELECT r.ticker, r.rc, r.ra,
               ROUND(1.0*r.rc/NULLIF(r.ra,0), 2) AS per_author,
               COALESCE(b.bc, 0) AS baseline_mentions,
               ROUND(r.sent, 3) AS avg_sent, ROUND(r.gone_frac, 3) AS gone_frac,
               ROUND(r.young_frac, 3) AS young_frac
        FROM recent r LEFT JOIN base b ON b.ticker = r.ticker
        WHERE r.rc >= ? AND 1.0*r.rc/NULLIF(r.ra,0) >= ?
    """, (f'-{window_hours} hours', f'-{baseline_days} days', f'-{window_hours} hours',
          min_recent, min_per_author))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    win_days = window_hours / 24.0
    base_days = max(baseline_days - win_days, 1.0)
    out = []
    for r in rows:
        recent_rate = r["rc"] / win_days
        base_rate = r["baseline_mentions"] / base_days
        fresh = r["baseline_mentions"] == 0
        burst = (recent_rate / base_rate) if base_rate > 0 else None
        if fresh or (burst is not None and burst >= burst_mult):
            r["burst_ratio"] = round(burst, 1) if burst is not None else None  # None = brand-new
            r["fresh"] = fresh
            out.append(r)
    # rank: brand-new first, then concentration x volume
    out.sort(key=lambda x: (x["fresh"], x["per_author"] * x["rc"]), reverse=True)
    return out[:limit]


def get_authors_needing_age(limit=200):
    """Distinct discovered authors (posts + comments) without a recorded age yet."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT author FROM (
            SELECT author FROM posts WHERE author IS NOT NULL
            UNION
            SELECT author FROM comments WHERE author IS NOT NULL
        )
        WHERE author NOT IN ('[deleted]', 'AutoModerator')
          AND author NOT IN (SELECT username FROM authors)
        LIMIT ?
    """, (limit,))
    res = [r["author"] for r in cursor.fetchall()]
    conn.close()
    return res


def get_author_ages(usernames):
    """Return {username: account_age_days} for the given users that we have ages for."""
    if not usernames:
        return {}
    conn = get_connection()
    cursor = conn.cursor()
    qs = ",".join("?" * len(usernames))
    cursor.execute(f"SELECT username, account_age_days FROM authors WHERE username IN ({qs})",
                   list(usernames))
    res = {r["username"]: r["account_age_days"] for r in cursor.fetchall()}
    conn.close()
    return res


def save_ticker_mentions(mentions):
    """Persist ticker-mention rows (one per ticker per post/comment). Idempotent."""
    if not mentions:
        return 0
    conn = get_connection()
    cursor = conn.cursor()
    saved = 0
    for m in mentions:
        try:
            cursor.execute("""
                INSERT INTO ticker_mentions
                (ticker, source_type, source_id, subreddit, author, created_utc, score, sentiment_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, source_type, source_id) DO UPDATE SET
                    score=excluded.score, sentiment_score=excluded.sentiment_score
            """, (m['ticker'], m['source_type'], m['source_id'], m['subreddit'],
                  m.get('author'), m.get('created_utc'), m.get('score', 0), m.get('sentiment_score')))
            if cursor.rowcount > 0:
                saved += 1
        except Exception:
            continue
    conn.commit()
    conn.close()
    return saved

def trending_tickers(window_hours=48, subreddit=None, limit=25):
    """Most-mentioned tickers in the window, with sentiment and velocity vs. the prior window."""
    from datetime import datetime, timedelta
    now = datetime.now()
    cutoff = (now - timedelta(hours=window_hours)).isoformat()
    prev_cutoff = (now - timedelta(hours=2 * window_hours)).isoformat()
    sub_clause = "AND subreddit = ?" if subreddit else ""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT ticker, COUNT(*) mentions, AVG(sentiment_score) avg_sent,
               COUNT(DISTINCT author) authors
        FROM ticker_mentions WHERE created_utc >= ? {sub_clause}
        GROUP BY ticker ORDER BY mentions DESC LIMIT ?
    """, [cutoff] + ([subreddit] if subreddit else []) + [limit])
    cur_rows = {r['ticker']: dict(r) for r in cursor.fetchall()}
    cursor.execute(f"""
        SELECT ticker, COUNT(*) mentions FROM ticker_mentions
        WHERE created_utc >= ? AND created_utc < ? {sub_clause}
        GROUP BY ticker
    """, [prev_cutoff, cutoff] + ([subreddit] if subreddit else []))
    prev = {r['ticker']: r['mentions'] for r in cursor.fetchall()}
    conn.close()
    out = []
    for t, d in cur_rows.items():
        p = prev.get(t, 0)
        out.append({
            'ticker': t, 'mentions': d['mentions'], 'authors': d['authors'],
            'avg_sentiment': round(d['avg_sent'] or 0, 3),
            'prev_mentions': p, 'velocity_change': d['mentions'] - p,
        })
    out.sort(key=lambda x: -x['mentions'])
    return {'window_hours': window_hours, 'subreddit': subreddit or 'all', 'tickers': out}

def ticker_sentiment(ticker, window_hours=168):
    """Sentiment summary for a ticker over a window, with per-subreddit breakdown."""
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(hours=window_hours)).isoformat()
    t = ticker.upper().lstrip('$')
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) mentions, AVG(sentiment_score) avg_sent,
               SUM(CASE WHEN sentiment_score > 0.05 THEN 1 ELSE 0 END) pos,
               SUM(CASE WHEN sentiment_score < -0.05 THEN 1 ELSE 0 END) neg,
               COUNT(DISTINCT author) authors, COUNT(DISTINCT subreddit) subs
        FROM ticker_mentions WHERE ticker = ? AND created_utc >= ?
    """, (t, cutoff))
    r = dict(cursor.fetchone())
    cursor.execute("""
        SELECT subreddit, COUNT(*) m, AVG(sentiment_score) s FROM ticker_mentions
        WHERE ticker = ? AND created_utc >= ? GROUP BY subreddit ORDER BY m DESC
    """, (t, cutoff))
    by_sub = [{'subreddit': x['subreddit'], 'mentions': x['m'], 'avg_sentiment': round(x['s'] or 0, 3)}
              for x in cursor.fetchall()]
    conn.close()
    return {
        'ticker': t, 'window_hours': window_hours, 'mentions': r['mentions'],
        'avg_sentiment': round(r['avg_sent'] or 0, 3), 'positive': r['pos'], 'negative': r['neg'],
        'unique_authors': r['authors'], 'subreddits': r['subs'], 'by_subreddit': by_sub,
    }

def ticker_timeseries(ticker, days=30):
    """Daily mention count + avg sentiment for a ticker."""
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    t = ticker.upper().lstrip('$')
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT date(created_utc) d, COUNT(*) mentions, AVG(sentiment_score) s
        FROM ticker_mentions WHERE ticker = ? AND created_utc >= ?
        GROUP BY date(created_utc) ORDER BY d
    """, (t, cutoff))
    series = [{'date': x['d'], 'mentions': x['mentions'], 'avg_sentiment': round(x['s'] or 0, 3)}
              for x in cursor.fetchall()]
    conn.close()
    return {'ticker': t, 'days': days, 'series': series}

def get_post_permalinks(subreddit=None):
    """Return stored post permalinks (for cross-run dedup), optionally per subreddit."""
    conn = get_connection()
    cursor = conn.cursor()
    if subreddit:
        cursor.execute("SELECT permalink FROM posts WHERE subreddit = ?", (subreddit,))
    else:
        cursor.execute("SELECT permalink FROM posts")
    result = [r['permalink'] for r in cursor.fetchall() if r['permalink']]
    conn.close()
    return result

def get_posts_for_comment_refresh(subreddit, since_days=14):
    """Stored posts in `subreddit` created within `since_days`, with the Reddit
    comment count recorded at last scrape and how many comment rows we actually hold.

    Used by the smart comment-refresh: compare these against Reddit's current counts
    to decide which threads gained comments and need a re-fetch.
    """
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=since_days)).isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.id, p.permalink, p.created_utc,
               p.num_comments AS stored_num_comments,
               (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS stored_rows
        FROM posts p
        WHERE p.subreddit = ? AND p.created_utc >= ?
        ORDER BY p.created_utc DESC
    """, (subreddit, cutoff))
    result = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return result

def update_post_counts(post_id, num_comments, score=None):
    """Update a post's recorded comment count (and optionally score) after a refresh,
    so the next cycle's change-detection compares against current values."""
    conn = get_connection()
    cursor = conn.cursor()
    if score is None:
        cursor.execute("UPDATE posts SET num_comments = ? WHERE id = ?",
                       (num_comments, post_id))
    else:
        cursor.execute("UPDATE posts SET num_comments = ?, score = ? WHERE id = ?",
                       (num_comments, score, post_id))
    conn.commit()
    conn.close()

def get_comments_by_subreddit(subreddit, limit=100000):
    """Return all comments for a subreddit (joined via their parent post)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.* FROM comments c
        JOIN posts p ON c.post_id = p.id
        WHERE p.subreddit = ?
        LIMIT ?
    """, (subreddit, limit))
    result = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return result

def delete_subreddit(subreddit):
    """Delete ALL stored data for a subreddit: posts, comments, ticker mentions, post
    embeddings, daily rollups, the tracking row, and the on-disk folder. Idempotent.
    (Shared data - authors, prices - is left intact.) Returns deleted counts."""
    conn = get_connection()
    cursor = conn.cursor()
    # Delete dependents that reference this sub's posts BEFORE the posts are gone.
    cursor.execute("""DELETE FROM post_embeddings WHERE post_id IN
                      (SELECT id FROM posts WHERE subreddit = ?)""", (subreddit,))
    deleted_embeddings = cursor.rowcount
    cursor.execute("""DELETE FROM comments WHERE post_id IN
                      (SELECT id FROM posts WHERE subreddit = ?)""", (subreddit,))
    deleted_comments = cursor.rowcount
    cursor.execute("DELETE FROM ticker_mentions WHERE subreddit = ?", (subreddit,))
    deleted_mentions = cursor.rowcount
    cursor.execute("DELETE FROM daily_stats WHERE subreddit = ?", (subreddit,))
    cursor.execute("DELETE FROM posts WHERE subreddit = ?", (subreddit,))
    deleted_posts = cursor.rowcount
    cursor.execute("DELETE FROM subreddits WHERE name = ?", (subreddit,))
    deleted_tracking = cursor.rowcount
    conn.commit()
    # Rebuild FTS so deleted posts/comments drop out of search.
    try:
        cursor.execute("INSERT INTO posts_fts(posts_fts) VALUES('rebuild')")
        cursor.execute("INSERT INTO comments_fts(comments_fts) VALUES('rebuild')")
        conn.commit()
    except Exception:
        pass
    conn.close()

    # Remove the on-disk folder (any leftover CSVs + media) for this subreddit.
    import shutil
    folder = DATA_DIR / f"r_{subreddit}"
    folder_removed = folder.exists()
    if folder_removed:
        shutil.rmtree(folder, ignore_errors=True)

    return {
        "subreddit": subreddit,
        "posts_deleted": deleted_posts,
        "comments_deleted": deleted_comments,
        "mentions_deleted": deleted_mentions,
        "embeddings_deleted": deleted_embeddings,
        "tracking_rows_deleted": deleted_tracking,
        "folder_removed": folder_removed,
    }

def get_subreddits_summary():
    """Summarize every subreddit we have data for: post/comment counts, the date
    range of posts (how far back), the span in days, and when it was last scraped.

    Returns a dict with per-subreddit entries plus aggregate totals.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Post counts + date range per subreddit (created_utc is ISO text, so MIN/MAX
    # sort correctly lexicographically).
    cursor.execute("""
        SELECT subreddit,
               COUNT(*) AS post_count,
               MIN(created_utc) AS oldest_post,
               MAX(created_utc) AS latest_post
        FROM posts
        GROUP BY subreddit
    """)
    rows = {r['subreddit']: dict(r) for r in cursor.fetchall()}

    # Comment counts per subreddit (join via the parent post).
    cursor.execute("""
        SELECT p.subreddit AS subreddit, COUNT(*) AS comment_count
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        GROUP BY p.subreddit
    """)
    for r in cursor.fetchall():
        if r['subreddit'] in rows:
            rows[r['subreddit']]['comment_count'] = r['comment_count']

    # last_scraped from the tracking table.
    cursor.execute("SELECT name, last_scraped FROM subreddits")
    last_scraped = {r['name']: r['last_scraped'] for r in cursor.fetchall()}

    conn.close()

    subreddits = []
    for name, data in rows.items():
        oldest = data.get('oldest_post')
        latest = data.get('latest_post')
        span_days = None
        if oldest and latest:
            try:
                d0 = datetime.fromisoformat(str(oldest).replace('Z', '+00:00'))
                d1 = datetime.fromisoformat(str(latest).replace('Z', '+00:00'))
                span_days = round((d1 - d0).total_seconds() / 86400, 1)
            except (ValueError, TypeError):
                span_days = None
        subreddits.append({
            'subreddit': name,
            'post_count': data.get('post_count', 0),
            'comment_count': data.get('comment_count', 0),
            'oldest_post': oldest,
            'latest_post': latest,
            'span_days': span_days,
            'last_scraped': last_scraped.get(name),
        })

    # Most posts first.
    subreddits.sort(key=lambda s: s['post_count'], reverse=True)

    return {
        'count': len(subreddits),
        'total_posts': sum(s['post_count'] for s in subreddits),
        'total_comments': sum(s['comment_count'] for s in subreddits),
        'subreddits': subreddits,
    }

# --- JOB HISTORY FUNCTIONS ---

def start_job_record(target, mode, is_user=False, dry_run=False):
    """
    Start tracking a new scrape job.
    
    Returns:
        job_id: Unique identifier for the job
    """
    import uuid
    
    conn = get_connection()
    cursor = conn.cursor()
    
    job_id = str(uuid.uuid4())[:8]
    started_at = datetime.now().isoformat()
    
    cursor.execute("""
        INSERT INTO job_history (job_id, target, is_user, mode, status, started_at, dry_run)
        VALUES (?, ?, ?, ?, 'running', ?, ?)
    """, (job_id, target, is_user, mode, started_at, dry_run))
    
    conn.commit()
    conn.close()
    
    print(f"📋 Job started: {job_id}")
    return job_id

def complete_job_record(job_id, status, posts=0, comments=0, media=0, errors=None):
    """
    Complete a job record with results.
    
    Args:
        job_id: Job ID from start_job_record
        status: 'completed' or 'failed'
        posts: Number of posts scraped
        comments: Number of comments scraped
        media: Number of media files downloaded
        errors: Error message if failed
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    completed_at = datetime.now().isoformat()
    
    # Calculate duration
    cursor.execute("SELECT started_at FROM job_history WHERE job_id = ?", (job_id,))
    row = cursor.fetchone()
    
    duration = 0
    error_count = 0
    if row:
        started = datetime.fromisoformat(row['started_at'])
        duration = (datetime.now() - started).total_seconds()
    
    if errors:
        error_count = 1
    
    cursor.execute("""
        UPDATE job_history 
        SET status = ?, completed_at = ?, duration_seconds = ?,
            posts_scraped = ?, comments_scraped = ?, media_downloaded = ?,
            errors = ?, error_count = ?
        WHERE job_id = ?
    """, (status, completed_at, duration, posts, comments, media, errors, error_count, job_id))
    
    conn.commit()
    conn.close()
    
    if status == 'completed':
        print(f"✅ Job {job_id} completed: {posts} posts, {comments} comments in {duration:.1f}s")
    else:
        print(f"❌ Job {job_id} failed: {errors}")

def get_job_history(limit=50, target=None, status=None):
    """Get recent job history."""
    conn = get_connection()
    cursor = conn.cursor()
    
    sql = "SELECT * FROM job_history WHERE 1=1"
    params = []
    
    if target:
        sql += " AND target = ?"
        params.append(target)
    
    if status:
        sql += " AND status = ?"
        params.append(status)
    
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(sql, params)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results

def get_job_stats():
    """Get aggregated job statistics."""
    conn = get_connection()
    cursor = conn.cursor()
    
    stats = {}
    
    # Overall counts
    cursor.execute("""
        SELECT 
            COUNT(*) as total_jobs,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
            SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as running,
            AVG(duration_seconds) as avg_duration,
            SUM(posts_scraped) as total_posts,
            SUM(comments_scraped) as total_comments
        FROM job_history
    """)
    row = cursor.fetchone()
    if row:
        stats.update(dict(row))
    
    # Recent jobs
    cursor.execute("""
        SELECT target, status, duration_seconds, posts_scraped, started_at
        FROM job_history ORDER BY started_at DESC LIMIT 10
    """)
    stats['recent_jobs'] = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    return stats

def print_job_history(limit=20):
    """Pretty print job history."""
    jobs = get_job_history(limit)
    
    print("\n📋 Job History")
    print("-" * 80)
    print(f"{'ID':<10} {'Target':<15} {'Status':<10} {'Posts':<8} {'Duration':<10} {'Started':<20}")
    print("-" * 80)
    
    for job in jobs:
        status_icon = "✅" if job['status'] == 'completed' else "❌" if job['status'] == 'failed' else "🔄"
        duration = f"{job['duration_seconds']:.1f}s" if job['duration_seconds'] else "-"
        started = job['started_at'][:19] if job['started_at'] else "-"
        dry = " (dry)" if job['dry_run'] else ""
        
        print(f"{status_icon} {job['job_id']:<8} {job['target']:<15} {job['status']:<10} "
              f"{job['posts_scraped']:<8} {duration:<10} {started}{dry}")
    
    print("-" * 80)
    
    stats = get_job_stats()
    success_rate = (stats['completed'] / stats['total_jobs'] * 100) if stats['total_jobs'] else 0
    print(f"\n📊 Stats: {stats['total_jobs']} jobs | {success_rate:.0f}% success | "
          f"{stats['total_posts'] or 0} posts total")

# --- SQLITE MAINTENANCE FUNCTIONS ---

def enable_auto_vacuum():
    """Enable incremental auto-vacuum on SQLite database."""
    conn = get_connection()
    try:
        conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
        conn.execute("PRAGMA incremental_vacuum")
        conn.commit()
        print("✅ Auto-vacuum enabled")
    finally:
        conn.close()

def vacuum_database():
    """Run VACUUM to optimize and compact the database."""
    conn = get_connection()
    try:
        print("🔧 Running VACUUM...")
        conn.execute("VACUUM")
        print("✅ Database optimized")
    finally:
        conn.close()

def backup_database(backup_path=None):
    """
    Create a backup of the SQLite database.
    
    Args:
        backup_path: Optional custom backup path
    
    Returns:
        Path to the backup file
    """
    import shutil
    
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(exist_ok=True)
    
    if backup_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"reddit_scraper_{timestamp}.db"
    
    shutil.copy2(DB_PATH, backup_path)
    
    # Get file size
    size_mb = Path(backup_path).stat().st_size / (1024 * 1024)
    print(f"✅ Backup created: {backup_path} ({size_mb:.2f} MB)")
    
    return str(backup_path)

def get_database_info():
    """Get database size and table info."""
    info = {}
    
    # File size
    if DB_PATH.exists():
        info['size_mb'] = DB_PATH.stat().st_size / (1024 * 1024)
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Table counts
    tables = ['posts', 'comments', 'job_history', 'alerts', 'subreddits']
    info['tables'] = {}
    
    for table in tables:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            info['tables'][table] = cursor.fetchone()[0]
        except:
            info['tables'][table] = 0
    
    conn.close()
    return info

# Initialize on import
init_database()

