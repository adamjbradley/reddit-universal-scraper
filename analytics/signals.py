"""Live signal generator — turns the feature store into actionable signals for the
/signals feed consumed by execution clients (the MT5 EA, an Alpaca paper trader, etc.).

Currently serves the one Phase-0-validated edge: the RRAI capitulation overlay (buy risk
when retail capitulates AND VIX confirms genuine fear). The robustness battery showed a
clean dose-response (more extreme + higher VIX + longer hold => bigger bounce), strongest
and most sign-stable via FX AUDJPY. The equity pump book is NOT served here yet (it did
not survive friction-aware backtesting); pump_suspects is included as an informational
WATCHLIST only, explicitly not actionable.
"""
import statistics

from export.database import get_connection

# --- validated overlay parameters (Phase 0 backtest + robustness sweep) ---
CAP_THRESHOLD = 0.15     # rrai_pct <= this = retail capitulation
VIX_MIN = 18.0           # require genuine fear (VIX gate ~4x'd the edge)
HORIZON_DAYS = 10        # holding period that maximised the bounce
# The diversified risk-on basket that expresses "buy risk when retail capitulates". An
# instrument scan across FX/commodities/indices/crypto (backtest/macro_research.py) found
# the signal GENERALISES across risk assets - so we spread across the robust ones rather
# than bet one. The EA maps these to its broker's symbol names.
INSTRUMENTS = [
    {"symbol": "AUDJPY", "asset": "fx",        "note": "steadiest (win 88%, +ve every regime)"},
    {"symbol": "XAUUSD", "asset": "commodity", "note": "gold - robust (+ve every regime)"},
    {"symbol": "US500",  "asset": "index",     "note": "S&P 500"},
    {"symbol": "USTEC",  "asset": "index",     "note": "Nasdaq 100"},
    {"symbol": "XAGUSD", "asset": "commodity", "note": "silver - highest excess but lumpy; size SMALL"},
]

# Feed symbol -> price-table (Yahoo) symbol, for the trend filter.
PRICE_SYMBOL = {"AUDJPY": "AUDJPY=X", "XAUUSD": "GC=F", "US500": "SPY", "USTEC": "QQQ", "XAGUSD": "SI=F"}
# Equity index legs FAILED the 2022 bear -> only fire their capitulation-long in an uptrend.
# AUDJPY + metals were all-weather in the multi-regime test, so they are NOT gated.
GATE_TREND = {"US500", "USTEC"}
EUPH_THRESHOLD = 0.85   # rrai_pct >= this = retail euphoria (short only in a downtrend)
FEAR_MIN = 0.5          # equity legs also need INDEPENDENT fear confirmation (Wikipedia z-score)


def _latest(ticker):
    conn = get_connection()
    r = conn.execute("SELECT close FROM prices WHERE ticker=? ORDER BY date DESC LIMIT 1",
                     (ticker,)).fetchone()
    conn.close()
    return r["close"] if r else None


def _trend(feed_symbol, slow=200, fast=50):
    """Price trend for an instrument: +1 up / -1 down / 0 neutral (close vs 200-DMA, confirmed
    by 50-DMA). 0 if insufficient history. Used to gate signals by regime."""
    ysym = PRICE_SYMBOL.get(feed_symbol, feed_symbol)
    conn = get_connection()
    rows = conn.execute("SELECT close FROM prices WHERE ticker=? ORDER BY date DESC LIMIT ?",
                        (ysym, slow)).fetchall()
    conn.close()
    if len(rows) < slow:
        return 0
    closes = [r["close"] for r in rows][::-1]
    c, sslow, sfast = closes[-1], sum(closes) / slow, sum(closes[-fast:]) / fast
    if c > sslow and sfast >= sslow:
        return 1
    if c < sslow and sfast <= sslow:
        return -1
    return 0


def _fear_z(lookback=31):
    """Independent fear-attention z-score from Wikipedia (latest total fear-page views vs the
    trailing ~30d). >0 = elevated fear. Confirms capitulation for the equity legs - backtests
    showed SPY capitulation only works (+0.36pp) when an independent fear-spike confirms it."""
    conn = get_connection()
    rows = conn.execute("""SELECT date, SUM(volume) v FROM external_sentiment
                           WHERE source='wikipedia' AND ticker LIKE 'fear_%' AND volume IS NOT NULL
                           GROUP BY date ORDER BY date DESC LIMIT ?""", (lookback,)).fetchall()
    conn.close()
    if len(rows) < 11:
        return 0.0
    vals = [r["v"] for r in rows]            # newest first
    mu = statistics.mean(vals[1:])
    sd = statistics.pstdev(vals[1:]) or 1.0
    return round((vals[0] - mu) / sd, 2)


def capitulation_state():
    """Latest RRAI + VIX and whether the capitulation overlay is active right now."""
    conn = get_connection()
    row = conn.execute("""SELECT date, rrai_pct, bull_frac, bear_frac FROM aggregate_daily
                          WHERE rrai_pct IS NOT NULL ORDER BY date DESC LIMIT 1""").fetchone()
    conn.close()
    vix = _latest("^VIX")
    if not row:
        return {"as_of": None, "rrai_pct": None, "vix": vix, "active": False, "strength": 0.0}
    pct = row["rrai_pct"]
    active = pct is not None and pct <= CAP_THRESHOLD and (vix is None or vix >= VIX_MIN)
    strength = max(0.0, min(1.0, (0.20 - pct) / 0.20)) if pct is not None else 0.0
    euphoria = pct is not None and pct >= EUPH_THRESHOLD
    strength_e = max(0.0, min(1.0, (pct - 0.80) / 0.20)) if pct is not None else 0.0
    return {
        "as_of": row["date"], "rrai_pct": pct, "vix": round(vix, 2) if vix else None,
        "vix_gate_ok": vix is None or vix >= VIX_MIN,
        "active": active, "strength": round(strength, 2),
        "euphoria": euphoria, "strength_e": round(strength_e, 2),
    }


# Live macro strategies. The macro R&D (backtest/macro_research.py, excess-over-buy-and-hold)
# found ONE real edge: contrarian retail-fear. Momentum/froth/all-shorts had no edge (drift),
# so only `retail_fear` ships enabled. The list is multi-strategy by design - new survivors
# slot in here and the EA selects by strategy tag.
STRATEGIES = [
    {"name": "retail_fear", "status": "validated", "enabled": True,
     "desc": "long risk when retail capitulates (RRAI<=0.15)+VIX>=18; equity legs gated to uptrend"},
    {"name": "euphoria_short", "status": "regime-gated", "enabled": True,
     "desc": "short index ONLY when retail euphoria (RRAI>=0.85) coincides with a downtrend"},
]


def _strategy_signals(name, st):
    if name == "retail_fear" and st["active"]:
        fz = _fear_z()
        out = []
        for ins in INSTRUMENTS:
            tr = _trend(ins["symbol"])
            # equity legs need BOTH an uptrend AND independent fear confirmation (they're
            # dead otherwise); AUDJPY/metals are all-weather and ungated.
            if ins["symbol"] in GATE_TREND and (tr < 0 or fz < FEAR_MIN):
                continue
            out.append({"symbol": ins["symbol"], "asset": ins["asset"], "side": "long",
                        "strength": st["strength"], "horizon_days": HORIZON_DAYS,
                        "trend": tr, "fear_z": fz,
                        "reason": f"retail capitulation (RRAI={st['rrai_pct']}) + VIX={st['vix']}"})
        return out
    if name == "euphoria_short" and st.get("euphoria"):
        out = []
        for ins in INSTRUMENTS:
            if ins["asset"] != "index" or _trend(ins["symbol"]) >= 0:  # index legs, downtrend only
                continue
            out.append({"symbol": ins["symbol"], "asset": ins["asset"], "side": "short",
                        "strength": st["strength_e"], "horizon_days": HORIZON_DAYS, "trend": -1,
                        "reason": f"retail euphoria (RRAI={st['rrai_pct']}) in a confirmed downtrend"})
        return out
    return []


# --- advisory SCREENS: every other idea, served as tagged signals (NOT auto-traded) ---
_UNIV = None


def _univ():
    global _UNIV
    if _UNIV is None:
        try:
            from backtest.sectors import universes
            _UNIV = universes()
        except Exception:
            _UNIV = {}
    return _UNIV


def _recent_feat(where, days=4, limit=40):
    conn = get_connection()
    rows = conn.execute(f"""SELECT ticker, date, mentions, breadth, per_author, concentration,
                                   mentions_z, sentiment, young_frac, gone_frac
                            FROM feature_daily
                            WHERE date >= date((SELECT MAX(date) FROM feature_daily), ?) AND {where}
                            ORDER BY date DESC, mentions_z DESC LIMIT ?""",
                        (f'-{days} days', limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _screen_smallcap_fade():
    u = _univ().get("smallcap", set())
    return [{"symbol": r["ticker"], "side": "short", "per_author": r["per_author"],
             "mentions": r["mentions"], "date": r["date"],
             "reason": "small-cap concentrated pump -> fade (validated t=-3.93)"}
            for r in _recent_feat("per_author>=3 AND mentions>=8") if r["ticker"] in u][:12]


def _screen_biotech():
    u = _univ().get("biotech", set())
    return [{"symbol": r["ticker"], "side": "long", "per_author": r["per_author"], "date": r["date"],
             "reason": "biotech catalyst hype (fat-tailed lottery; size small)"}
            for r in _recent_feat("per_author>=2.5 AND mentions>=5", limit=60) if r["ticker"] in u][:8]


def _screen_attention_fade():
    return [{"symbol": r["ticker"], "side": "short", "mentions_z": r["mentions_z"], "date": r["date"],
             "reason": "broad attention spike -> mean-reverts down (organic_long was a loser)"}
            for r in _recent_feat("mentions_z>=2.5 AND concentration<=0.15 AND breadth>=8")][:10]


def _screen_fresh_pump():
    try:
        from export.database import fresh_pump_suspects
        return [{"symbol": r["ticker"], "side": "avoid/fade", "fresh": r["fresh"],
                 "per_author": r["per_author"], "young_frac": r["young_frac"],
                 "reason": "fresh concentrated burst (catch within hours)"}
                for r in fresh_pump_suspects(limit=10)]
    except Exception:
        return []


def _screen_coordination():
    try:
        from analytics.graph import suspected_coordination
        r = suspected_coordination(window_hours=72)
        items = r if isinstance(r, list) else (r.get("flagged") or r.get("suspected") or r.get("results") or [])
        out = []
        for x in items[:10]:
            tk = x.get("ticker") if isinstance(x, dict) else x
            if tk:
                out.append({"symbol": tk, "side": "avoid", "reason": "suspected coordinated pushing"})
        return out
    except Exception:
        return []


def _calendar_overlay():
    import datetime as _dt
    d = _dt.date.today()
    tom = d.day <= 3 or d.day >= 26          # turn-of-month (equity flow)
    weak = d.month in (2, 3, 9)              # historically weak months
    bias = "risk-off tilt" if weak else ("risk-on tilt" if tom else "neutral")
    return [{"flag": "turn_of_month" if tom else "mid_month", "month": d.strftime("%b"),
             "seasonal": "weak" if weak else "ok", "bias": bias,
             "reason": "equity seasonality overlay (turn-of-month +; Feb/Mar/Sep weak)"}]


SCREENS = [
    {"name": "smallcap_pump_fade", "category": "equity",       "status": "validated(t=-3.93)",    "tradeable": False, "gen": _screen_smallcap_fade},
    {"name": "fresh_pump_alert",   "category": "manipulation", "status": "live-screen",           "tradeable": False, "gen": _screen_fresh_pump},
    {"name": "coordination_flag",  "category": "manipulation", "status": "screen",                "tradeable": False, "gen": _screen_coordination},
    {"name": "biotech_catalyst",   "category": "equity",       "status": "experimental(lottery)", "tradeable": False, "gen": _screen_biotech},
    {"name": "attention_fade",     "category": "equity",       "status": "research",              "tradeable": False, "gen": _screen_attention_fade},
    {"name": "calendar_overlay",   "category": "seasonality",  "status": "advisory",              "tradeable": False, "gen": _calendar_overlay},
]


def current_signals(watchlist=True, force=False):
    """Multi-strategy live payload. Each strategy carries its status + actionable signals.
    force=True simulates an active capitulation signal (for end-to-end EA testing when the
    live market is flat) - DO NOT use for real trading decisions."""
    st = capitulation_state()
    if force:                                    # test mode: pretend capitulation is active
        st = dict(st)
        st["active"] = True
        st["strength"] = st["strength"] or 0.5
        st["test"] = True
    strategies = []
    for sdef in STRATEGIES:
        sigs = _strategy_signals(sdef["name"], st) if sdef["enabled"] else []
        strategies.append({**sdef, "signals": sigs})
    screens = []
    if watchlist:
        for sdef in SCREENS:
            try:
                inst = sdef["gen"]()
            except Exception:
                inst = []
            screens.append({"name": sdef["name"], "category": sdef["category"],
                            "status": sdef["status"], "tradeable": sdef["tradeable"], "signals": inst})
    payload = {
        "as_of": st["as_of"],
        "market": {"rrai_pct": st["rrai_pct"], "vix": st["vix"],
                   "capitulation_active": st["active"], "euphoria": st.get("euphoria", False),
                   "spx_trend": _trend("US500"), "fear_z": _fear_z(), "strength": st["strength"]},
        "strategies": strategies,   # TRADEABLE (auto-trade): retail_fear, euphoria_short
        "screens": screens,         # advisory / watch - NOT auto-traded (status-tagged)
        "disclaimer": "Only `strategies` are auto-tradeable (retail_fear validated multi-regime). "
                      "`screens` are advisory/research - tagged by status, not auto-traded. "
                      "All preliminary: small n, overlapping windows.",
    }
    return payload


def as_mt5_lines(payload=None, force=False):
    """Compact text the MT5 EA parses trivially: one 'STRATEGY,SYMBOL,SIDE,STRENGTH,HORIZON'
    per active signal. A single '# flat' line when nothing is active. The EA filters by its
    configured strategy tag."""
    payload = payload or current_signals(watchlist=False, force=force)
    rows = [f"{strat['name']},{s['symbol']},{s['side']},{s['strength']},{s['horizon_days']}"
            for strat in payload["strategies"] for s in strat["signals"]]
    return "\n".join(rows) if rows else "# flat"
