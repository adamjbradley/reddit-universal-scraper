"""Does the pump signal LOCALISE to a sector/universe where it failed broadly?

Universes (data-driven where possible):
  - biotech  : tickers mentioned in r/Biotechplays (catalyst-driven small-cap pharma)
  - smallcap : tickers whose mentions are mostly from the penny subs
  - energy   : a curated list of major US energy names (no dedicated sub)
We segment the concentrated-pump events (from feature_daily) by universe and measure the
5/10d forward EXCESS-vs-SPY return per universe vs the ALL baseline. Run:
  python -m backtest.sectors
"""
import statistics
from export.database import get_connection
from backtest.engine import _load_prices, _fwd

# --- curated thematic universes (no dedicated sub for these) ---
ENERGY  = {"ET","PR","XOM","CVX","COP","OXY","SLB","HAL","DVN","FANG","MPC","VLO","KMI",
           "WMB","OKE","EOG","PSX","MRO","APA","CTRA","EQT","AR","RRC","NOG","SM","PXD",
           "HES","TRGP","LNG"}
AI      = {"NVDA","AMD","SMCI","PLTR","AI","BBAI","SOUN","NBIS","CRWV","VRT","ARM","MRVL"}
QUANTUM = {"IONQ","QBTS","RGTI","QUBT","ARQQ","LAES"}
SPACE   = {"ASTS","RKLB","SPCE","LUNR","RDW","RCAT","KULR"}
NUCLEAR = {"SMR","OKLO","CCJ","LEU","NNE","UEC","DNN","NUKZ","UUUU"}
ROBOT   = {"TSLA","SERV","RBOT","PRCT"}
CRYPTO_EQ = {"MSTR","COIN","MARA","RIOT","CLSK","HUT","BITF","HOOD","BTBT","WULF"}
MEME    = {"GME","AMC","BYND","OPEN","KOSS","BB","NOK","BBBY","CHWY","RDDT"}
# "Futurology" = frontier/future-tech speculation (the theme retail piles into).
FRONTIER = AI | QUANTUM | SPACE | NUCLEAR | ROBOT
CURATED = {"energy": ENERGY, "ai": AI, "quantum": QUANTUM, "space": SPACE,
           "nuclear": NUCLEAR, "crypto_eq": CRYPTO_EQ, "meme": MEME, "frontier": FRONTIER}
PENNY_SUBS = ("pennystocks", "RobinHoodPennyStocks", "smallstreetbets")
SQUEEZE_SUBS = ("Shortsqueeze", "SqueezePlays")


def universes():
    """Data-driven universes from subreddit provenance + curated thematic lists."""
    c = get_connection()
    bio = {r["ticker"] for r in c.execute(
        "SELECT ticker FROM ticker_mentions WHERE subreddit='Biotechplays' "
        "GROUP BY ticker HAVING COUNT(*)>=3").fetchall()}

    def share_set(subs, thresh=0.5, min_n=20):
        inq = ",".join("'%s'" % s for s in subs)
        rows = c.execute(f"""SELECT ticker,
            SUM(CASE WHEN subreddit IN ({inq}) THEN 1 ELSE 0 END)*1.0/COUNT(*) AS sh,
            COUNT(*) AS n FROM ticker_mentions GROUP BY ticker HAVING n>=?""", (min_n,)).fetchall()
        return {r["ticker"] for r in rows if (r["sh"] or 0) >= thresh}

    small = share_set(PENNY_SUBS, 0.5)
    squeeze = share_set(SQUEEZE_SUBS, 0.4)
    c.close()
    return {"biotech": bio, "smallcap": small, "squeeze": squeeze, **CURATED}


def pump_events(min_pa=2.5, min_m=8, min_z=1.5):
    c = get_connection()
    ev = [(r["ticker"], r["date"]) for r in c.execute(
        "SELECT ticker, date FROM feature_daily WHERE per_author>=? AND mentions>=? AND mentions_z>=?",
        (min_pa, min_m, min_z)).fetchall()]
    c.close()
    return ev


def run(horizon=10):
    px = _load_prices()
    U = universes()
    ev = pump_events()

    def excess(tk, d):
        a, b = _fwd(px, tk, d, horizon), _fwd(px, "SPY", d, horizon)
        return None if (a is None or b is None) else a - b

    segs = {"ALL": []}
    for name in U:
        segs[name] = []
    for tk, d in ev:
        e = excess(tk, d)
        if e is None:
            continue
        segs["ALL"].append(e)
        for name, s in U.items():
            if tk in s:
                segs[name].append(e)

    print(f"=== Pump signal by universe (concentrated mention-days, {horizon}d excess vs SPY) ===")
    print(f"    universe sizes: " + ", ".join(f"{k}={len(v)} tickers" for k, v in U.items()))
    print()
    for name, xs in segs.items():
        if len(xs) >= 5:
            m = statistics.mean(xs)
            win = sum(1 for x in xs if x > 0) / len(xs) * 100
            sd = statistics.pstdev(xs) or 1e-9
            t = m / (sd / (len(xs) ** 0.5))
            print(f"  {name:9s} n={len(xs):<4} excess={m*100:+.2f}%  win={win:.0f}%  t={t:+.2f}")
        else:
            print(f"  {name:9s} n={len(xs):<4} (too few to judge)")


if __name__ == "__main__":
    main = run
    run()
