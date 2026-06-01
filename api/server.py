"""
REST API Module - Expose Reddit Scraper data as a REST API
For integration with Metabase, Grafana, DreamFactory, and other tools.

Start with: python api/server.py
Or: uvicorn api.server:app --reload --port 8000
"""
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, List
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from export.database import (
    get_connection, search_posts, search_comments,
    get_subreddit_stats, get_all_subreddits, get_subreddits_summary,
    delete_subreddit, get_job_history, get_job_stats, get_database_info,
    trending_tickers, ticker_sentiment, ticker_timeseries, search_fts
)

# Create FastAPI app
app = FastAPI(
    title="Reddit Scraper API",
    description="REST API for Reddit Scraper data. Use with Metabase, Grafana, or any tool.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Enable CORS for external tools
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for local tools
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- HEALTH & INFO ---

@app.get("/", tags=["Info"])
def root():
    """API root - basic info."""
    return {
        "name": "Reddit Scraper API",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": ["/posts", "/comments", "/subreddits", "/jobs", "/stats"]
    }


@app.get("/health", tags=["Info"])
def health_check():
    """Health check endpoint."""
    try:
        info = get_database_info()
        return {"status": "healthy", "database": info}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


@app.get("/info", tags=["Info"])
def database_info():
    """Get database info and table counts."""
    return get_database_info()


# --- POSTS ---

@app.get("/posts", tags=["Posts"])
def list_posts(
    q: Optional[str] = Query(None, description="Search query"),
    subreddit: Optional[str] = Query(None, description="Filter by subreddit"),
    author: Optional[str] = Query(None, description="Filter by author"),
    min_score: Optional[int] = Query(None, description="Minimum score"),
    post_type: Optional[str] = Query(None, description="Post type filter"),
    limit: int = Query(100, ge=1, le=1000, description="Max results")
):
    """
    Get posts with optional filters.
    
    Use for Grafana dashboards, Metabase queries, or custom integrations.
    """
    return search_posts(
        query=q,
        subreddit=subreddit,
        author=author,
        min_score=min_score,
        post_type=post_type,
        limit=limit
    )


@app.get("/posts/{post_id}", tags=["Posts"])
def get_post(post_id: str):
    """Get a single post by ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM posts WHERE id = ?", (post_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Post not found")
    return dict(row)


# --- COMMENTS ---

@app.get("/comments", tags=["Comments"])
def list_comments(
    q: Optional[str] = Query(None, description="Search in comment body"),
    post_id: Optional[str] = Query(None, description="Filter by post ID"),
    author: Optional[str] = Query(None, description="Filter by author"),
    min_score: Optional[int] = Query(None, description="Minimum score"),
    limit: int = Query(100, ge=1, le=1000, description="Max results")
):
    """Get comments with optional filters."""
    return search_comments(
        query=q,
        post_id=post_id,
        author=author,
        min_score=min_score,
        limit=limit
    )


# --- SUBREDDITS ---

@app.get("/subreddits", tags=["Subreddits"])
def list_subreddits():
    """Get all scraped subreddits with post counts."""
    return get_all_subreddits()


@app.get("/subreddits/summary", tags=["Subreddits"])
def subreddits_summary():
    """Summary of all subreddits we have data for: post/comment counts, the date
    range of posts (how far back), span in days, and last-scraped time, plus totals."""
    return get_subreddits_summary()


@app.get("/subreddits/{subreddit}/stats", tags=["Subreddits"])
def subreddit_stats(subreddit: str):
    """Get detailed statistics for a subreddit."""
    stats = get_subreddit_stats(subreddit)
    if not stats.get('total_posts'):
        raise HTTPException(status_code=404, detail=f"No data for r/{subreddit}")
    return stats


@app.delete("/subreddits/{subreddit}", tags=["Subreddits"])
def remove_subreddit(subreddit: str):
    """Delete all stored data for a subreddit (posts, comments, tracking row).
    Destructive and irreversible. Idempotent - returns zero counts if nothing matched."""
    return delete_subreddit(subreddit)


# --- JOBS ---

@app.get("/jobs", tags=["Jobs"])
def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status"),
    target: Optional[str] = Query(None, description="Filter by target"),
    limit: int = Query(50, ge=1, le=200)
):
    """Get job history."""
    return get_job_history(limit=limit, target=target, status=status)


@app.get("/jobs/stats", tags=["Jobs"])
def job_stats():
    """Get aggregated job statistics."""
    return get_job_stats()


# --- TICKERS & ANALYTICS ---

@app.get("/tickers/trending", tags=["Analytics"])
def tickers_trending(
    window_hours: int = Query(48, ge=1, le=720),
    subreddit: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=200),
):
    """Most-mentioned tickers in the window, with sentiment + velocity vs. prior window."""
    return trending_tickers(window_hours=window_hours, subreddit=subreddit, limit=limit)


@app.get("/tickers/{ticker}/sentiment", tags=["Analytics"])
def ticker_sentiment_endpoint(ticker: str, window_hours: int = Query(168, ge=1, le=2160)):
    """Sentiment summary for a ticker over a window, with per-subreddit breakdown."""
    return ticker_sentiment(ticker, window_hours=window_hours)


@app.get("/tickers/{ticker}/timeseries", tags=["Analytics"])
def ticker_timeseries_endpoint(ticker: str, days: int = Query(30, ge=1, le=365)):
    """Daily mention count + average sentiment for a ticker."""
    return ticker_timeseries(ticker, days=days)


@app.get("/search", tags=["Analytics"])
def fts_search(
    q: str = Query(..., description="Full-text query"),
    kind: str = Query("posts", description="posts | comments"),
    limit: int = Query(50, ge=1, le=500),
):
    """Fast full-text search (FTS5, relevance-ranked)."""
    return {"query": q, "kind": kind, "results": search_fts(q, kind=kind, limit=limit)}


# --- OLAP (DuckDB analytical engine over the SQLite store) ---

@app.get("/analytics/momentum", tags=["Analytics"])
def analytics_momentum(window_days: int = Query(30, ge=2, le=180), top: int = Query(25, ge=1, le=100)):
    """Mention-velocity spike scores (z-score of latest-day mentions vs trailing),
    computed with DuckDB window analytics. High z = abnormal surge of attention."""
    from analytics.olap import ticker_momentum
    return ticker_momentum(window_days=window_days, top=top)


@app.get("/analytics/pump-suspects", tags=["Analytics"])
def analytics_pump_suspects(
    window_days: int = Query(90, ge=1, le=365),
    min_mentions: int = Query(10, ge=1, le=1000),
    limit: int = Query(30, ge=1, le=200),
):
    """Tickers whose mentions concentrate in young and/or now-deleted/suspended accounts -
    the throwaway-pump signature (the one bucket with real excess return in backtests).
    gone_frac = share of mentions from accounts now deleted/suspended; suspended_frac =
    Reddit-banned share; young_frac = accounts <=1yr old. Timing is coarse (gone = current)."""
    from export.database import pump_suspects
    return pump_suspects(window_days=window_days, min_mentions=min_mentions, limit=limit)


@app.get("/signals", tags=["Signals"])
def signals_feed(format: str = Query("json", description="json | mt5"),
                 test: int = Query(0, description="1 = force an active signal (EA testing only)")):
    """Live actionable signals for execution clients. Currently serves the validated RRAI
    capitulation overlay (buy a risk-on basket when retail capitulates AND VIX>=18).
    format=mt5 returns 'STRATEGY,SYMBOL,SIDE,STRENGTH,HORIZON' lines for the MetaTrader EA.
    test=1 forces an active signal so you can verify the EA end-to-end while the market is
    flat - NOT a real trading signal."""
    from analytics.signals import current_signals, as_mt5_lines
    force = test == 1
    if format == "mt5":
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(as_mt5_lines(force=force))
    return current_signals(force=force)


# --- SEMANTIC SEARCH (embeddings) ---

@app.get("/semantic_search", tags=["Semantic"])
def semantic_search_endpoint(q: str = Query(...), k: int = Query(10, ge=1, le=50)):
    """Semantic (meaning-based) search over posts via embeddings."""
    from analytics.embeddings import semantic_search
    return semantic_search(q, k=k)


@app.get("/similar_posts/{post_id}", tags=["Semantic"])
def similar_posts_endpoint(post_id: str, k: int = Query(10, ge=1, le=50)):
    """Posts most semantically similar to the given one (near-duplicates / same theme)."""
    from analytics.embeddings import similar_posts
    return similar_posts(post_id, k=k)


@app.get("/summarize_ticker/{ticker}", tags=["Semantic"])
def summarize_ticker_endpoint(ticker: str, window_days: int = Query(7, ge=1, le=90), k: int = Query(15, ge=1, le=40)):
    """RAG retrieval: the most thesis-relevant recent posts about a ticker (for LLM summarization)."""
    from analytics.embeddings import summarize_ticker
    return summarize_ticker(ticker, window_days=window_days, k=k)


# --- GRAPH ANALYTICS ---

@app.get("/graph/cooccurrence/{ticker}", tags=["Graph"])
def graph_cooccurrence(ticker: str, window_days: int = Query(14, ge=1, le=90), top: int = Query(15, ge=1, le=50)):
    """Tickers most often discussed in the same thread as this one (basket/rotation)."""
    from analytics.graph import ticker_cooccurrence
    return ticker_cooccurrence(ticker, window_days=window_days, top=top)


@app.get("/graph/clusters", tags=["Graph"])
def graph_clusters(window_days: int = Query(7, ge=1, le=90)):
    """Louvain communities of co-mentioned tickers (groups that move together)."""
    from analytics.graph import trending_clusters
    return trending_clusters(window_days=window_days)


@app.get("/graph/coordination", tags=["Graph"])
def graph_coordination(ticker: Optional[str] = Query(None), window_hours: int = Query(72, ge=1, le=720)):
    """Flag pump-like coordination (few authors driving many mentions). Omit ticker to scan all."""
    from analytics.graph import suspected_coordination
    return suspected_coordination(ticker=ticker, window_hours=window_hours)


@app.get("/graph/leadlag/{ticker}", tags=["Graph"])
def graph_leadlag(ticker: str, window_days: int = Query(45, ge=7, le=180), max_lag_days: int = Query(3, ge=1, le=10)):
    """Contagion direction: tickers that tend to spike AFTER (followers) or BEFORE (leaders) this one."""
    from analytics.graph import ticker_leadlag
    return ticker_leadlag(ticker, window_days=window_days, max_lag_days=max_lag_days)


@app.get("/graph/propagation/{ticker}", tags=["Graph"])
def graph_propagation(ticker: str, window_days: int = Query(30, ge=1, le=180)):
    """How a ticker spread across subreddits over time (origin -> viral)."""
    from analytics.graph import ticker_propagation
    return ticker_propagation(ticker, window_days=window_days)


@app.get("/authors/{author}/credibility", tags=["Graph"])
def author_credibility_endpoint(author: str, with_hitrate: bool = Query(True)):
    """Conservative multi-signal author credibility: attributes + behaviour + prescience
    + (if price data) validated hit-rate. Returns score, confidence, and a verdict."""
    from analytics.authors import author_credibility
    return author_credibility(author, with_hitrate=with_hitrate)


@app.get("/graph/influencers", tags=["Graph"])
def graph_influencers(subreddit: Optional[str] = Query(None), window_days: int = Query(14, ge=1, le=90), top: int = Query(20, ge=1, le=100)):
    """PageRank over the reply network — most influential authors."""
    from analytics.graph import influential_authors
    return influential_authors(subreddit=subreddit, window_days=window_days, top=top)


# --- RAW SQL (for advanced users) ---

@app.get("/query", tags=["Advanced"])
def raw_query(
    sql: str = Query(..., description="SQL SELECT query"),
    limit: int = Query(100, ge=1, le=1000)
):
    """
    Execute a raw SQL SELECT query.
    
    ⚠️ Only SELECT queries allowed. Use for custom Grafana/Metabase queries.
    
    Example: /query?sql=SELECT title, score FROM posts ORDER BY score DESC
    """
    # Security: Only allow SELECT
    if not sql.strip().upper().startswith("SELECT"):
        raise HTTPException(status_code=400, detail="Only SELECT queries allowed")
    
    # Add limit if not present
    if "LIMIT" not in sql.upper():
        sql = f"{sql} LIMIT {limit}"
    
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return {"query": sql, "count": len(results), "results": results}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Query error: {e}")


# --- GRAFANA COMPATIBLE ENDPOINTS ---

@app.get("/grafana/search", tags=["Grafana"])
def grafana_search():
    """Grafana SimpleJSON datasource - search endpoint."""
    subs = get_all_subreddits()
    return [s['subreddit'] for s in subs]


@app.post("/grafana/query", tags=["Grafana"])
def grafana_query(body: dict):
    """Grafana SimpleJSON datasource - query endpoint."""
    # Return time series data for Grafana
    results = []
    
    for target in body.get('targets', []):
        subreddit = target.get('target')
        if subreddit:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT date(created_utc) as time, COUNT(*) as value
                FROM posts WHERE subreddit = ?
                GROUP BY date(created_utc)
                ORDER BY time
            """, (subreddit,))
            
            datapoints = [[row['value'], row['time']] for row in cursor.fetchall()]
            conn.close()
            
            results.append({
                "target": subreddit,
                "datapoints": datapoints
            })
    
    return results


# --- CLI ---

if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting Reddit Scraper API...")
    print("   📖 Docs: http://localhost:8000/docs")
    print("   📊 Use with Metabase, Grafana, or any REST client")
    uvicorn.run(app, host="0.0.0.0", port=8000)
