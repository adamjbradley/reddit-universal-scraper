"""Analytics tools. Fetch posts via the API, then run analytics/sentiment.py in-process."""
from analytics.sentiment import analyze_posts_sentiment, extract_keywords as _extract_keywords

from mcp_server.proxy import api_get


def register(mcp):
    @mcp.tool()
    async def analyze_sentiment(
        subreddit: str = "",
        q: str = "",
        author: str = "",
        limit: int = 200,
    ) -> dict:
        """Run sentiment analysis over posts matching the given filters.
        Returns the score distribution (positive/negative/neutral), the average
        score, and a sample of scored posts."""
        posts = await api_get("/posts", {
            "subreddit": subreddit, "q": q, "author": author, "limit": limit,
        })
        scored, counts = analyze_posts_sentiment(posts)
        avg = sum(p.get("sentiment_score", 0) for p in scored) / max(len(scored), 1)
        return {
            "count": len(scored),
            "distribution": counts,
            "avg_score": round(avg, 3),
            "sample": scored[:20],
        }

    @mcp.tool()
    async def extract_keywords(
        subreddit: str = "",
        q: str = "",
        limit: int = 500,
        top_n: int = 50,
    ) -> dict:
        """Extract the most common keywords from the titles and bodies of posts
        matching the given filters."""
        posts = await api_get("/posts", {"subreddit": subreddit, "q": q, "limit": limit})
        texts = [f"{p.get('title', '')} {p.get('selftext', '')}" for p in posts]
        keywords = _extract_keywords(texts, top_n=top_n)
        return {
            "count": len(posts),
            "keywords": [{"word": w, "count": c} for w, c in keywords],
        }
