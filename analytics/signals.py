"""Live signal generator — turns the feature store into actionable signals for the
/signals feed consumed by execution clients (the MT5 EA, an Alpaca paper trader, etc.).

Currently serves the one Phase-0-validated edge: the RRAI capitulation overlay (buy risk
when retail capitulates AND VIX confirms genuine fear). The robustness battery showed a
clean dose-response (more extreme + higher VIX + longer hold => bigger bounce), strongest
and most sign-stable via FX AUDJPY. The equity pump book is NOT served here yet (it did
not survive friction-aware backtesting); pump_suspects is included as an informational
WATCHLIST only, explicitly not actionable.
"""
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
        out = []
        for ins in INSTRUMENTS:
            tr = _trend(ins["symbol"])
            if ins["symbol"] in GATE_TREND and tr < 0:        # don't buy fear in a downtrend
                continue
            out.append({"symbol": ins["symbol"], "asset": ins["asset"], "side": "long",
                        "strength": st["strength"], "horizon_days": HORIZON_DAYS, "trend": tr,
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
    payload = {
        "as_of": st["as_of"],
        "market": {"rrai_pct": st["rrai_pct"], "vix": st["vix"],
                   "capitulation_active": st["active"], "euphoria": st.get("euphoria", False),
                   "spx_trend": _trend("US500"), "strength": st["strength"]},
        "strategies": strategies,
        "disclaimer": "Phase-0 preliminary: single ~12mo regime, small n, overlapping windows; "
                      "edge is a modest timing tilt over buy-and-hold (~0.2-0.5pp/10d).",
    }
    if watchlist:
        try:
            from export.database import pump_suspects
            payload["watchlist_pump_suspects"] = pump_suspects(window_days=14, min_mentions=10, limit=10)
        except Exception:
            payload["watchlist_pump_suspects"] = []
    return payload


def as_mt5_lines(payload=None, force=False):
    """Compact text the MT5 EA parses trivially: one 'STRATEGY,SYMBOL,SIDE,STRENGTH,HORIZON'
    per active signal. A single '# flat' line when nothing is active. The EA filters by its
    configured strategy tag."""
    payload = payload or current_signals(watchlist=False, force=force)
    rows = [f"{strat['name']},{s['symbol']},{s['side']},{s['strength']},{s['horizon_days']}"
            for strat in payload["strategies"] for s in strat["signals"]]
    return "\n".join(rows) if rows else "# flat"
