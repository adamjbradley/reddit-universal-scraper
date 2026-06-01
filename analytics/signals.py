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


def _latest(ticker):
    conn = get_connection()
    r = conn.execute("SELECT close FROM prices WHERE ticker=? ORDER BY date DESC LIMIT 1",
                     (ticker,)).fetchone()
    conn.close()
    return r["close"] if r else None


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
    # strength in [0,1]: how extreme the capitulation is (wide band so threshold isn't a cliff)
    strength = max(0.0, min(1.0, (0.20 - pct) / 0.20)) if pct is not None else 0.0
    return {
        "as_of": row["date"], "rrai_pct": pct, "vix": round(vix, 2) if vix else None,
        "vix_gate_ok": vix is None or vix >= VIX_MIN,
        "active": active, "strength": round(strength, 2),
    }


# Live macro strategies. The macro R&D (backtest/macro_research.py, excess-over-buy-and-hold)
# found ONE real edge: contrarian retail-fear. Momentum/froth/all-shorts had no edge (drift),
# so only `retail_fear` ships enabled. The list is multi-strategy by design - new survivors
# slot in here and the EA selects by strategy tag.
STRATEGIES = [
    {"name": "retail_fear", "status": "validated", "enabled": True,
     "desc": "long risk when retail capitulates (RRAI pct<=0.15) and VIX>=18 confirms fear"},
]


def _strategy_signals(name, st):
    if name == "retail_fear" and st["active"]:
        return [{"symbol": ins["symbol"], "asset": ins["asset"], "side": "long",
                 "strength": st["strength"], "horizon_days": HORIZON_DAYS,
                 "reason": f"retail capitulation (RRAI pct={st['rrai_pct']}) + VIX={st['vix']}"}
                for ins in INSTRUMENTS]
    return []


def current_signals(watchlist=True):
    """Multi-strategy live payload. Each strategy carries its status + actionable signals."""
    st = capitulation_state()
    strategies = []
    for sdef in STRATEGIES:
        sigs = _strategy_signals(sdef["name"], st) if sdef["enabled"] else []
        strategies.append({**sdef, "signals": sigs})
    payload = {
        "as_of": st["as_of"],
        "market": {"rrai_pct": st["rrai_pct"], "vix": st["vix"],
                   "capitulation_active": st["active"], "strength": st["strength"]},
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


def as_mt5_lines(payload=None):
    """Compact text the MT5 EA parses trivially: one 'STRATEGY,SYMBOL,SIDE,STRENGTH,HORIZON'
    per active signal. A single '# flat' line when nothing is active. The EA filters by its
    configured strategy tag."""
    payload = payload or current_signals(watchlist=False)
    rows = [f"{strat['name']},{s['symbol']},{s['side']},{s['strength']},{s['horizon_days']}"
            for strat in payload["strategies"] for s in strat["signals"]]
    return "\n".join(rows) if rows else "# flat"
