"""Data query + raw SQL tools. All proxy the existing FastAPI service at API_BASE_URL."""
from typing import Optional

from mcp_server.proxy import api_get, api_delete


def register(mcp):
    @mcp.tool()
    async def search_posts(
        q: str = "",
        subreddit: str = "",
        author: str = "",
        min_score: Optional[int] = None,
        post_type: str = "",
        limit: int = 100,
    ) -> list:
        """Search scraped Reddit posts with optional filters (text query, subreddit,
        author, minimum score, post type). Returns a list of post records."""
        return await api_get("/posts", {
            "q": q, "subreddit": subreddit, "author": author,
            "min_score": min_score, "post_type": post_type, "limit": limit,
        })

    @mcp.tool()
    async def get_post(post_id: str) -> dict:
        """Get a single scraped post by its ID."""
        return await api_get(f"/posts/{post_id}")

    @mcp.tool()
    async def search_comments(
        q: str = "",
        post_id: str = "",
        author: str = "",
        min_score: Optional[int] = None,
        limit: int = 100,
    ) -> list:
        """Search scraped comments, optionally filtered by text, post ID, author,
        or minimum score."""
        return await api_get("/comments", {
            "q": q, "post_id": post_id, "author": author,
            "min_score": min_score, "limit": limit,
        })

    @mcp.tool()
    async def list_subreddits() -> list:
        """List all scraped subreddits with their post counts and post date range."""
        return await api_get("/subreddits")

    @mcp.tool()
    async def subreddits_summary() -> dict:
        """Summarize which subreddits we have data for: post/comment counts, the
        date range of posts (how far back the data goes), the span in days, and when
        each was last scraped, plus aggregate totals across all subreddits."""
        return await api_get("/subreddits/summary")

    @mcp.tool()
    async def subreddit_stats(subreddit: str) -> dict:
        """Get detailed statistics for a single scraped subreddit (scores, post
        types, top authors, hourly activity)."""
        return await api_get(f"/subreddits/{subreddit}/stats")

    @mcp.tool()
    async def delete_subreddit(subreddit: str) -> dict:
        """Delete ALL stored data for a subreddit: its posts, their comments, and
        the tracking row. Destructive and irreversible. Returns the counts deleted."""
        return await api_delete(f"/subreddits/{subreddit}")

    @mcp.tool()
    async def list_jobs(status: str = "", target: str = "", limit: int = 50) -> list:
        """List scrape job history, optionally filtered by status or target.
        Use this to track jobs launched with start_scrape."""
        return await api_get("/jobs", {"status": status, "target": target, "limit": limit})

    @mcp.tool()
    async def job_stats() -> dict:
        """Get aggregated scrape-job statistics."""
        return await api_get("/jobs/stats")

    @mcp.tool()
    async def database_info() -> dict:
        """Get database size and per-table row counts."""
        return await api_get("/info")

    @mcp.tool()
    async def health() -> dict:
        """Health check of the underlying scraper API and database."""
        return await api_get("/health")

    @mcp.tool()
    async def trending_tickers(window_hours: int = 48, subreddit: str = "", limit: int = 25) -> dict:
        """Most-mentioned stock tickers in the time window across the tracked subreddits,
        with average sentiment and mention-velocity change vs. the prior window.
        The core signal for spotting what retail is piling into."""
        return await api_get("/tickers/trending", {
            "window_hours": window_hours, "subreddit": subreddit, "limit": limit})

    @mcp.tool()
    async def ticker_sentiment(ticker: str, window_hours: int = 168) -> dict:
        """Sentiment summary for one ticker over a window: mention count, average
        sentiment, positive/negative split, unique authors, and per-subreddit breakdown."""
        return await api_get(f"/tickers/{ticker}/sentiment", {"window_hours": window_hours})

    @mcp.tool()
    async def ticker_timeseries(ticker: str, days: int = 30) -> dict:
        """Daily mention count + average sentiment for a ticker over N days (trend/velocity)."""
        return await api_get(f"/tickers/{ticker}/timeseries", {"days": days})

    @mcp.tool()
    async def search(q: str, kind: str = "posts", limit: int = 50) -> dict:
        """Fast full-text search over posts or comments (FTS5, relevance-ranked).
        kind: 'posts' or 'comments'."""
        return await api_get("/search", {"q": q, "kind": kind, "limit": limit})

    @mcp.tool()
    async def ticker_momentum(window_days: int = 30, top: int = 25) -> dict:
        """Mention-velocity spike detector (DuckDB): ranks tickers by the z-score of
        the latest day's mentions vs. their trailing daily distribution. High z-score =
        an abnormal surge of attention forming right now."""
        return await api_get("/analytics/momentum", {"window_days": window_days, "top": top})

    @mcp.tool()
    async def pump_suspects(window_days: int = 90, min_mentions: int = 10, top: int = 30) -> dict:
        """Throwaway-pump screen: tickers whose mentions concentrate in young and/or
        now-deleted/suspended accounts. In backtests this 'concentrated promotion' bucket
        was the ONLY one with real excess return (+6.45%% vs SPY over 5d). Each row gives
        per_author concentration, gone_frac (now deleted/suspended), suspended_frac
        (Reddit-banned), young_frac (<=1yr accounts), and avg sentiment. Higher = more
        pump-like. Timing is coarse: 'gone' means currently gone, not necessarily right
        after the pump (status is observed via periodic revalidation)."""
        return await api_get("/analytics/pump-suspects",
                             {"window_days": window_days, "min_mentions": min_mentions, "limit": top})

    @mcp.tool()
    async def fresh_pumps(window_hours: int = 48, min_recent: int = 4,
                          min_per_author: float = 2.0, top: int = 25) -> dict:
        """FAST pump screen - catches FRESH pump-and-dumps within HOURS (not the ~152-day-late
        feature signal) by scanning raw recent mentions, bypassing the 20-mention gate. Each row:
        recent mentions/authors, per_author concentration, baseline_mentions, burst_ratio
        (recent vs baseline rate; null='brand-new'), gone_frac/young_frac (manipulation tells).
        Small-cap pumps reliably FADE (backtest t=-3.93), so this is primarily an avoid/fade screen."""
        return await api_get("/analytics/fresh-pumps", {
            "window_hours": window_hours, "min_recent": min_recent,
            "min_per_author": min_per_author, "limit": top})

    @mcp.tool()
    async def current_signals() -> dict:
        """Live actionable trading signals. Serves the Phase-0-validated RRAI capitulation
        overlay: when retail sentiment capitulates (RRAI percentile low) AND VIX confirms
        genuine fear (>=18), go LONG risk - AUDJPY (best leg) + index (US500/USTEC) - for
        ~10 days. Returns the current market state (rrai_pct, vix, capitulation_active),
        any active signals with strength, and an informational pump-suspects watchlist
        (NOT actionable - the equity pump book failed friction-aware backtesting)."""
        return await api_get("/signals", {"format": "json"})

    @mcp.tool()
    async def semantic_search(q: str, k: int = 10) -> dict:
        """Meaning-based search over posts (embeddings) — finds topically relevant posts
        even without the exact words. E.g. 'rate cut fears' or 'AI capex bull thesis'."""
        return await api_get("/semantic_search", {"q": q, "k": k})

    @mcp.tool()
    async def similar_posts(post_id: str, k: int = 10) -> dict:
        """Posts most semantically similar to a given post (near-duplicates / same theme)."""
        return await api_get(f"/similar_posts/{post_id}", {"k": k})

    @mcp.tool()
    async def summarize_ticker(ticker: str, window_days: int = 7, k: int = 15) -> dict:
        """RAG retrieval: returns the most thesis-relevant recent posts about a ticker
        (title + body + sentiment) for you to synthesize a bull/bear summary with sources."""
        return await api_get(f"/summarize_ticker/{ticker}", {"window_days": window_days, "k": k})

    @mcp.tool()
    async def ticker_cooccurrence(ticker: str, window_days: int = 14, top: int = 15) -> dict:
        """Tickers most often discussed in the same thread as this one — surfaces
        baskets / sector rotation (e.g. NVDA lights up -> SMCI/AVGO next)."""
        return await api_get(f"/graph/cooccurrence/{ticker}", {"window_days": window_days, "top": top})

    @mcp.tool()
    async def trending_clusters(window_days: int = 7) -> dict:
        """Communities of tickers that are co-mentioned together (move as a group),
        via Louvain community detection on the co-mention graph."""
        return await api_get("/graph/clusters", {"window_days": window_days})

    @mcp.tool()
    async def suspected_coordination(ticker: str = "", window_hours: int = 72) -> dict:
        """Detect pump-like coordination. Omit ticker for a quick scan of all active
        tickers; pass a ticker for the full graph verdict (author concentration, temporal
        burst, duplicate text, dense author-cluster, and new-account fraction)."""
        return await api_get("/graph/coordination", {"ticker": ticker, "window_hours": window_hours})

    @mcp.tool()
    async def ticker_leadlag(ticker: str, window_days: int = 45, max_lag_days: int = 3) -> dict:
        """Contagion direction via lagged mention cross-correlation: 'followers' tend to
        spike AFTER this ticker (front-running candidates); 'leaders' spike before it."""
        return await api_get(f"/graph/leadlag/{ticker}", {"window_days": window_days, "max_lag_days": max_lag_days})

    @mcp.tool()
    async def ticker_propagation(ticker: str, window_days: int = 30) -> dict:
        """How a ticker spread across subreddits over time — origin sub + when each sub
        picked it up. A niche-sub origin later hitting WSB = a play going viral."""
        return await api_get(f"/graph/propagation/{ticker}", {"window_days": window_days})

    @mcp.tool()
    async def author_credibility(author: str) -> dict:
        """Conservative multi-signal credibility verdict for an author: account attributes
        (age, karma, mod, verified), behaviour (ticker/subreddit diversity, activity cadence,
        sentiment balance), an early-mover 'prescience' score, and - where price data exists -
        a validated forward-return hit-rate. Returns a score, confidence, and qualify_in/out
        verdict (act only when confidence is high)."""
        return await api_get(f"/authors/{author}/credibility")

    @mcp.tool()
    async def influential_authors(subreddit: str = "", window_days: int = 14, top: int = 20) -> dict:
        """Most influential authors by PageRank over the reply network (whose threads
        draw the most engagement) — weight signals by source credibility."""
        return await api_get("/graph/influencers", {"subreddit": subreddit, "window_days": window_days, "top": top})

    @mcp.tool()
    async def run_sql(sql: str, limit: int = 100) -> dict:
        """Run a read-only SELECT query against the scraper database.
        Only SELECT statements are permitted. Example:
        SELECT title, score FROM posts ORDER BY score DESC"""
        if not sql.strip().upper().startswith("SELECT"):
            raise ValueError("Only SELECT queries are allowed")
        return await api_get("/query", {"sql": sql, "limit": limit})
