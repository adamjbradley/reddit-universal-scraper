"""Targeted profiling: fetch account age/status for the authors who drive EQUITY pump events
(per_author>=3, mentions>=10), excluding crypto subs. This is the ~hundreds of authors that
actually gate the deletion short -- far cheaper than sweeping all ~150k discovered authors.
Run in the mcp container; then rebuild feature_daily so young_frac/gone_frac pick up the ages.
"""
from main import _fetch_one_author_age
from export.database import get_connection

CRYPTO = ("CryptoCurrency", "Bitcoin", "CryptoMoonShots", "SatoshiStreetBets")


def main():
    c = get_connection()
    rows = c.execute(f"""
        SELECT DISTINCT m.author FROM ticker_mentions m
        JOIN feature_daily f ON f.ticker=m.ticker AND f.date=substr(m.created_utc,1,10)
        WHERE f.per_author>=3 AND f.mentions>=10
          AND m.subreddit NOT IN ({','.join('?'*len(CRYPTO))})
          AND m.author NOT IN ('[deleted]','AutoModerator')
          AND m.author NOT IN (SELECT username FROM authors WHERE fetched_at IS NOT NULL)
    """, CRYPTO).fetchall()
    c.close()
    auth = [r["author"] for r in rows]
    print(f"targeted profiling: {len(auth)} unprofiled pump authors", flush=True)
    for i, a in enumerate(auth):
        try:
            _fetch_one_author_age(a)
        except Exception:
            pass
        if i % 50 == 0:
            print(f"  {i}/{len(auth)}", flush=True)
    print("PROFILING DONE", flush=True)


if __name__ == "__main__":
    main()
