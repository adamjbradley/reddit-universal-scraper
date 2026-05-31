"""Scrape-trigger tools. Launch detached subprocesses so requests return instantly."""
import subprocess

from mcp_server.proxy import api_get


def _launch_scrape(target, mode, limit, is_user=False, no_media=False,
                   no_comments=False, dry_run=False, max_age_days=None,
                   comment_score_min=0, refresh=False):
    """Build and launch a detached scrape subprocess. Returns a result dict."""
    cmd = ["python", "main.py", target, "--mode", mode, "--limit", str(limit)]
    if is_user:
        cmd.append("--user")
    if no_media:
        cmd.append("--no-media")
    if no_comments:
        cmd.append("--no-comments")
    if dry_run:
        cmd.append("--dry-run")
    if max_age_days:
        cmd.extend(["--max-age-days", str(max_age_days)])
    if comment_score_min:
        cmd.extend(["--comment-score-min", str(comment_score_min)])
    if refresh:
        cmd.append("--refresh")

    # Detached, non-blocking. cwd=/app matches the image layout; inherits the
    # ./data volume so job tracking lands in the same DB the API reads.
    proc = subprocess.Popen(
        cmd,
        cwd="/app",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    return {
        "status": "launched",
        "pid": proc.pid,
        "target": target,
        "mode": mode,
        "command": " ".join(cmd),
        "hint": "Call list_jobs(target=...) to find the job_id and watch its status.",
    }


def register(mcp):
    @mcp.tool()
    async def start_scrape(
        target: str,
        limit: int = 100,
        is_user: bool = False,
        mode: str = "full",
        no_media: bool = False,
        no_comments: bool = False,
        dry_run: bool = False,
        max_age_days: int = 0,
        comment_score_min: int = 0,
        refresh: bool = False,
    ) -> dict:
        """Launch a background scrape of a subreddit (or a user, if is_user=True).

        Returns immediately - the scrape runs as a detached process writing to the
        shared database. Poll list_jobs(target=...) to find the job_id and watch its
        status go from 'running' to 'completed'.

        Args:
            target: Subreddit name (e.g. "python") or username if is_user=True.
            limit: Max posts to fetch (also caps a time-windowed scrape).
            is_user: Treat target as a username instead of a subreddit.
            mode: "full" (posts + comments + media) or "history" (posts only).
            no_media: Skip downloading images/videos.
            no_comments: Skip scraping comments.
            dry_run: Simulate without saving.
            max_age_days: If > 0, only scrape posts newer than N days (e.g. 30 for
                the last month); stops paginating once posts are older.
            comment_score_min: If > 0, only fetch comments for posts scoring >= N
                (keeps the meaningful threads without the full comment cost).
            refresh: Re-fetch posts already stored and update their scores/comments
                (upsert) instead of skipping them. Pair with max_age_days.
        """
        return _launch_scrape(target, mode, limit, is_user=is_user,
                              no_media=no_media, no_comments=no_comments,
                              dry_run=dry_run, max_age_days=max_age_days or None,
                              comment_score_min=comment_score_min, refresh=refresh)

    @mcp.tool()
    async def backfill_comments(subreddit: str = "", lookback_days: int = 180) -> dict:
        """Backfill historical COMMENTS for already-tracked subreddits (one-time, resumable).

        Pulls comments from the arctic-shift archive for stored ticker-mentioning threads
        plus sampled daily megathreads, keeping comments that mention a ticker or clear the
        score floor. Runs in the background; resumable (reruns skip threads already done).
        This is the path for EXISTING subreddits - new subreddits get comments automatically
        via add_subreddit.

        Args:
            subreddit: A single subreddit (without "r/"), or "" / empty to backfill EVERY
                tracked subreddit. Big subs (wallstreetbets) take hours.
            lookback_days: How far back to pull comments. Default 180 (six months).
        """
        cmd = ["python", "main.py", "--archive-comment-backfill",
               "--max-age-days", str(lookback_days)]
        name = subreddit.strip().lstrip("/").removeprefix("r/").strip("/")
        if name:
            cmd.insert(2, name)  # positional target after "main.py"
        proc = subprocess.Popen(
            cmd, cwd="/app", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {
            "status": "launched",
            "pid": proc.pid,
            "scope": name or "ALL tracked subreddits",
            "lookback_days": lookback_days,
            "command": " ".join(cmd),
            "note": "Comment backfill runs in the background (hours for large subs). "
                    "Resumable - safe to rerun. Check trending_tickers / database_info to watch it grow.",
        }

    @mcp.tool()
    async def revalidate_author_status() -> dict:
        """Re-check the lifecycle status (active / suspended / deleted) of stored authors,
        catching accounts that were deleted or suspended AFTER we scraped them. This is the
        longitudinal half of the throwaway-pump signal - an account that was active when it
        hyped a ticker but is gone now. Runs in the background (rate-limited). Feeds
        pump_suspects(). The scheduler also does a small revalidation pass each cycle."""
        proc = subprocess.Popen(
            ["python", "main.py", "--author-status-revalidate"], cwd="/app",
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        return {"status": "launched", "pid": proc.pid,
                "hint": "Status revalidation runs in the background; check pump_suspects() after."}

    @mcp.tool()
    async def enrich_backfill() -> dict:
        """Backfill sentiment + ticker mentions + full-text index over all existing rows
        (one-time; idempotent). Runs in the background. Poll database_info / trending_tickers
        to see it populate."""
        proc = subprocess.Popen(
            ["python", "main.py", "--enrich-backfill"], cwd="/app",
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        return {"status": "launched", "pid": proc.pid,
                "hint": "Enrichment runs in the background; check trending_tickers in a minute."}

    @mcp.tool()
    async def update_all_subreddits(
        max_age_days: int = 2,
        comment_score_min: int = 50,
        limit: int = 200,
        refresh: bool = True,
    ) -> dict:
        """Incrementally update EVERY tracked subreddit in one call.

        Reads the tracked subreddits from the DB and launches a background scrape for
        each (posts within max_age_days, comments for posts scoring >= comment_score_min).
        With refresh=True it also updates scores/comments on posts already stored.
        Returns the list of launched jobs. Poll list_jobs to watch progress.
        """
        subs = await api_get("/subreddits")
        launched = []
        for s in subs:
            name = s.get("subreddit")
            if not name:
                continue
            res = _launch_scrape(name, "full", limit, no_media=True,
                                 max_age_days=max_age_days or None,
                                 comment_score_min=comment_score_min, refresh=refresh)
            launched.append({"subreddit": name, "pid": res["pid"]})
        return {
            "status": "launched",
            "count": len(launched),
            "subreddits": launched,
            "params": {"max_age_days": max_age_days, "comment_score_min": comment_score_min,
                       "limit": limit, "refresh": refresh},
            "hint": "Poll list_jobs() to watch each subreddit's update complete.",
        }

    @mcp.tool()
    async def add_subreddit(subreddit: str, lookback_days: int = 365,
                            comment_lookback_days: int = 180,
                            limit: int = 200, comment_score_min: int = 50) -> dict:
        """Add a new subreddit and backfill a year of history by default.

        Launches a chained background job, in order:
          1. live scrape (recent posts + their comments via the Reddit API),
          2. archive POST backfill back `lookback_days` (default 365) - reaching past
             Reddit's ~1000-post /new cap via the arctic-shift archive,
          3. archive COMMENT backfill back `comment_lookback_days` (default 180) - pulls
             historical comments for ticker-mentioning threads + sampled daily megathreads,
             keeping comments that mention a ticker or clear the score floor.
        The subreddit appears in subreddits_summary once the live scrape completes; post
        then comment history fill in after. Poll list_jobs(target=...) for the live phase.

        Args:
            subreddit: Subreddit name to start tracking (e.g. "python"), without "r/".
            lookback_days: How far back to pull historical POSTS. Default 365 (one year);
                pass larger (e.g. 730) for deeper history.
            comment_lookback_days: How far back to pull historical COMMENTS. Default 180
                (six months); comments are far higher volume, so this is shallower. Set 0
                to skip comment backfill entirely.
            limit: Max posts for the initial live scrape.
            comment_score_min: Only fetch comments for live posts scoring >= this.
        """
        name = subreddit.strip().lstrip("/").removeprefix("r/").strip("/")
        days = lookback_days or 365
        cdays = comment_lookback_days
        # 1) live recent scrape, 2) deep post backfill, 3) historical comment backfill.
        steps = [
            ["python", "main.py", name, "--mode", "full", "--no-media",
             "--limit", str(limit), "--comment-score-min", str(comment_score_min)],
            ["python", "main.py", name, "--archive-backfill", "--max-age-days", str(days)],
        ]
        if cdays and cdays > 0:
            steps.append(["python", "main.py", name, "--archive-comment-backfill",
                          "--max-age-days", str(cdays)])
        chained = " ; ".join(" ".join(s) for s in steps)
        proc = subprocess.Popen(
            ["sh", "-c", chained], cwd="/app",
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        return {
            "status": "launched",
            "pid": proc.pid,
            "subreddit": name,
            "lookback_days": days,
            "comment_lookback_days": cdays,
            "command": chained,
            "note": (f"Live scrape + {days}d post backfill + {cdays}d comment backfill launched. "
                     "Appears in subreddits_summary after the live phase; history fills in after. "
                     "Poll list_jobs(target=...) to watch the live phase."),
        }
