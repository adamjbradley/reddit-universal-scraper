"""One-pass cleanup: remove bare-token false-positive ticker mentions that the FIXED
extractor (analytics/tickers.AMBIGUOUS) no longer recognizes.

Older ticker_mentions rows were created before the AMBIGUOUS soft-stoplist existed, so
common all-caps words leaked in as tickers ("GRESHAM IS LIVE" -> LIVE). We CANNOT blanket-
delete `ticker IN AMBIGUOUS` -- that would also drop legitimate $CASHTAG mentions ($OPEN =
Opendoor, $SI = a real short). Instead we re-run the fixed extractor on each candidate's
SOURCE TEXT and delete only the mentions it no longer finds (i.e. came via a bare token).
Orphan mentions (no surviving source row) can't be re-validated and are KEPT. Idempotent.

  python scripts/purge_ambiguous_mentions.py            # dry run (default) -- counts only
  python scripts/purge_ambiguous_mentions.py --execute  # apply + rebuild derived tables
"""
import sys
from collections import Counter

from export.database import get_connection, update_daily_stats
from analytics.tickers import extract_tickers, AMBIGUOUS


def _candidates(c, ph, amb):
    """(mention_id, ticker, source_text) for every AMBIGUOUS mention with a joinable source."""
    posts = c.execute(f"""
        SELECT m.id, m.ticker, COALESCE(p.title,'')||' '||COALESCE(p.selftext,'') AS txt
        FROM ticker_mentions m JOIN posts p ON p.id = m.source_id
        WHERE m.source_type='post' AND m.ticker IN ({ph})""", amb).fetchall()
    comments = c.execute(f"""
        SELECT m.id, m.ticker, COALESCE(cm.body,'') AS txt
        FROM ticker_mentions m JOIN comments cm ON cm.comment_id = m.source_id
        WHERE m.source_type='comment' AND m.ticker IN ({ph})""", amb).fetchall()
    return posts + comments


def run(execute=False):
    c = get_connection()
    amb = sorted(AMBIGUOUS)
    ph = ",".join("?" * len(amb))
    total = c.execute(f"SELECT COUNT(*) n FROM ticker_mentions WHERE ticker IN ({ph})", amb).fetchone()["n"]
    cands = _candidates(c, ph, amb)

    del_ids, examples = [], []
    del_by, keep_by = Counter(), Counter()
    for row in cands:
        if row["ticker"] in extract_tickers(row["txt"]):
            keep_by[row["ticker"]] += 1                       # survives the fixed extractor -> cashtag, legit
        else:
            del_ids.append(row["id"]); del_by[row["ticker"]] += 1   # bare false positive -> purge
            if len(examples) < 6:
                examples.append((row["ticker"], row["txt"][:70].replace("\n", " ")))

    orphans = total - len(cands)
    print(f"AMBIGUOUS mentions: {total} total | {len(cands)} validatable | {orphans} orphan (kept)")
    print(f"  -> DELETE {len(del_ids)} confirmed bare false positives | KEEP {sum(keep_by.values())} cashtag-sourced")
    print(f"  delete breakdown: {dict(del_by.most_common(25))}")
    if keep_by:
        print(f"  kept ($cashtag) breakdown: {dict(keep_by.most_common(25))}")
    print("  examples to delete:")
    for tk, snip in examples:
        print(f"    {tk:5} <- {snip!r}")

    if not execute:
        print("\n[dry run] re-run with --execute to apply.")
        c.close()
        return

    cur = c.cursor()
    # Reversible: stash the to-be-deleted rows in a backup table before deleting (idempotent
    # append). Restore with: INSERT INTO ticker_mentions SELECT * FROM ticker_mentions_ambiguous_bak;
    cur.execute("CREATE TABLE IF NOT EXISTS ticker_mentions_ambiguous_bak "
                "AS SELECT * FROM ticker_mentions WHERE 0")
    for i in range(0, len(del_ids), 500):
        chunk = del_ids[i:i + 500]
        inb = ",".join("?" * len(chunk))
        cur.execute(f"INSERT INTO ticker_mentions_ambiguous_bak SELECT * FROM ticker_mentions WHERE id IN ({inb})", chunk)
        cur.execute(f"DELETE FROM ticker_mentions WHERE id IN ({inb})", chunk)
    c.commit()
    after = c.execute(f"SELECT COUNT(*) n FROM ticker_mentions WHERE ticker IN ({ph})", amb).fetchone()["n"]
    print(f"\n  deleted {len(del_ids)} rows; AMBIGUOUS mentions remaining (cashtag+orphan): {after}")
    c.close()
    print("  rebuilding daily_stats:", update_daily_stats())
    from analytics.features import build_all
    print("  rebuilding feature store:", build_all())


if __name__ == "__main__":
    run(execute="--execute" in sys.argv)
