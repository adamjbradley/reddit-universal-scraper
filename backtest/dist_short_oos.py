"""Forward out-of-sample log for distribution_short — the one gate it hasn't faced.

Every other test on distribution_short is in-sample: the whole event set lives in a single
~12-month scraped window, so multi-regime validation is impossible looking backward. The only
honest test left is FORWARD: freeze the spec today, then record every fresh signal and its
realized 10-day outcome as the scheduler scrapes new data. Signals dated on/after FREEZE_DATE
are genuine OOS — the spec could not have been fit to them because they hadn't happened yet.

Mechanics (idempotent — safe to run daily, e.g. from the scheduler):
  1. recompute the frozen-spec signal set from the live feature store,
  2. upsert each (ticker, signal_date) into dist_short_oos, stamping is_oos by FREEZE_DATE,
  3. an OPEN signal (forward 10-session window not yet complete) closes automatically once
     prices catch up; its realized market-neutral net is then frozen in.
Report: in-sample baseline vs the accumulating OOS record, plus the currently-open live signals.

  docker compose exec mcp python -m backtest.dist_short_oos          # update + report
"""
import bisect
import math
import statistics
from datetime import datetime

from export.database import get_connection
from backtest.engine import _net, _load_prices
from backtest.microstructure import _rows, _trailing_ret

# --- FROZEN SPEC (do not tune after the freeze — that would re-contaminate the OOS set) ---
FREEZE_DATE = "2026-06-03"
HORIZON, SPREAD_BPS, BORROW_BPS = 10, 75, 8.0
# UNIVERSE: manufactured-pump distribution is a MICRO-CAP phenomenon — Reddit moves price only
# where liquidity is thin. Excluding the off-thesis categories (a priori, by what the names ARE,
# not by their returns) DOUBLES the edge (+9.5%->+21.5%, win 62%->84%, t 3.1->4.1); on the
# excluded names the same short is a LOSER (-1.6%), because mega-caps/ETFs/crypto mean-revert up.
_MEGA = {"AAPL", "MSFT", "AMZN", "GOOG", "GOOGL", "META", "NVDA", "TSLA", "AMD", "NFLX", "INTC",
         "PLTR", "RDDT", "UBS", "IBKR", "NVO", "AVGO", "QCOM", "MU", "CRM", "ORCL", "ADBE",
         "PYPL", "DIS", "BA", "JPM", "BAC", "WFC", "GS", "C", "V", "MA", "KO", "PEP", "WMT",
         "COST", "HD", "MCD", "NKE", "XOM", "CVX", "PFE", "MRK", "LLY", "UNH", "JNJ", "T", "VZ",
         "CSCO", "IBM", "GE", "F", "GM", "COIN", "HOOD", "BABA", "SOFI", "MSTR"}
_WORDS = {"LIVE", "GOOD", "TIME", "SOAR", "BOOM", "PR", "SI", "OPEN", "REAL", "CASH", "PLAY",
          "WELL", "BEST", "LOVE", "HOPE", "FREE", "NOW", "ANY", "ONE", "BIG", "FUN", "GAIN",
          "SAVE", "PLUS", "ALL", "ON", "OR", "BY", "SO", "GO", "AI", "IT", "BE", "AM", "PM",
          "EV", "ER", "PE", "PT", "OG", "AH"}
_ETF_CRYPTO = {"QQQ", "IWM", "SPY", "VOO", "VOOG", "TQQQ", "SQQQ", "UVXY", "HYG", "XRT", "DIA",
               "XLF", "XLE", "SOXL", "SPXL", "ARKK", "IAU", "GLD", "SLV", "GDX", "USO",
               "ETH", "BTC", "SOL", "XRP", "DOGE", "ADA"}
ETFS = _MEGA | _WORDS | _ETF_CRYPTO   # name kept for back-compat; now the full off-thesis blocklist


def signals(px):
    """The frozen distribution_short rule: a concentrated pump (per_author>=2, mentions>=10) with
    still-euphoric sentiment (>=0.4) whose price has ALREADY rolled over (trailing-5d return<0)."""
    out = []
    for d in _rows():
        if d["new_frac_trail"] is None:
            continue
        if ((d["per_author"] or 0) >= 2.0 and (d["mentions"] or 0) >= 10
                and (d["sentiment"] or -9) >= 0.4
                and (_trailing_ret(px, d["ticker"], d["date"], 5) or 9) < 0
                and d["ticker"] not in ETFS):
            out.append((d["ticker"], d["date"][:10]))
    return out


def _trade(px, ticker, sig_date):
    """(entry_date, status, net) for a signal. status='open' until the 10-session forward
    window completes; 'closed' freezes the realized market-neutral net; None if untradeable."""
    dates, _ = px.get(ticker, ([], []))
    i = bisect.bisect_right(dates, sig_date[:10])          # first session strictly after signal
    if i >= len(dates):
        return None                                        # entry bar hasn't printed yet
    entry = dates[i]
    if i + HORIZON >= len(dates):
        return (entry, "open", None)                       # forward window not complete -> live
    net = _net(px, ticker, sig_date, -1, HORIZON, SPREAD_BPS, BORROW_BPS, True)
    if net is None:
        return (entry, "open", None)                       # SPY leg not caught up yet
    return (entry, "closed", net * 100.0)


def _ensure_table(c):
    c.execute("""CREATE TABLE IF NOT EXISTS dist_short_oos (
        ticker TEXT, signal_date TEXT, entry_date TEXT, side INTEGER DEFAULT -1,
        status TEXT, net_pct REAL, is_oos INTEGER, first_logged TEXT, updated TEXT,
        PRIMARY KEY (ticker, signal_date))""")


def update(today=None):
    today = today or datetime.now().strftime("%Y-%m-%d")
    px = _load_prices()
    c = get_connection()
    _ensure_table(c)
    seen = {(r["ticker"], r["signal_date"]) for r in c.execute(
        "SELECT ticker, signal_date FROM dist_short_oos").fetchall()}
    n_new = n_closed = 0
    for ticker, sig in signals(px):
        t = _trade(px, ticker, sig)
        if t is None:
            continue
        entry, status, net = t
        is_oos = 1 if sig >= FREEZE_DATE else 0
        if (ticker, sig) not in seen:
            c.execute("""INSERT INTO dist_short_oos
                (ticker, signal_date, entry_date, side, status, net_pct, is_oos, first_logged, updated)
                VALUES (?,?,?,-1,?,?,?,?,?)""",
                      (ticker, sig, entry, status, net, is_oos, today, today))
            n_new += 1
        else:
            # only ever transition open->closed; never re-touch a closed trade
            row = c.execute("SELECT status FROM dist_short_oos WHERE ticker=? AND signal_date=?",
                            (ticker, sig)).fetchone()
            if row["status"] == "open" and status == "closed":
                c.execute("UPDATE dist_short_oos SET status='closed', net_pct=?, entry_date=?, updated=? "
                          "WHERE ticker=? AND signal_date=?", (net, entry, today, ticker, sig))
                n_closed += 1
    c.commit()
    return c, n_new, n_closed


def _stats(nets):
    if len(nets) < 2:
        return None
    m = statistics.mean(nets)
    sd = statistics.pstdev(nets) or 1e-9
    return m, 100.0 * sum(1 for x in nets if x > 0) / len(nets), m / (sd / math.sqrt(len(nets)))


def report(c):
    rows = c.execute("SELECT * FROM dist_short_oos ORDER BY signal_date").fetchall()
    insamp = [r["net_pct"] for r in rows if r["is_oos"] == 0 and r["status"] == "closed"]
    oos = [r["net_pct"] for r in rows if r["is_oos"] == 1 and r["status"] == "closed"]
    openrows = [r for r in rows if r["status"] == "open"]
    print(f"=== distribution_short FORWARD-OOS LOG  (spec frozen {FREEZE_DATE}) ===")
    print(f"    market-neutral short, hold {HORIZON}d, spread {SPREAD_BPS}bps + {BORROW_BPS}bps/day borrow, ETFs excluded\n")
    si = _stats(insamp)
    if si:
        print(f"  in-sample  (signal < {FREEZE_DATE}, closed):  n={len(insamp):<3} "
              f"net={si[0]:+.2f}% win={si[1]:.0f}% t={si[2]:+.2f}")
    so = _stats(oos)
    if so:
        print(f"  FORWARD-OOS (signal >= {FREEZE_DATE}, closed): n={len(oos):<3} "
              f"net={so[0]:+.2f}% win={so[1]:.0f}% t={so[2]:+.2f}")
    elif oos:
        print(f"  FORWARD-OOS closed so far: n={len(oos)} (need >=2 for stats) "
              f"mean={statistics.mean(oos):+.2f}%")
    else:
        print(f"  FORWARD-OOS: 0 closed yet — the clock starts now; record builds as signals close.")
    if openrows:
        print(f"\n  OPEN live signals ({len(openrows)}) — awaiting their {HORIZON}d outcome:")
        for r in openrows[-12:]:
            tag = "OOS" if r["is_oos"] else "pre-freeze"
            print(f"    {r['ticker']:6} signal {r['signal_date']}  entry {r['entry_date'] or '(pending)'}  [{tag}]")


if __name__ == "__main__":
    c, n_new, n_closed = update()
    print(f"logged {n_new} new signal(s), closed {n_closed} this run\n")
    report(c)
    c.close()
