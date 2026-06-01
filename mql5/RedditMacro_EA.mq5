//+------------------------------------------------------------------+
//|  RedditMacro_EA.mq5                                             |
//|  Multi-strategy execution client for the Reddit macro overlay.  |
//|                                                                  |
//|  Picks ONE strategy from the /signals feed (StrategyTag) and     |
//|  mirrors it on the mapped FX/index CFD symbols. Computes no       |
//|  alpha - all logic is server-side. Run one chart per strategy.   |
//|                                                                  |
//|  Strategies the feed currently serves:                          |
//|    retail_fear  (VALIDATED) - long risk when retail capitulates   |
//|                  AND VIX>=18. Modest timing tilt over buy-&-hold. |
//|  (Momentum/froth candidates had NO edge vs buy-&-hold and are     |
//|   not served. Equity pump strategies can't trade on MT5.)        |
//|                                                                  |
//|  SETUP: compile to Experts/; Tools>Options>Expert Advisors>       |
//|  Allow WebRequest for your host; attach to any chart; DEMO ONLY. |
//+------------------------------------------------------------------+
#property strict
#include <Trade/Trade.mqh>
#include "SignalClient.mqh"

input string SignalsUrl      = "http://YOUR_HOST:8000/signals?format=mt5";
input string AuthBearerToken = "";
input string StrategyTag     = "retail_fear";   // which feed strategy to trade
input int    PollSeconds     = 300;
input double BaseLots        = 0.10;
input bool   ScaleByStrength = true;
input int    MaxHoldDays     = 10;              // matches the signal horizon
input int    MagicNumber     = 770077;
// Map feed symbols -> YOUR broker's names (blank = skip that instrument).
input string FxSymbol        = "AUDJPY";        // feed "AUDJPY" (strongest leg)
input string IndexSymbol     = "US500";         // feed "US500"
input string TechSymbol      = "USTEC";         // feed "USTEC"

CTrade        trade;
CSignalClient client;
string g_feed[3], g_broker[3];
int    g_n = 0;

int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   client.Init(SignalsUrl, AuthBearerToken);
   g_n = 0;
   if(StringLen(FxSymbol)    > 0){ g_feed[g_n]="AUDJPY"; g_broker[g_n]=FxSymbol;    g_n++; }
   if(StringLen(IndexSymbol) > 0){ g_feed[g_n]="US500";  g_broker[g_n]=IndexSymbol; g_n++; }
   if(StringLen(TechSymbol)  > 0){ g_feed[g_n]="USTEC";  g_broker[g_n]=TechSymbol;  g_n++; }
   EventSetTimer(MathMax(10, PollSeconds));
   Print("RedditMacro_EA started; strategy=", StrategyTag, " url=", SignalsUrl);
   OnTimer();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason){ EventKillTimer(); }

void OnTimer()
{
   SignalRow rows[];
   if(!client.Fetch(rows, StrategyTag)) return;   // network problem -> leave positions

   bool wantLong[3]; double strength[3];
   for(int i=0;i<g_n;i++){ wantLong[i]=false; strength[i]=0.0; }
   for(int r=0; r<ArraySize(rows); r++)
      for(int i=0;i<g_n;i++)
         if(rows[r].symbol==g_feed[i] && rows[r].side=="long")
         { wantLong[i]=true; strength[i]=rows[r].strength; }

   for(int i=0;i<g_n;i++)
   {
      string sym = g_broker[i];
      bool   have = SC_HasPosition(sym, MagicNumber);
      if(wantLong[i] && !have)
      {
         double lots = SC_NormalizeLots(sym, BaseLots*(ScaleByStrength?MathMax(0.1,strength[i]):1.0));
         if(lots>0 && trade.Buy(lots, sym)) Print("OPEN long ", sym, " lots=", lots);
      }
      else if(have && (!wantLong[i] || SC_PositionAgeDays(sym,MagicNumber) >= MaxHoldDays))
      {
         if(trade.PositionClose(sym))
            Print("CLOSE ", sym, (!wantLong[i] ? " (signal cleared)" : " (max hold)"));
      }
   }
}
//+------------------------------------------------------------------+
