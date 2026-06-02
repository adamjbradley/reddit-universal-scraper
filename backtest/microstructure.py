"""Pump-microstructure R&D — does DEMAND QUALITY (not sentiment level) lead price?

The validated lesson (deletion-gated short): price can't tell organic demand from
manufactured demand going up, but the social graph can. This module tests three
point-in-time, tradeable signatures of that idea:

  (1) Recruitment exhaustion  -- a pump runs on NEW people to recruit. We add a feature
      the store lacks: NEW-author inflow per ticker-day (authors mentioning the ticker
      for the FIRST time). When new blood dries up while mentions stay high, the pump is
      out of fuel = the social analog of declining volume on a rally. (down-leg, leads)
  (2) Mention deceleration    -- accel<0 at high mentions_z = attention climax. (down-leg)
  (3) Sentiment-price divergence -- sentiment still euphoric while price rolls over =
      bagholder distribution. (down-leg, leads)
  (+) Attention-leads-price   -- strong recruitment while price hasn't moved yet = the
      crowd gathering before the move. (UP-leg, the "catch the wave" entry)

Each is tested with a CONTROL (the same pump state WITHOUT the gate), mirroring the
deletion-short control that proved the gate — not pump-selection — does the work. All
features are no-lookahead (trailing windows end the day BEFORE the signal day); forward
returns come from the shared event-study engine. Underpowered + single-regime by nature;
treat as hypotheses, read sign-stability across regimes, not the headline average.
"""
import bisect

from config import DB_PATH
from export.database import get_connection
from backtest.engine import run_event_study, _load_prices


def _rows(lookback_days=400, min_total=20):
    """Per-(ticker,date): feature-store columns + the NEW-author recruitment feature,
    over the pump-candidate universe (>= min_total mentions in the window)."""
    import duckdb
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    con = duckdb.connect()
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{str(DB_PATH)}' AS s (TYPE sqlite, READ_ONLY)")
    rows = con.execute(f"""
        WITH ta AS (
            SELECT m.ticker, CAST(substr(m.created_utc,1,10) AS DATE) AS d, m.author
            FROM s.ticker_mentions m
            WHERE substr(m.created_utc,1,10) >= '{cutoff}'
              AND m.ticker IN (
                  SELECT ticker FROM s.ticker_mentions
                  WHERE substr(created_utc,1,10) >= '{cutoff}'
                  GROUP BY ticker HAVING COUNT(*) >= {min_total})
        ),
        first_seen AS (  -- first day each author touched each ticker (PIT-safe)
            SELECT ticker, author, MIN(d) AS first_d FROM ta GROUP BY ticker, author
        ),
        rec AS (
            SELECT t.ticker, t.d,
                   COUNT(DISTINCT t.author) AS breadth_r,
                   COUNT(DISTINCT CASE WHEN fs.first_d = t.d THEN t.author END) AS new_authors
            FROM ta t JOIN first_seen fs ON fs.ticker = t.ticker AND fs.author = t.author
            GROUP BY t.ticker, t.d
        ),
        fd AS (
            SELECT ticker, CAST(date AS DATE) AS d, mentions, mentions_z, per_author,
                   concentration, sentiment, sent_delta, accel, vel, breadth
            FROM s.feature_daily
        ),
        j AS (
            SELECT fd.*, rec.new_authors,
                   rec.new_authors * 1.0 / NULLIF(fd.breadth, 0) AS new_frac
            FROM fd JOIN rec ON rec.ticker = fd.ticker AND rec.d = fd.d
        ),
        o AS (
            SELECT *,
                   AVG(new_frac)   OVER w AS new_frac_trail,
                   AVG(new_authors) OVER w AS new_auth_trail,
                   (new_authors - AVG(new_authors) OVER w)
                       / NULLIF(STDDEV_POP(new_authors) OVER w, 0) AS new_z
            FROM j
            WINDOW w AS (PARTITION BY ticker ORDER BY d
                         RANGE BETWEEN INTERVAL 14 DAY PRECEDING AND INTERVAL 1 DAY PRECEDING)
        )
        SELECT ticker, CAST(d AS VARCHAR) AS date, mentions, mentions_z, per_author,
               concentration, sentiment, sent_delta, accel, vel, breadth,
               new_authors, new_frac, new_frac_trail, new_auth_trail, new_z
        FROM o ORDER BY ticker, d
    """).fetchall()
    cols = [c[0] for c in con.description]
    con.close()
    return [dict(zip(cols, r)) for r in rows]


def _trailing_ret(px, ticker, date, k):
    """Price return over the k sessions ending ON the signal day (known at signal time)."""
    if ticker not in px:
        return None
    dates, closes = px[ticker]
    i = bisect.bisect_right(dates, date[:10]) - 1   # last session <= signal day
    if i < k or i < 0:
        return None
    p0, p1 = closes[i - k], closes[i]
    return (p1 / p0 - 1) if p0 > 0 else None


def _show(label, sig, px, side_note="", horizons=(10,)):
    if not sig:
        print(f"  {label:<44} (no events)")
        return
    for h in horizons:
        r = run_event_study(label, sig, horizon=h, spread_bps=75, borrow_bps_day=8.0,
                            market_neutral=True, px=px)
        if "note" in r:
            print(f"  {label:<44} h={h:<2} n={r['tradeable']:<4} (too few)")
            continue
        reg = " ".join(f"{p[0]}={p[1]:+.0f}" for p in r["regime"]) or "-"
        gross = r["gross_avg"]
        print(f"  {label:<44} h={h:<2} n={r['tradeable']:<4} gross={gross:+6.2f}%"
              f" net={r['net_avg_pct']:+6.2f}% win={r['win_pct']:4.1f}% t={r['t_stat']:+5.2f} [{reg}]")
    if side_note:
        print(f"       ^ {side_note}")


def main():
    data = _rows()
    px = _load_prices()
    rows = [d for d in data if d["new_frac_trail"] is not None]
    print(f"loaded {len(data)} ticker-days ({len(rows)} with a 14d recruitment baseline), "
          f"{len(px)} priced tickers\n")

    PUMP = lambda d: (d["mentions_z"] or 0) >= 1.0                       # abnormal attention
    CONC = lambda d: (d["per_author"] or 0) >= 2.0 and (d["mentions"] or 0) >= 10  # manufactured-ish

    # BENCHMARK: unconditional drift of each universe (gross = excess vs SPY, pre-cost) ----
    print("=== BENCHMARK: unconditional forward drift (LONG side, gross=excess vs SPY) ===")
    _show("all high-attention (mz>=1) LONG", [(d["ticker"], d["date"], +1) for d in rows if PUMP(d)], px)
    _show("concentrated pump (pa>=2,m>=10) LONG", [(d["ticker"], d["date"], +1) for d in rows if CONC(d)], px,
          "if gross>0 the universe DRIFTS UP -> shorting fights the drift; only a precise filter flips it")

    # The gates, tested BOTH on the broad pump set AND restricted to concentrated pumps -----
    def gate(name, cond, base_pred):
        treat = [(d["ticker"], d["date"], -1) for d in rows if base_pred(d) and cond(d)]
        ctrl  = [(d["ticker"], d["date"], -1) for d in rows if base_pred(d) and not cond(d)]
        _show(f"{name} short (gate ON)", treat, px)
        _show(f"   control (gate OFF, same base)", ctrl, px)

    def exhaust(d):
        return d["new_frac"] is not None and d["new_frac_trail"] and d["new_frac"] < 0.5 * d["new_frac_trail"]
    def decel(d):
        return d["accel"] is not None and d["accel"] < 0
    def diverge5(d):
        tr = _trailing_ret(px, d["ticker"], d["date"], 5)
        return tr is not None and tr < 0

    for base_name, base in (("broad pump (mz>=1)", PUMP), ("CONCENTRATED pump (pa>=2,m>=10)", CONC)):
        print(f"\n=== gates within {base_name} — SHORT (looking for net>0 with sign-stable regimes) ===")
        gate("(1) recruitment-exhaustion", exhaust, base)
        gate("(2) deceleration accel<0", decel, base)
        gate("(3) divergence price<0&sent-high",
             lambda d: diverge5(d) and (d["sentiment"] or -9) >= 0.4, base)
        # stacked: the manufactured subset with ALL three distribution tells
        stacked = [(d["ticker"], d["date"], -1) for d in rows if base(d)
                   and exhaust(d) and decel(d) and diverge5(d)]
        _show("(1+2+3) STACKED short", stacked, px,
              "all three distribution signatures at once")

    # UP-leg: ride the manufactured/organic pump while it's still recruiting + accelerating --
    print("\n=== UP-leg: continuation LONG within concentrated pumps ===")
    ride = [(d["ticker"], d["date"], +1) for d in rows if CONC(d)
            and (d["new_z"] or -9) >= 0.5 and d["accel"] is not None and d["accel"] >= 0]
    _show("recruiting+accelerating LONG", ride, px,
          "ride only while new blood still arriving AND attention still accelerating")


if __name__ == "__main__":
    main()
