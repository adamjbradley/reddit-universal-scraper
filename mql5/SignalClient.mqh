//+------------------------------------------------------------------+
//|  SignalClient.mqh                                               |
//|  Shared plumbing for Reddit-signal EAs: fetch + parse the        |
//|  /signals?format=mt5 feed and reconcile positions.              |
//|                                                                  |
//|  Feed line format:  STRATEGY,SYMBOL,SIDE,STRENGTH,HORIZON        |
//|  (a single "# flat" line means no active signal).                |
//+------------------------------------------------------------------+
#property strict

struct SignalRow
{
   string strategy;   // e.g. "retail_fear"
   string symbol;     // feed symbol: AUDJPY / US500 / USTEC
   string side;       // "long" | "short"
   double strength;   // 0..1
   int    horizon;    // days
};

//+------------------------------------------------------------------+
//| Polls the feed; keeps rows matching filterStrategy ("" = all).   |
//+------------------------------------------------------------------+
class CSignalClient
{
private:
   string m_url;
   string m_token;
public:
   void Init(const string url, const string token){ m_url=url; m_token=token; }

   bool Fetch(SignalRow &rows[], const string filterStrategy)
   {
      ArrayResize(rows, 0);
      char post[], result[];
      string resHeaders;
      string headers = (StringLen(m_token) > 0)
                       ? "Authorization: Bearer " + m_token + "\r\n" : "";
      ResetLastError();
      int code = WebRequest("GET", m_url, headers, 5000, post, result, resHeaders);
      if(code == -1)
      {
         Print("WebRequest err ", GetLastError(),
               " - add the host in Tools>Options>Expert Advisors>Allow WebRequest");
         return(false);
      }
      if(code != 200){ Print("Feed HTTP ", code); return(false); }

      string body = CharArrayToString(result);
      string lines[];
      int nl = StringSplit(body, '\n', lines);
      for(int i=0; i<nl; i++)
      {
         string ln = lines[i];
         StringTrimLeft(ln); StringTrimRight(ln);
         if(StringLen(ln)==0 || StringGetCharacter(ln,0)=='#') continue;
         string f[];
         if(StringSplit(ln, ',', f) < 5) continue;
         if(filterStrategy != "" && f[0] != filterStrategy) continue;
         SignalRow r;
         r.strategy = f[0]; r.symbol = f[1]; r.side = f[2];
         r.strength = StringToDouble(f[3]); r.horizon = (int)StringToInteger(f[4]);
         int n = ArraySize(rows); ArrayResize(rows, n+1); rows[n] = r;
      }
      return(true);
   }
};

//--- position helpers (scoped by magic number) ---------------------
bool SC_HasPosition(const string sym, const long magic)
{
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      if(PositionGetTicket(i)==0) continue;
      if(PositionGetString(POSITION_SYMBOL)==sym &&
         PositionGetInteger(POSITION_MAGIC)==magic) return(true);
   }
   return(false);
}

double SC_PositionAgeDays(const string sym, const long magic)
{
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      if(PositionGetTicket(i)==0) continue;
      if(PositionGetString(POSITION_SYMBOL)==sym &&
         PositionGetInteger(POSITION_MAGIC)==magic)
         return((double)(TimeCurrent()-(datetime)PositionGetInteger(POSITION_TIME))/86400.0);
   }
   return(0.0);
}

double SC_NormalizeLots(const string sym, double lots)
{
   double mn = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double mx = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   double st = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   if(st > 0) lots = MathRound(lots/st)*st;
   return(MathMax(mn, MathMin(mx, lots)));
}
//+------------------------------------------------------------------+
