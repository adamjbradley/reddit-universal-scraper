"""Export D1 OHLC straight from the MT5 terminal (the exact broker data the Strategy Tester uses)
so the Python backtest (backtest/mt5_sim.py) can model stops/ATR/drawdown accurately instead of
the close-only Yahoo proxy. Runs on the HOST (Windows) where MT5 + the MetaTrader5 package live.

  python scripts/export_mt5_ohlc.py   ->  writes mt5_ohlc_<SYM>.csv to %TEMP%

Then load into the container's mt5_ohlc table.
"""
import os
import csv
from datetime import datetime

import MetaTrader5 as mt5

SYMBOLS = ["XAUUSD", "AUDJPY", "AUDUSD", "XAGUSD", "US500", "USTEC"]
FROM = datetime(2016, 1, 1)
TO = datetime(2026, 6, 3)
TERM = r"C:\Program Files\MetaTrader 5\terminal64.exe"


def main():
    if not mt5.initialize(path=TERM):
        if not mt5.initialize():
            print("initialize() failed:", mt5.last_error())
            return
    info = mt5.terminal_info()
    print("connected:", info.name if info else "?", "| build", mt5.version())
    out = os.environ.get("TEMP", ".")
    for sym in SYMBOLS:
        mt5.symbol_select(sym, True)
        rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_D1, FROM, TO)
        if rates is None or len(rates) == 0:
            print(f"  {sym:8} NO DATA ({mt5.last_error()})")
            continue
        path = os.path.join(out, f"mt5_ohlc_{sym}.csv")
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "date", "open", "high", "low", "close"])
            for r in rates:
                d = datetime.utcfromtimestamp(int(r["time"])).strftime("%Y-%m-%d")
                w.writerow([sym, d, r["open"], r["high"], r["low"], r["close"]])
        print(f"  {sym:8} {len(rates):5d} bars  {datetime.utcfromtimestamp(int(rates[0]['time'])).date()}"
              f"..{datetime.utcfromtimestamp(int(rates[-1]['time'])).date()}")
    mt5.shutdown()


if __name__ == "__main__":
    main()
