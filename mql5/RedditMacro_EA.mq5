//+------------------------------------------------------------------+
//|  RedditMacro_EA.mq5                                             |
//|  ONE EA, two modes - auto-detected via MQLInfoInteger(MQL_TESTER):|
//|                                                                  |
//|   LIVE  : polls the /signals HTTP feed (WebRequest) and mirrors  |
//|           it across the mapped AUD/NZD + gold basket.            |
//|   TESTER: reads capitulation dates from a CSV in Common\Files    |
//|           and trades them on the CHART symbol - fully offline.    |
//|                                                                  |
//|  Strategy = retail_fear: long risk when retail capitulates.      |
//|  Entry: TURN-based (wait for the bounce, not the first fall) +    |
//|         ATR stop. Sizing: Fixed / ATR / Kelly (selectable).      |
//|  Validation lives in the Python backtester. DEMO ONLY.           |
//+------------------------------------------------------------------+
#property strict
#include <Trade/Trade.mqh>
#include "SignalClient.mqh"

enum ENUM_SIZING { SIZING_FIXED=0, SIZING_ATR=1, SIZING_KELLY=2 };

// NOTE: the text after each input is its DISPLAY LABEL in MT5.
// --- entry / exit ---
// Defaults = the OOS-validated robust GOLD set (StopATR 3 / hold 11 -> OOS PF 1.96, DD 18%).
// AUDJPY prefers a longer hold (~24); AUDUSD/NZDJPY did not survive OOS (see STRATEGIES.md).
input bool   TurnEntry       = true;            // Enter on the TURN (first up-bar after capitulation), not the first fall
input int    ArmWindow       = 5;               // Bars to wait for the turn (>=3 is irrelevant)
input double StopATR         = 3.0;             // Stop = StopATR x ATR (0 = no stop)
input int    MaxHoldDays     = 11;              // Max hold (TRADING days / bars)
// --- sizing ---
input ENUM_SIZING SizingMethod = SIZING_ATR;    // Position sizing method
input double RiskPctPerTrade = 1.0;             // ATR/Kelly base risk % of equity per trade
input int    AtrPeriod       = 14;              // ATR period (D1)
input double KellyFraction   = 0.5;             // Kelly: fraction of full Kelly (0.5 = half)
input double KellyCapPct     = 5.0;             // Kelly: max risk % per trade (cap)
input int    KellyMinTrades  = 20;              // Kelly: warmup trades (use base risk until then)
input double BaseLots        = 0.10;            // Fixed: lots per position
input int    MagicNumber     = 770077;          // Magic number
// --- tester ---
input string SignalFile      = "rrai_capitulation.csv";   // TESTER signal CSV (Common Files)
// --- live (ignored in the tester) ---
input string SignalsUrl      = "http://YOUR_HOST:8000/signals?format=mt5";  // LIVE feed URL
input string AuthBearerToken = "";              // LIVE bearer token (optional)
input string StrategyTag     = "retail_fear";   // LIVE strategy to trade
input int    PollSeconds     = 300;             // LIVE poll interval sec
input bool   ScaleByStrength = true;            // LIVE scale lots by strength
// --- LIVE basket: GOLD-LED, PER-INSTRUMENT params (each leg uses its own OOS-tuned hold/stop/risk).
//     AUDUSD/NZDJPY dropped (failed OOS). AUDJPY = small secondary at half risk. Set a symbol to "" to disable. ---
input string GoldSymbol      = "XAUUSD";        // Gold broker symbol (PRIMARY)
input double GoldStopATR     = 3.0;             //   gold: stop x ATR
input int    GoldHold        = 11;              //   gold: max hold (days)
input double GoldRisk        = 1.0;             //   gold: risk % per trade
input string AudJpySymbol    = "AUDJPY";        // AUDJPY broker symbol (secondary)
input double AudJpyStopATR   = 2.0;             //   AUDJPY: stop x ATR
input int    AudJpyHold      = 24;              //   AUDJPY: max hold (days, long)
input double AudJpyRisk      = 0.5;             //   AUDJPY: risk % per trade (small)

CTrade        trade;
CSignalClient client;
bool   g_tester = false;
string g_feed[4], g_broker[4];
double g_legStop[4];               // live: per-leg stop x ATR
int    g_legHold[4];               // live: per-leg max hold (days)
double g_legRisk[4];               // live: per-leg risk % per trade
int    g_n = 0;
datetime g_sig[];
int      g_sn = 0;
datetime g_entry = 0, g_lastBar = 0;
int      g_atr = INVALID_HANDLE;
int      g_atrLive[4];
int      g_held = 0;
bool     g_armed = false;            // capitulation seen, waiting for the turn
int      g_armBars = 0;
// Kelly bookkeeping (tester): realized win/loss stats so far
int      g_wins = 0, g_losses = 0;
double   g_grossWin = 0.0, g_grossLoss = 0.0, g_entryEquity = 0.0;
bool     g_wasInPos = false;

datetime FloorDay(datetime t) { return (t - (t % 86400)); }

double AtrVal(int atrh)
{
   double buf[];
   if(atrh == INVALID_HANDLE || CopyBuffer(atrh, 0, 1, 1, buf) != 1) return 0.0;
   return buf[0];
}

//==================== sizing ====================
// Kelly fraction from realized stats: f* = W - (1-W)/R, scaled by KellyFraction and capped.
double CurrentRiskPct()
{
   if(SizingMethod != SIZING_KELLY) return RiskPctPerTrade;
   int n = g_wins + g_losses;
   if(n < KellyMinTrades) return RiskPctPerTrade;                 // warmup
   double W = (double)g_wins / n;
   double avgW = (g_wins   > 0) ? g_grossWin  / g_wins   : 0.0;
   double avgL = (g_losses > 0) ? g_grossLoss / g_losses : 0.0;
   if(avgL <= 0.0) return RiskPctPerTrade;
   double R = avgW / avgL;
   double f = (W - (1.0 - W) / R) * KellyFraction;
   if(f <= 0.0) return 0.0;
   return MathMin(f * 100.0, KellyCapPct);
}

// lot such that a stop-out (stopMult x ATR) ~= riskPct% of equity. Fixed method -> BaseLots.
double SizedLot(string sym, int atrh, double riskPct, double stopMult)
{
   double minL = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double maxL = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   if(SizingMethod == SIZING_FIXED || atrh == INVALID_HANDLE || riskPct <= 0.0)
      return MathMax(minL, BaseLots);
   double atr = AtrVal(atrh);
   double tickVal = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   double tickSz  = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   if(atr <= 0.0 || tickVal <= 0.0 || tickSz <= 0.0)
      return MathMax(minL, BaseLots);
   double stopDist   = (stopMult > 0.0 ? stopMult : 1.0) * atr;   // risk measured to the stop
   double riskMoney  = AccountInfoDouble(ACCOUNT_EQUITY) * riskPct / 100.0;
   double riskPerLot = (stopDist / tickSz) * tickVal;
   double lots = (riskPerLot > 0.0) ? riskMoney / riskPerLot : minL;
   if(step > 0) lots = MathFloor(lots / step) * step;
   return MathMax(minL, MathMin(maxL, lots));
}

int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   g_tester = (bool)MQLInfoInteger(MQL_TESTER);
   if(g_tester)
   {
      g_atr = iATR(_Symbol, PERIOD_D1, AtrPeriod);
      return LoadSignalCsv();
   }
   client.Init(SignalsUrl, AuthBearerToken);
   g_n = 0;
   if(StringLen(GoldSymbol) > 0){
      g_feed[g_n]="XAUUSD"; g_broker[g_n]=GoldSymbol;
      g_legStop[g_n]=GoldStopATR; g_legHold[g_n]=GoldHold; g_legRisk[g_n]=GoldRisk; g_n++; }
   if(StringLen(AudJpySymbol) > 0){
      g_feed[g_n]="AUDJPY"; g_broker[g_n]=AudJpySymbol;
      g_legStop[g_n]=AudJpyStopATR; g_legHold[g_n]=AudJpyHold; g_legRisk[g_n]=AudJpyRisk; g_n++; }
   for(int i = 0; i < g_n; i++) g_atrLive[i] = iATR(g_broker[i], PERIOD_D1, AtrPeriod);
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
      Print("TESTER: cannot open ", SignalFile, " in Common\\Files (err ", GetLastError(), ").");
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
         double lots = SizedLot(sym, g_atrLive[i], g_legRisk[i], g_legStop[i])
                       * (ScaleByStrength ? MathMax(0.1, strength[i]) : 1.0);
         lots = SC_NormalizeLots(sym, lots);
         double atr = AtrVal(g_atrLive[i]);
         double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
         double sl  = (g_legStop[i] > 0 && atr > 0) ? ask - g_legStop[i] * atr : 0.0;
         if(lots > 0 && trade.Buy(lots, sym, 0.0, sl, 0.0)) Print("OPEN long ", sym, " lots=", lots);
      }
      else if(have && (!wantLong[i] || SC_PositionAgeDays(sym, MagicNumber) >= g_legHold[i]))
      {
         if(trade.PositionClose(sym))
            Print("CLOSE ", sym, (!wantLong[i] ? " (signal cleared)" : " (max hold)"));
      }
   }
}

//==================== TESTER ====================
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

// Turn = the last completed bar closed UP (the bounce has started). If TurnEntry off, always true.
bool TurnConfirmed()
{
   if(!TurnEntry) return true;
   double c1 = iClose(_Symbol, _Period, 1);
   double c2 = iClose(_Symbol, _Period, 2);
   return (c1 > 0 && c2 > 0 && c1 > c2);
}

void OnTick()
{
   if(!g_tester) return;
   datetime bt = iTime(_Symbol, _Period, 0);
   if(bt == g_lastBar) return;            // act once per new bar
   g_lastBar = bt;
   datetime day = FloorDay(bt);

   bool inpos = HasPosSym();
   // Kelly bookkeeping: a position just closed -> record its realized P&L
   if(g_wasInPos && !inpos)
   {
      double pnl = AccountInfoDouble(ACCOUNT_EQUITY) - g_entryEquity;
      if(pnl >= 0) { g_wins++;   g_grossWin  += pnl;  }
      else         { g_losses++; g_grossLoss += -pnl; }
   }
   g_wasInPos = inpos;

   if(inpos)
   {
      g_held++;
      if(g_held >= MaxHoldDays) trade.PositionClose(_Symbol);   // time exit (stop handles downside)
      return;
   }

   if(IsSignalDay(day)) { g_armed = true; g_armBars = 0; }      // arm on capitulation

   if(g_armed)
   {
      g_armBars++;
      if(TurnConfirmed())
      {
         double lots = SizedLot(_Symbol, g_atr, CurrentRiskPct(), StopATR);
         double atr  = AtrVal(g_atr);
         double ask  = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         double sl   = (StopATR > 0 && atr > 0) ? ask - StopATR * atr : 0.0;
         if(lots > 0 && trade.Buy(lots, _Symbol, 0.0, sl, 0.0))
         {
            g_held = 0; g_armed = false;
            g_entryEquity = AccountInfoDouble(ACCOUNT_EQUITY);
         }
      }
      else if(g_armBars > ArmWindow)
         g_armed = false;                                       // turn didn't come -> stand down
   }
}

//==================== TESTER / OPTIMIZER: results ====================
// Single run -> rrai_tester_result.txt. Optimization -> every pass is sent as a FRAME and the
// main terminal appends it to rrai_opt_log.csv (FrameAdd/OnTesterPass = the headless-safe path).
double OnTester()
{
   double prof = TesterStatistics(STAT_PROFIT);
   if(MQLInfoInteger(MQL_OPTIMIZATION))
   {
      double m[12];
      m[0]=ArmWindow; m[1]=StopATR; m[2]=MaxHoldDays; m[3]=(double)SizingMethod; m[4]=RiskPctPerTrade;
      m[5]=AtrPeriod; m[6]=(TurnEntry ? 1.0 : 0.0);
      m[7]=TesterStatistics(STAT_TRADES); m[8]=prof; m[9]=TesterStatistics(STAT_PROFIT_FACTOR);
      m[10]=TesterStatistics(STAT_EQUITYDD_PERCENT); m[11]=TesterStatistics(STAT_SHARPE_RATIO);
      FrameAdd("R", 0, prof, m);
      return prof;
   }
   int trades = (int)TesterStatistics(STAT_TRADES);
   int wins   = (int)TesterStatistics(STAT_PROFIT_TRADES);
   double winrate = (trades > 0) ? 100.0 * wins / trades : 0.0;
   string sizing = (SizingMethod==SIZING_FIXED ? "FIXED" : (SizingMethod==SIZING_ATR ? "ATR" : "KELLY"));
   int h = FileOpen("rrai_tester_result.txt", FILE_COMMON | FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h != INVALID_HANDLE)
   {
      FileWrite(h, "symbol=" + _Symbol);
      FileWrite(h, "sizing=" + sizing);
      FileWrite(h, "turn_entry=" + (TurnEntry ? "on" : "off"));
      FileWrite(h, "stop_atr=" + DoubleToString(StopATR, 1));
      FileWrite(h, "signals_loaded=" + IntegerToString(g_sn));
      FileWrite(h, "trades=" + IntegerToString(trades));
      FileWrite(h, "win_rate_pct=" + DoubleToString(winrate, 1));
      FileWrite(h, "net_profit=" + DoubleToString(prof, 2));
      FileWrite(h, "profit_factor=" + DoubleToString(TesterStatistics(STAT_PROFIT_FACTOR), 2));
      FileWrite(h, "expected_payoff=" + DoubleToString(TesterStatistics(STAT_EXPECTED_PAYOFF), 2));
      FileWrite(h, "max_equity_dd_pct=" + DoubleToString(TesterStatistics(STAT_EQUITYDD_PERCENT), 2));
      FileWrite(h, "sharpe=" + DoubleToString(TesterStatistics(STAT_SHARPE_RATIO), 2));
      FileClose(h);
   }
   return prof;
}

// Optimization start (main terminal): truncate the log + write a header.
int OnTesterInit()
{
   int h = FileOpen("rrai_opt_log.csv", FILE_COMMON|FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(h != INVALID_HANDLE)
   {
      FileWrite(h, "arm","stopatr","hold","sizing","risk","atrp","turn","trades","net","pf","dd","sharpe");
      FileClose(h);
   }
   return(INIT_SUCCEEDED);
}

// Each optimization pass arrives here on the MAIN terminal (single-threaded) -> safe append.
void OnTesterPass()
{
   ulong pass; string name; long id; double val; double data[];
   while(FrameNext(pass, name, id, val, data))
   {
      if(name != "R" || ArraySize(data) < 12) continue;
      int h = FileOpen("rrai_opt_log.csv", FILE_COMMON|FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
      if(h != INVALID_HANDLE)
      {
         FileSeek(h, 0, SEEK_END);
         FileWrite(h, (int)data[0], data[1], (int)data[2], (int)data[3], data[4], (int)data[5], (int)data[6],
                      (int)data[7], data[8], data[9], data[10], data[11]);
         FileClose(h);
      }
   }
}

// Required whenever OnTesterInit is defined (nothing to clean up here).
void OnTesterDeinit() { }
//+------------------------------------------------------------------+
