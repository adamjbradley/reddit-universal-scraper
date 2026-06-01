//+------------------------------------------------------------------+
//|  RedditMacro_EA.mq5                                             |
//|  ONE EA, two modes - auto-detected via MQLInfoInteger(MQL_TESTER):|
//|                                                                  |
//|   LIVE  : polls the /signals HTTP feed (WebRequest) and mirrors  |
//|           it across the mapped FX/index/metal CFDs (a basket).   |
//|   TESTER: WebRequest is disabled in the Strategy Tester, so it    |
//|           instead reads capitulation dates from a CSV in          |
//|           Common\Files and trades them on the CHART symbol -      |
//|           fully offline, fully backtestable.                      |
//|                                                                  |
//|  Strategy = retail_fear: long risk when retail capitulates.      |
//|  Validation lives in the Python backtester. DEMO ONLY.           |
//|                                                                  |
//|  Tester: use RedditMacro_Tester.ini (Expert=RedditMacro_EA,       |
//|  AUDJPY, D1, "Open prices only"); export the CSV with             |
//|  `python main.py --export-signals` into Common\Files.            |
//+------------------------------------------------------------------+
#property strict
#include <Trade/Trade.mqh>
#include "SignalClient.mqh"

// --- common ---
input double BaseLots        = 0.10;
input int    MaxHoldDays     = 10;               // hold / horizon (both modes)
input int    MagicNumber     = 770077;
// --- LIVE mode (ignored in the Strategy Tester) ---
input string SignalsUrl      = "http://YOUR_HOST:8000/signals?format=mt5";
input string AuthBearerToken = "";
input string StrategyTag     = "retail_fear";
input int    PollSeconds     = 300;
input bool   ScaleByStrength = true;
input string AudJpySymbol    = "AUDJPY";         // feed "AUDJPY"
input string GoldSymbol      = "XAUUSD";         // feed "XAUUSD"
input string IndexSymbol     = "US500";          // feed "US500"
input string TechSymbol      = "USTEC";          // feed "USTEC"
input string SilverSymbol    = "XAGUSD";         // feed "XAGUSD"
// --- STRATEGY-TESTER mode (ignored live) ---
input string SignalFile      = "rrai_capitulation.csv";   // Common\Files; dates YYYY.MM.DD

CTrade        trade;
CSignalClient client;
bool   g_tester = false;
// live: feed-symbol -> broker-symbol map
string g_feed[5], g_broker[5];
int    g_n = 0;
// tester: capitulation dates
datetime g_sig[];
int      g_sn = 0;
datetime g_entry = 0, g_lastBar = 0;

datetime FloorDay(datetime t) { return (t - (t % 86400)); }

int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   g_tester = (bool)MQLInfoInteger(MQL_TESTER);
   if(g_tester)
      return LoadSignalCsv();                    // TESTER: file only, no WebRequest

   client.Init(SignalsUrl, AuthBearerToken);     // LIVE: feed + basket
   g_n = 0;
   if(StringLen(AudJpySymbol) > 0){ g_feed[g_n]="AUDJPY"; g_broker[g_n]=AudJpySymbol; g_n++; }
   if(StringLen(GoldSymbol)   > 0){ g_feed[g_n]="XAUUSD"; g_broker[g_n]=GoldSymbol;   g_n++; }
   if(StringLen(IndexSymbol)  > 0){ g_feed[g_n]="US500";  g_broker[g_n]=IndexSymbol;  g_n++; }
   if(StringLen(TechSymbol)   > 0){ g_feed[g_n]="USTEC";  g_broker[g_n]=TechSymbol;   g_n++; }
   if(StringLen(SilverSymbol) > 0){ g_feed[g_n]="XAGUSD"; g_broker[g_n]=SilverSymbol; g_n++; }
   EventSetTimer(MathMax(10, PollSeconds));
   Print("RedditMacro_EA LIVE; strategy=", StrategyTag, " url=", SignalsUrl);
   OnTimer();
   return(INIT_SUCCEEDED);
}

int LoadSignalCsv()
{
   ArrayResize(g_sig, 0);
   int h = FileOpen(SignalFile, FILE_COMMON | FILE_READ | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE)
   {
      Print("TESTER: cannot open ", SignalFile, " in Common\\Files (err ", GetLastError(),
            "). Run: python main.py --export-signals, then copy it there.");
      return(INIT_FAILED);
   }
   while(!FileIsEnding(h))
   {
      string s = FileReadString(h);
      StringTrimLeft(s); StringTrimRight(s);
      if(StringLen(s) >= 8)
      {
         datetime d = StringToTime(s);
         if(d > 0) { int n = ArraySize(g_sig); ArrayResize(g_sig, n + 1); g_sig[n] = FloorDay(d); }
      }
   }
   FileClose(h);
   g_sn = ArraySize(g_sig);
   Print("RedditMacro_EA TESTER; loaded ", g_sn, " capitulation dates from ", SignalFile);
   return(g_sn > 0 ? INIT_SUCCEEDED : INIT_FAILED);
}

void OnDeinit(const int reason) { if(!g_tester) EventKillTimer(); }

//==================== LIVE: poll feed, reconcile basket ====================
void OnTimer()
{
   if(g_tester) return;
   SignalRow rows[];
   if(!client.Fetch(rows, StrategyTag)) return;
   bool wantLong[5]; double strength[5];
   for(int i = 0; i < g_n; i++){ wantLong[i] = false; strength[i] = 0.0; }
   for(int r = 0; r < ArraySize(rows); r++)
      for(int i = 0; i < g_n; i++)
         if(rows[r].symbol == g_feed[i] && rows[r].side == "long")
         { wantLong[i] = true; strength[i] = rows[r].strength; }
   for(int i = 0; i < g_n; i++)
   {
      string sym = g_broker[i];
      bool   have = SC_HasPosition(sym, MagicNumber);
      if(wantLong[i] && !have)
      {
         double lots = SC_NormalizeLots(sym, BaseLots * (ScaleByStrength ? MathMax(0.1, strength[i]) : 1.0));
         if(lots > 0 && trade.Buy(lots, sym)) Print("OPEN long ", sym, " lots=", lots);
      }
      else if(have && (!wantLong[i] || SC_PositionAgeDays(sym, MagicNumber) >= MaxHoldDays))
      {
         if(trade.PositionClose(sym))
            Print("CLOSE ", sym, (!wantLong[i] ? " (signal cleared)" : " (max hold)"));
      }
   }
}

//==================== TESTER: trade cap-dates on the chart symbol ====================
bool IsSignalDay(datetime day)
{
   for(int i = 0; i < g_sn; i++)
      if(g_sig[i] == day) return(true);
   return(false);
}

bool HasPosSym()
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
   if(!g_tester) return;
   datetime bt = iTime(_Symbol, _Period, 0);
   if(bt == g_lastBar) return;            // act once per new bar
   g_lastBar = bt;
   datetime day = FloorDay(bt);
   if(HasPosSym())
   {
      if((day - g_entry) / 86400 >= MaxHoldDays) trade.PositionClose(_Symbol);
   }
   else if(IsSignalDay(day))
   {
      double lots = MathMax(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), BaseLots);
      if(trade.Buy(lots, _Symbol)) g_entry = day;
   }
}
//+------------------------------------------------------------------+
