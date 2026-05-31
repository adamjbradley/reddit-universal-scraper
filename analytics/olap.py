"""DuckDB analytical layer over the SQLite system-of-record.

DuckDB reads the existing reddit_scraper.db directly (no migration, no copy) and
runs window-function analytics that SQLite is awkward at — e.g. mention-velocity
z-scores for spike detection. SQLite stays the OLTP/write store; DuckDB is OLAP.
"""
from datetime import datetime, timedelta

from config import DB_PATH


def _con():
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{str(DB_PATH)}' AS s (TYPE sqlite, READ_ONLY)")
    return con


def ticker_momentum(window_days=30, min_total=10, top=25):
    """Per-ticker mention-velocity spike score: z-score of the latest day's mentions
    vs. the trailing daily distribution. High z = abnormal surge of attention."""
    cutoff = (datetime.now() - timedelta(days=window_days)).isoformat()
    con = _con()
    rows = con.execute(f"""
        WITH daily AS (
            SELECT ticker,
                   substr(created_utc, 1, 10) AS d,
                   COUNT(*) AS mentions,
                   AVG(sentiment_score) AS sent
            FROM s.ticker_mentions
            WHERE created_utc >= '{cutoff}'
            GROUP BY ticker, substr(created_utc, 1, 10)
        ),
        maxd AS (SELECT MAX(d) AS md FROM daily),
        stats AS (
            SELECT ticker,
                   SUM(mentions) AS total,
                   AVG(mentions) AS mean_d,
                   STDDEV_POP(mentions) AS sd_d,
                   MAX(CASE WHEN d = (SELECT md FROM maxd) THEN mentions END) AS latest,
                   AVG(sent) AS avg_sent
            FROM daily GROUP BY ticker
        )
        SELECT ticker, total, ROUND(mean_d, 2) AS mean_daily, latest,
               ROUND((latest - mean_d) / NULLIF(sd_d, 0), 2) AS zscore,
               ROUND(avg_sent, 3) AS avg_sentiment
        FROM stats
        WHERE total >= {min_total} AND latest IS NOT NULL
        ORDER BY zscore DESC NULLS LAST
        LIMIT {top}
    """).fetchall()
    con.close()
    cols = ["ticker", "total_mentions", "mean_daily", "latest_day", "zscore", "avg_sentiment"]
    return {"window_days": window_days, "momentum": [dict(zip(cols, r)) for r in rows]}
