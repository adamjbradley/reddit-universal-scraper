"""Live actionable feed for distribution_short — deployment-ready "what to trade now".

Applies the frozen spec to the FRESHEST data (use_store=False) and emits an actionable short list
by CONVICTION tier and LIQUIDITY tier, with the defined-risk PUTS framing. The edge is the
rollover gate (trailing-5d<0); always express via puts — caps the squeeze tail (BFRI -115%).

Conviction tiers (same engine, concentration knob):
  HIGH  — per_author>=2 & mentions>=10  (highest per-trade, +12.7%)
  CAP   — per_author>=3 & mentions>=8   (higher capacity, +9.5%, positive every regime)
Liquidity tiers (signal-day price = optionability/borrow proxy):
  TRADE $5+  |  THIN $2-5  |  SKIP <$2 (no options / lethal borrow)

  docker compose exec mcp python -m backtest.live_signals [days]
"""
import sys
import bisect
from datetime import datetime, timedelta

from backtest.microstructure import _rows, _trailing_ret
from backtest.engine import _load_prices
from backtest.dist_short_oos import ETFS as BLOCK


def _px_at(px, t, d):
    if t not in px:
        return None
    ds, cs = px[t]
    i = bisect.bisect_right(ds, d[:10]) - 1
    return cs[i] if 0 <= i < len(cs) else None


def signals(px, days):
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for d in _rows(use_store=False):                      # fresh SQLite, never a stale snapshot
        if d["new_frac_trail"] is None or d["ticker"] in BLOCK or d["date"][:10] < cutoff:
            continue
        pa, mn, s = d["per_author"] or 0, d["mentions"] or 0, d["sentiment"] or -9
        roll = (_trailing_ret(px, d["ticker"], d["date"], 5) or 9) < 0
        if not (s >= 0.4 and roll):
            continue
        if pa >= 2 and mn >= 10:
            tier = "HIGH"
        elif pa >= 3 and mn >= 8:
            tier = "CAP"
        else:
            continue
        p = _px_at(px, d["ticker"], d["date"])
        out.append((d["date"][:10], d["ticker"], tier, p, pa, mn, s))
    return sorted(out, reverse=True)


def fresh_tradeable(days=7):
    """Actionable signals only: fresh + in the liquid $5-50 small/mid band. For scheduler alerts."""
    px = _load_prices()
    return [x for x in signals(px, days) if x[3] and 5 <= x[3] <= 50]


def _liq(p):
    """Price-tier proxy for tradeability. The edge works micro->mid and REVERSES on large-cap,
    so a high price is a (crude) large-cap exclusion, not a green light."""
    if p is None:
        return "?     "
    if p < 2:
        return "SKIP  "          # no options / lethal borrow
    if p < 5:
        return "THIN  "          # marginal options/borrow
    if p <= 50:
        return "TRADE "          # liquid small/mid -- the edge's sweet spot
    return "LARGE?"              # >$50 likely large-cap -> edge REVERSES, do NOT short


def run(days=21):
    px = _load_prices()
    sig = signals(px, days)
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"=== distribution_short LIVE FEED — as of {today} (last {days}d) ===")
    print("    rollover-gated micro-cap pump fade -> SHORT VIA PUTS (~2-4wk, ATM/slightly-OTM, risk=premium)\n")
    if not sig:
        print("  no fresh signals in window.")
        return
    for tier_lbl, code in (("HIGH-CONVICTION (pa>=2, m>=10)", "HIGH"),
                           ("HIGHER-CAPACITY (pa>=3, m>=8)", "CAP")):
        rows = [x for x in sig if x[2] == code]
        if not rows:
            continue
        print(f"  {tier_lbl}:")
        print(f"    {'date':10} {'ticker':7} {'px':>7}  {'liq':6} {'per_auth':>8} {'ment':>5} {'sent':>5}")
        for dt, t, _, p, pa, mn, s in rows:
            pxs = f"${p:.2f}" if p else "n/a"
            lq = _liq(p)
            note = {"SKIP  ": "  <- illiquid, skip", "THIN  ": "  <- thin, scale down",
                    "LARGE?": "  <- likely large-cap, edge REVERSES, skip"}.get(lq, "")
            print(f"    {dt:10} {t:7} {pxs:>7}  {lq} {pa:>8.1f} {mn:>5} {s:>5.2f}{note}")
        print()
    trade = [x for x in sig if x[3] and 5 <= x[3] <= 50]
    print(f"  -> {len(trade)} of {len(sig)} signals are TRADEABLE ($5-50 liquid small/mid); "
          f"express each via puts, size by conviction tier.")


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 21)
