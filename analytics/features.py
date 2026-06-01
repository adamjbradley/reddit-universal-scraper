"""Point-in-time feature store — the foundation of the signal framework.

Computes, per (ticker, date), the cross-sectional features the strategies read
(velocity, breadth, concentration, sentiment trajectory, author-quality mix, novelty),
plus a per-date aggregate level (market breadth + a Retail Risk-Appetite Index) that
feeds the index/FX macro overlay.

Hard rule: NO LOOKAHEAD. Every feature at date D uses only mentions dated <= D, and all
trailing windows are RANGE frames ending the day BEFORE D (so a spike never inflates its
own baseline). Forward returns / labels live in the backtester, never here.

Built with DuckDB over the SQLite store (read-only attach), written back via the app's
WAL connection so it never contends with the live scheduler.
"""
from datetime import datetime, timedelta

from config import DB_PATH
from export.database import get_connection


def _con():
    """Read-only DuckDB connection with the SQLite store attached as `s`."""
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{str(DB_PATH)}' AS s (TYPE sqlite, READ_ONLY)")
    return con


def _ensure_schema():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS feature_daily (
        ticker TEXT, date TEXT,
        mentions INTEGER, breadth INTEGER, per_author REAL, concentration REAL, hhi REAL,
        sentiment REAL, vel REAL, accel REAL, mentions_z REAL, sent_delta REAL,
        young_frac REAL, gone_frac REAL, suspended_frac REAL, novelty_days INTEGER,
        PRIMARY KEY (ticker, date))""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_feat_date ON feature_daily(date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_feat_ticker ON feature_daily(ticker, date)")
    cur.execute("""CREATE TABLE IF NOT EXISTS aggregate_daily (
        date TEXT PRIMARY KEY,
        total_mentions INTEGER, tickers INTEGER, authors INTEGER,
        mkt_sent REAL, bull_frac REAL, bear_frac REAL,
        young_frac REAL, gone_frac REAL, froth INTEGER,
        rrai_raw REAL, rrai_pct REAL)""")
    conn.commit()
    conn.close()


def build_feature_daily(lookback_days=400, min_total=20):
    """Materialize per-(ticker, date) features for tickers with >= min_total mentions in
    the window. Returns row count written."""
    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    con = _con()
    rows = con.execute(f"""
        WITH ta AS (
            SELECT m.ticker,
                   CAST(substr(m.created_utc, 1, 10) AS DATE) AS d,
                   m.author, m.sentiment_score,
                   a.account_age_days AS age, a.account_status AS st
            FROM s.ticker_mentions m
            LEFT JOIN s.authors a ON a.username = m.author
            WHERE m.created_utc >= '{cutoff}'
              AND m.ticker IN (
                  SELECT ticker FROM s.ticker_mentions
                  WHERE created_utc >= '{cutoff}'
                  GROUP BY ticker HAVING COUNT(*) >= {min_total})
        ),
        authday AS (  -- per author per ticker-day (for concentration + quality mix)
            SELECT ticker, d, author, COUNT(*) AS c,
                   ANY_VALUE(age) AS age, ANY_VALUE(st) AS st
            FROM ta GROUP BY ticker, d, author
        ),
        daily AS (
            SELECT ticker, d,
                   SUM(c) AS mentions,
                   COUNT(*) AS breadth,
                   MAX(c) AS top_author,
                   SUM(c*c) * 1.0 / NULLIF(SUM(c)*SUM(c), 0) AS hhi,
                   SUM(CASE WHEN age IS NOT NULL AND age <= 365 THEN c ELSE 0 END) * 1.0 / SUM(c) AS young_frac,
                   SUM(CASE WHEN st IN ('deleted','suspended') THEN c ELSE 0 END) * 1.0 / SUM(c) AS gone_frac,
                   SUM(CASE WHEN st = 'suspended' THEN c ELSE 0 END) * 1.0 / SUM(c) AS suspended_frac
            FROM authday GROUP BY ticker, d
        ),
        sent AS (SELECT ticker, d, AVG(sentiment_score) AS sentiment FROM ta GROUP BY ticker, d),
        j AS (
            SELECT daily.*, sent.sentiment,
                   MIN(d) OVER (PARTITION BY ticker) AS first_d
            FROM daily JOIN sent USING (ticker, d)
        ),
        feat AS (
            SELECT ticker, d, mentions, breadth, sentiment,
                   young_frac, gone_frac, suspended_frac,
                   mentions * 1.0 / NULLIF(breadth, 0) AS per_author,
                   top_author * 1.0 / NULLIF(mentions, 0) AS concentration,
                   hhi,
                   mentions * 1.0 / NULLIF(AVG(mentions) OVER w7, 0) AS vel,
                   (mentions - AVG(mentions) OVER w14) / NULLIF(STDDEV_POP(mentions) OVER w14, 0) AS mentions_z,
                   sentiment - AVG(sentiment) OVER w7 AS sent_delta,
                   date_diff('day', first_d, d) AS novelty_days
            FROM j
            WINDOW w7  AS (PARTITION BY ticker ORDER BY d
                           RANGE BETWEEN INTERVAL 7 DAY PRECEDING AND INTERVAL 1 DAY PRECEDING),
                   w14 AS (PARTITION BY ticker ORDER BY d
                           RANGE BETWEEN INTERVAL 14 DAY PRECEDING AND INTERVAL 1 DAY PRECEDING)
        )
        SELECT ticker, CAST(d AS VARCHAR) AS date, mentions, breadth,
               ROUND(per_author, 3), ROUND(concentration, 3), ROUND(hhi, 3),
               ROUND(sentiment, 3),
               ROUND(vel, 3),
               ROUND(vel - LAG(vel) OVER (PARTITION BY ticker ORDER BY d), 3) AS accel,
               ROUND(mentions_z, 3), ROUND(sent_delta, 3),
               ROUND(young_frac, 3), ROUND(gone_frac, 3), ROUND(suspended_frac, 3),
               novelty_days
        FROM feat
        ORDER BY ticker, date
    """).fetchall()
    con.close()

    _ensure_schema()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM feature_daily")
    cur.executemany("""INSERT OR REPLACE INTO feature_daily
        (ticker, date, mentions, breadth, per_author, concentration, hhi, sentiment,
         vel, accel, mentions_z, sent_delta, young_frac, gone_frac, suspended_frac, novelty_days)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    conn.commit()
    conn.close()
    return len(rows)


def build_aggregate_daily(lookback_days=400):
    """Materialize the per-date market aggregate + Retail Risk-Appetite Index (RRAI).
    Reads feature_daily for the froth count, so run build_feature_daily first. RRAI
    percentile is a trailing 252-day rank (computed in Python to keep the SQL simple)."""
    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    con = _con()
    # POSTS ONLY: posts are uniformly archive-covered across the whole window, whereas
    # comment coverage is recent-heavy (live scrape) - so a posts-only aggregate is
    # coverage-stationary. All RRAI inputs are RATIOS (coverage-invariant), never raw counts.
    base = con.execute(f"""
        WITH agg AS (
            SELECT CAST(substr(m.created_utc, 1, 10) AS DATE) AS d,
                   COUNT(*) AS total_mentions,
                   COUNT(DISTINCT m.ticker) AS tickers,
                   COUNT(DISTINCT m.author) AS authors,
                   AVG(m.sentiment_score) AS mkt_sent,
                   AVG(CASE WHEN m.sentiment_score > 0.3 THEN 1.0 ELSE 0 END) AS bull_frac,
                   AVG(CASE WHEN m.sentiment_score < 0 THEN 1.0 ELSE 0 END) AS bear_frac,
                   AVG(CASE WHEN a.account_age_days IS NOT NULL AND a.account_age_days <= 365 THEN 1.0 ELSE 0 END) AS young_frac,
                   AVG(CASE WHEN a.account_status IN ('deleted','suspended') THEN 1.0 ELSE 0 END) AS gone_frac
            FROM s.ticker_mentions m
            LEFT JOIN s.authors a ON a.username = m.author
            WHERE m.created_utc >= '{cutoff}' AND m.source_type = 'post'
            GROUP BY 1
        ),
        froth AS (  -- concentrated-ish ticker-days that day = froth proxy (informational)
            SELECT date AS d2, COUNT(*) AS froth
            FROM s.feature_daily
            WHERE per_author >= 2 AND mentions >= 5
            GROUP BY date
        )
        SELECT CAST(agg.d AS VARCHAR) AS date, total_mentions, tickers, authors,
               ROUND(mkt_sent, 4), ROUND(bull_frac, 4), ROUND(bear_frac, 4),
               ROUND(young_frac, 4), ROUND(gone_frac, 4),
               COALESCE(froth.froth, 0) AS froth
        FROM agg LEFT JOIN froth ON froth.d2 = CAST(agg.d AS VARCHAR)
        ORDER BY agg.d
    """).fetchall()
    con.close()

    # rrai_raw: net retail bullishness = bull_frac - bear_frac (posts-only, coverage-robust).
    # A clean euphoria/capitulation gauge; traded CONTRARIAN at extremes, not directionally.
    # (Leverage-language enrichment - calls/puts ratio, margin/YOLO terms - is a TODO.)
    # Components are stored too, so the composite can be re-validated / re-weighted later.
    enriched = []
    for date, tot, tk, au, sent, bull, bear, yf, gf, froth in base:
        rrai_raw = (bull or 0.0) - (bear or 0.0)
        enriched.append([date, tot, tk, au, sent, bull, bear, yf, gf, froth, round(rrai_raw, 4)])

    # rrai_pct: trailing 90-day percentile rank of rrai_raw (share of window <= today).
    # NULL until >= 30 days of history (burn-in) so early days aren't falsely "extreme".
    WIN, MIN_HIST = 90, 30
    out = []
    for i, row in enumerate(enriched):
        window = [enriched[j][10] for j in range(max(0, i - WIN + 1), i + 1)]
        pct = None if len(window) < MIN_HIST else round(
            sum(1 for v in window if v <= row[10]) / len(window), 4)
        out.append(row + [pct])

    _ensure_schema()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM aggregate_daily")
    cur.executemany("""INSERT OR REPLACE INTO aggregate_daily
        (date, total_mentions, tickers, authors, mkt_sent, bull_frac, bear_frac,
         young_frac, gone_frac, froth, rrai_raw, rrai_pct)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", out)
    conn.commit()
    conn.close()
    return len(out)


def build_all(lookback_days=400, min_total=20):
    """Rebuild the whole feature store (cross-sectional then aggregate). Idempotent."""
    n_feat = build_feature_daily(lookback_days=lookback_days, min_total=min_total)
    n_agg = build_aggregate_daily(lookback_days=lookback_days)
    return {"feature_daily_rows": n_feat, "aggregate_daily_rows": n_agg}
