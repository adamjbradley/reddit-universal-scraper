//+------------------------------------------------------------------+
//|  RedditRRAI_EA.mq5                                               |
//|  Thin execution client for the Reddit RRAI capitulation overlay. |
//|                                                                  |
//|  It computes NOTHING. It polls the project's /signals feed and   |
//|  mirrors it: go LONG risk (AUDJPY / index CFDs) while the feed    |
//|  reports an active capitulation signal, flat otherwise. All the  |
//|  alpha lives server-side; this just executes.                    |
//|                                                                  |
//|  SETUP (required):                                               |
//|   1. MetaEditor: compile this into Experts/.                     |
//|   2. Terminal: Tools > Options > Expert Advisors >               |
//|      "Allow WebRequest for listed URL" and ADD your host         |
//|      (e.g. http://192.168.1.50:8000).                            |
//|   3. Attach to ANY chart; set SignalsUrl + broker symbol names.  |
//|   4. TEST ON A DEMO ACCOUNT. Validation lives in the Python      |
//|      backtester; MT5 is forward/demo execution only.             |
//+------------------------------------------------------------------+
#property strict
#include <Trade/Trade.mqh>

input string SignalsUrl       = "http://YOUR_HOST:8000/signals?format=mt5";
input string AuthBearerToken  = "";        // optional; only if the API requires Bearer auth
input int    PollSeconds      = 300;       // how often to poll the feed
input double BaseLots         = 0.10;      // lot size at full strength
input bool   ScaleByStrength  = true;      // multiply lots by signal strength (0..1)
input int    MaxHoldDays      = 10;        // force-exit after N days (matches the signal horizon)
input int    MagicNumber      = 770077;
// Map the feed's generic symbols to YOUR broker's symbol names. Blank = don't trade it.
input string FxSymbol         = "AUDJPY";  // feed "AUDJPY"  (the strongest leg)
input string IndexSymbol      = "US500";   // feed "US500"   (S&P 500 CFD; broker-specific)
input string TechSymbol       = "USTEC";   // feed "USTEC"   (Nasdaq 100 CFD; broker-specific)

CTrade trade;

// feed-symbol -> broker-symbol pairs (filled in OnInit)
string  g_feedSym[3];
string  g_brokerSym[3];
int     g_n = 0;

int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   g_n = 0;
   if(StringLen(FxSymbol)    > 0){ g_feedSym[g_n]="AUDJPY"; g_brokerSym[g_n]=FxSymbol;    g_n++; }
   if(StringLen(IndexSymbol) > 0){ g_feedSym[g_n]="US500";  g_brokerSym[g_n]=IndexSymbol; g_n++; }
   if(StringLen(TechSymbol)  > 0){ g_feedSym[g_n]="USTEC";  g_brokerSym[g_n]=TechSymbol;  g_n++; }
   EventSetTimer(MathMax(10, PollSeconds));
   Print("RedditRRAI_EA started. Polling ", SignalsUrl, " every ", PollSeconds, "s");
   OnTimer();   // act immediately on attach
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason){ EventKillTimer(); }

//+------------------------------------------------------------------+
//| Poll the feed and reconcile positions to it                      |
//+------------------------------------------------------------------+
void OnTimer()
{
   string body;
   if(!FetchSignals(body)) return;            // network problem -> leave positions as-is

   // Parse the feed: lines of "SYMBOL,SIDE,STRENGTH,HORIZON". Collect active longs + strength.
   bool   wantLong[3]; double strength[3];
   for(int i=0;i<g_n;i++){ wantLong[i]=false; strength[i]=0.0; }

   string lines[];
   int nl = StringSplit(body, '\n', lines);
   for(int l=0; l<nl; l++)
   {
      string ln = lines[l];
      StringTrimLeft(ln); StringTrimRight(ln);
      if(StringLen(ln)==0 || StringGetCharacter(ln,0)=='#') continue;   // blank / "# flat"
      string f[]; int nf = StringSplit(ln, ',', f);
      if(nf < 4) continue;
      for(int i=0;i<g_n;i++)
         if(f[0]==g_feedSym[i] && f[1]=="long")
         { wantLong[i]=true; strength[i]=StringToDouble(f[2]); }
   }

   // Reconcile each configured instrument.
   for(int i=0;i<g_n;i++)
   {
      string sym = g_brokerSym[i];
      bool   have = HasPosition(sym);
      if(wantLong[i] && !have)
      {
         double lots = BaseLots * (ScaleByStrength ? MathMax(0.1, strength[i]) : 1.0);
         lots = NormalizeLots(sym, lots);
         if(lots > 0 && trade.Buy(lots, sym))
            Print("OPEN long ", sym, " lots=", lots, " strength=", strength[i]);
      }
      else if(have && (!wantLong[i] || PositionAgeDays(sym) >= MaxHoldDays))
      {
         if(trade.PositionClose(sym))
            Print("CLOSE ", sym, (!wantLong[i] ? " (signal cleared)" : " (max hold)"));
      }
   }
}

//+------------------------------------------------------------------+
//| HTTP GET the signals feed (plain-text mt5 format)                |
//+------------------------------------------------------------------+
bool FetchSignals(string &out)
{
   char post[], result[];
   string resHeaders;
   string headers = (StringLen(AuthBearerToken)>0)
                    ? "Authorization: Bearer "+AuthBearerToken+"\r\n" : "";
   ResetLastError();
   int code = WebRequest("GET", SignalsUrl, headers, 5000, post, result, resHeaders);
   if(code == -1)
   {
      Print("WebRequest failed err=", GetLastError(),
            " - add the host to Tools>Options>Expert Advisors>Allow WebRequest");
      return(false);
   }
   if(code != 200){ Print("Feed HTTP ", code); return(false); }
   out = CharArrayToString(result);
   return(true);
}

//+------------------------------------------------------------------+
//| Position helpers (scoped to this EA's magic number)              |
//+------------------------------------------------------------------+
bool HasPosition(string sym)
{
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)==sym &&
         PositionGetInteger(POSITION_MAGIC)==MagicNumber) return(true);
   }
   return(false);
}

double PositionAgeDays(string sym)
{
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)==sym &&
         PositionGetInteger(POSITION_MAGIC)==MagicNumber)
         return((double)(TimeCurrent()-(datetime)PositionGetInteger(POSITION_TIME))/86400.0);
   }
   return(0.0);
}

double NormalizeLots(string sym, double lots)
{
   double minl = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double maxl = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   if(step>0) lots = MathRound(lots/step)*step;
   lots = MathMax(minl, MathMin(maxl, lots));
   return(lots);
}
//+------------------------------------------------------------------+
