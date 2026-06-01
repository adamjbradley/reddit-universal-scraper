//+------------------------------------------------------------------+
//|  RedditMacro_Tester.mq5                                         |
//|  STRATEGY-TESTER variant of the retail_fear macro strategy.     |
//|                                                                  |
//|  The live EA (RedditMacro_EA) polls an HTTP feed via WebRequest, |
//|  which the Strategy Tester DISABLES - so it can't be backtested. |
//|  This variant instead reads the historical capitulation dates    |
//|  from a CSV (exported by the Python side) and trades them on the  |
//|  chart symbol's bars - fully offline, fully testable.            |
//|                                                                  |
//|  RULE: on a capitulation day, if flat, go LONG; exit after        |
//|  HorizonDays. One position at a time (clean equity curve).        |
//|                                                                  |
//|  SETUP:                                                          |
//|   - Put the CSV (one date per line, YYYY.MM.DD) in the terminal's |
//|     Common\Files folder (the Python exporter does this).          |
//|   - Strategy Tester: this EA, Symbol=AUDJPY (or XAUUSD/US500...),  |
//|     Period=D1, model "Open prices only". Use RedditMacro_Tester.ini|
//+------------------------------------------------------------------+
#property strict
#include <Trade/Trade.mqh>

input string SignalFile  = "rrai_capitulation.csv";  // in Common\Files; dates YYYY.MM.DD
input int    HorizonDays = 10;                        // bars to hold (matches the signal)
input double BaseLots    = 0.10;
input int    MagicNumber = 770078;

CTrade   trade;
datetime g_sig[];      // capitulation dates (floored to day)
int      g_n = 0;
datetime g_entry = 0;
datetime g_lastBar = 0;

datetime FloorDay(datetime t) { return (t - (t % 86400)); }

int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   ArrayResize(g_sig, 0);
   int h = FileOpen(SignalFile, FILE_COMMON | FILE_READ | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE)
   {
      Print("Cannot open ", SignalFile, " in Common\\Files (err ", GetLastError(),
            "). Export it from the Python side first.");
      return(INIT_FAILED);
   }
   while(!FileIsEnding(h))
   {
      string s = FileReadString(h);
      StringTrimLeft(s); StringTrimRight(s);
      if(StringLen(s) >= 8)
      {
         datetime d = StringToTime(s);   // "YYYY.MM.DD" -> datetime
         if(d > 0) { int n = ArraySize(g_sig); ArrayResize(g_sig, n + 1); g_sig[n] = FloorDay(d); }
      }
   }
   FileClose(h);
   g_n = ArraySize(g_sig);
   Print("RedditMacro_Tester: loaded ", g_n, " capitulation dates from ", SignalFile);
   return(g_n > 0 ? INIT_SUCCEEDED : INIT_FAILED);
}

bool IsSignalDay(datetime day)
{
   for(int i = 0; i < g_n; i++)
      if(g_sig[i] == day) return(true);
   return(false);
}

bool HasPos()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(PositionGetTicket(i) == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == MagicNumber) return(true);
   }
   return(false);
}

void OnTick()
{
   datetime bt = iTime(_Symbol, _Period, 0);
   if(bt == g_lastBar) return;          // act once per new bar
   g_lastBar = bt;
   datetime day = FloorDay(bt);

   if(HasPos())
   {
      if((day - g_entry) / 86400 >= HorizonDays)
         trade.PositionClose(_Symbol);   // exit after the horizon
   }
   else if(IsSignalDay(day))
   {
      double lots = MathMax(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), BaseLots);
      if(trade.Buy(lots, _Symbol)) g_entry = day;
   }
}
//+------------------------------------------------------------------+
