"""Columnar research store — a native DuckDB snapshot of the hot analytical tables.

SQLite stays the operational/ingest store (the scheduler writes it). This is READ-ONLY derived
data: backtests and drills scan it columnar (predicate pushdown, zonemap pruning, no per-run
SQLite re-read) instead of re-loading + re-aggregating SQLite every experiment. Matters once the
deep backfill pushes ticker_mentions/prices into the millions. Rebuild after each backfill:

  docker compose exec mcp python -m backtest.store          # (re)build the store
"""
import duckdb

from config import DB_PATH

STORE_PATH = DB_PATH.parent / "research.duckdb"
# the tables every drill touches; ordered by (ticker,date) where present for scan locality
HOT = ["ticker_mentions", "prices", "feature_daily", "short_volume", "authors", "mt5_ohlc"]


def exists():
    return STORE_PATH.exists()


def build(tables=HOT):
    """Snapshot `tables` from SQLite into the native DuckDB store, returning row counts."""
    if STORE_PATH.exists():
        STORE_PATH.unlink()                       # full rebuild (cheap; derived data)
    con = duckdb.connect(str(STORE_PATH))
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{str(DB_PATH)}' AS s (TYPE sqlite, READ_ONLY)")
    out = {}
    for t in tables:
        try:
            cols = [d[0] for d in con.execute(f"SELECT * FROM s.{t} LIMIT 0").description]
        except Exception:
            continue                              # table absent -> skip
        order = "ORDER BY ticker, date" if ("ticker" in cols and "date" in cols) else ""
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM s.{t} {order}")
        out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    con.execute("DETACH s")
    con.close()
    return out


def connect():
    """Read-only DuckDB connection to the research store (raises if not built yet)."""
    if not STORE_PATH.exists():
        raise FileNotFoundError(f"research store not built — run `python -m backtest.store` first ({STORE_PATH})")
    return duckdb.connect(str(STORE_PATH), read_only=True)


if __name__ == "__main__":
    print(f"building research store -> {STORE_PATH}")
    counts = build()
    print("row counts:", counts)
