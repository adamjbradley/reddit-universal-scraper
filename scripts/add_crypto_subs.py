"""Add the crypto subreddits (live scrape + 1-year archive backfill), SEQUENTIALLY.

Crypto is the most retail-driven corner of the market and the cheapest reliable way to
grow independent pump episodes (per DATA_PLAN.md Phase-1). Each sub is registered the
moment it has posts (get_all_subreddits reads the posts table), after which the scheduler
maintains it automatically. We run one sub at a time on purpose: a single writer stream
avoids the SQLite lock contention that concurrent backfills caused. Idempotent + resumable
(both passes dedupe), so it's safe to re-run if interrupted.

Run detached:
    docker compose exec -T -d mcp sh -c "python scripts/add_crypto_subs.py > /tmp/crypto_add.log 2>&1"
Then tail /tmp/crypto_add.log for progress.
"""
import sys

from main import run_full_history, extract_post_data
from scraper.archive import archive_backfill

SUBS = ["CryptoCurrency", "Bitcoin", "CryptoMoonShots", "SatoshiStreetBets"]


def main():
    for s in SUBS:
        print(f"\n===== {s}: live scrape (register + recent) =====", flush=True)
        try:
            run_full_history(s, 120, is_user=False, download_media_flag=False,
                             scrape_comments_flag=True, refresh=False)
        except Exception as e:
            print(f"  live scrape FAILED for {s}: {e}", flush=True)
        print(f"===== {s}: 1-year archive backfill (history) =====", flush=True)
        try:
            n = archive_backfill(s, extract_post_data, days=365)
            print(f"  archive backfill {s}: {n} posts", flush=True)
        except Exception as e:
            print(f"  archive backfill FAILED for {s}: {e}", flush=True)
    print("\nCRYPTO ADD COMPLETE", flush=True)


if __name__ == "__main__":
    sys.exit(main())
