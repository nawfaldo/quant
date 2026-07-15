#property strict
#property version   "1.00"
#property description "Thin execution bridge for the March Rust backend. Contains no strategy logic."

input string BackendUrl = "http://127.0.0.1:8080";
input string BridgeToken = "";
input int PollIntervalMs = 500;
input int HttpTimeoutMs = 5000;
input int PositionReportMs = 2000;
input ulong MagicNumber = 26032026;

string PendingResultBody = "";
ulong LastPositionReport = 0;
ulong ActiveMagic = MagicNumber;

int OnInit()
{
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      Print("March bridge: automated trading is currently disabled");
   EventSetMillisecondTimer(MathMax(PollIntervalMs, 200));
   Print("March execution bridge started for login ", AccountInfoInteger(ACCOUNT_LOGIN));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTimer()
{
   if(PendingResultBody != "")
   {
      if(PostJson("/api/march/mt5/bridge/result", PendingResultBody))
         PendingResultBody = "";
      else
         return;
   }

   ReportPositionsWhenDue();

   string poll = "{\"token\":\"" + JsonEscape(BridgeToken) +
                 "\",\"login\":\"" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) +
                 "\",\"server\":\"" + JsonEscape(AccountInfoString(ACCOUNT_SERVER)) +
                 "\",\"balance\":" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) +
                 ",\"equity\":" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) +
                 ",\"currency\":\"" + JsonEscape(AccountInfoString(ACCOUNT_CURRENCY)) + "\"}";
   string response;
   int status = Http("POST", "/api/march/mt5/bridge/poll", poll, response);
   if(status < 200 || status >= 300 || response == "NONE" || response == "")
      return;

   string fields[];
   if(StringSplit(response, StringGetCharacter("|", 0), fields) != 7 || fields[0] != "ORDER")
   {
      Print("March bridge: invalid command: ", response);
      return;
   }

   long commandId = (long)StringToInteger(fields[1]);
   string action = fields[2];
   string symbol = fields[3];
   double volume = StringToDouble(fields[4]);
   ulong magic = (ulong)StringToInteger(fields[5]);
   ActiveMagic = magic;
   int deviation = (int)StringToInteger(fields[6]);

   double entryPrice = 0.0, entrySpread = 0.0, closePrice = 0.0;
   ulong ticket = 0;
   long fillTime = TimeTradeServer();
   string error = "";
   bool filled = ExecuteCommand(commandId, action, symbol, volume, magic, deviation,
                                entryPrice, entrySpread, closePrice, ticket, fillTime, error);

   PendingResultBody = "{\"token\":\"" + JsonEscape(BridgeToken) +
      "\",\"login\":\"" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) +
      "\",\"command_id\":" + IntegerToString(commandId) +
      ",\"status\":\"" + (filled ? "filled" : "failed") +
      "\",\"ticket\":" + IntegerToString((long)ticket) +
      ",\"entry_price\":" + DoubleToString(entryPrice, 10) +
      ",\"entry_spread\":" + DoubleToString(entrySpread, 10) +
      ",\"close_price\":" + DoubleToString(closePrice, 10) +
      ",\"fill_time\":" + IntegerToString(fillTime) +
      ",\"error\":\"" + JsonEscape(error) + "\"}";

   if(PostJson("/api/march/mt5/bridge/result", PendingResultBody))
      PendingResultBody = "";
}

bool ExecuteCommand(const long commandId, const string action, const string symbol,
                    const double requestedVolume, const ulong magic, const int deviation,
                    double &entryPrice, double &entrySpread, double &closePrice,
                    ulong &ticket, long &fillTime, string &error)
{
   if(action != "long" && action != "short" && action != "close" && action != "flat")
   {
      error = "unknown action " + action;
      return false;
   }
   if(!SymbolSelect(symbol, true))
   {
      error = "symbol is unavailable: " + symbol;
      return false;
   }

   string comment = "march:" + IntegerToString(commandId);
   if(RecoverFill(comment, action, entryPrice, closePrice, ticket, fillTime))
      return true;

   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong positionTicket = PositionGetTicket(i);
      if(positionTicket == 0 || !PositionSelectByTicket(positionTicket))
         continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != magic ||
         PositionGetString(POSITION_SYMBOL) != symbol)
         continue;
      double filledPrice = 0.0;
      long dealTime = fillTime;
      if(!ClosePosition(positionTicket, symbol, magic, deviation, comment,
                        filledPrice, ticket, dealTime, error))
         return false;
      closePrice = filledPrice;
      fillTime = dealTime;
   }

   if(action == "close" || action == "flat")
      return true;

   MqlTick tickData;
   if(!SymbolInfoTick(symbol, tickData))
   {
      error = "no tick available for " + symbol;
      return false;
   }
   entrySpread = tickData.ask - tickData.bid;
   double volume = NormalizeVolume(symbol, requestedVolume);
   if(volume <= 0.0)
   {
      error = "invalid volume";
      return false;
   }
   ENUM_ORDER_TYPE orderType = action == "long" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double price = orderType == ORDER_TYPE_BUY ? tickData.ask : tickData.bid;
   return SendDeal(symbol, orderType, volume, price, 0, magic, deviation, comment,
                   entryPrice, ticket, fillTime, error);
}

bool ClosePosition(const ulong positionTicket, const string symbol, const ulong magic,
                   const int deviation, const string comment, double &price,
                   ulong &dealTicket, long &fillTime, string &error)
{
   if(!PositionSelectByTicket(positionTicket))
      return true;
   ENUM_POSITION_TYPE positionType = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
   ENUM_ORDER_TYPE orderType = positionType == POSITION_TYPE_BUY ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
   MqlTick tickData;
   if(!SymbolInfoTick(symbol, tickData))
   {
      error = "no tick available while closing " + symbol;
      return false;
   }
   double requestedPrice = orderType == ORDER_TYPE_BUY ? tickData.ask : tickData.bid;
   double volume = PositionGetDouble(POSITION_VOLUME);
   return SendDeal(symbol, orderType, volume, requestedPrice, positionTicket, magic,
                   deviation, comment, price, dealTicket, fillTime, error);
}

bool SendDeal(const string symbol, const ENUM_ORDER_TYPE orderType, const double volume,
              const double requestedPrice, const ulong positionTicket, const ulong magic,
              const int deviation, const string comment, double &filledPrice,
              ulong &dealTicket, long &fillTime, string &error)
{
   MqlTradeRequest request = {};
   MqlTradeResult result = {};
   request.action = TRADE_ACTION_DEAL;
   request.symbol = symbol;
   request.volume = volume;
   request.type = orderType;
   request.position = positionTicket;
   request.price = requestedPrice;
   request.deviation = deviation;
   request.magic = magic;
   request.comment = comment;
   request.type_time = ORDER_TIME_GTC;
   request.type_filling = FillingMode(symbol);
   if(!OrderSend(request, result) ||
      (result.retcode != TRADE_RETCODE_DONE && result.retcode != TRADE_RETCODE_DONE_PARTIAL))
   {
      error = "OrderSend retcode=" + IntegerToString((long)result.retcode) + " " + result.comment;
      return false;
   }
   filledPrice = result.price > 0.0 ? result.price : requestedPrice;
   dealTicket = result.deal;
   fillTime = TimeTradeServer();
   return true;
}

bool RecoverFill(const string comment, const string action, double &entryPrice,
                 double &closePrice, ulong &ticket, long &fillTime)
{
   if(!HistorySelect(0, TimeCurrent()))
      return false;
   bool foundEntry = false, foundClose = false;
   for(int i = HistoryDealsTotal() - 1; i >= 0; --i)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0 || HistoryDealGetString(deal, DEAL_COMMENT) != comment)
         continue;
      ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal, DEAL_ENTRY);
      double price = HistoryDealGetDouble(deal, DEAL_PRICE);
      if(entry == DEAL_ENTRY_IN || entry == DEAL_ENTRY_INOUT)
      {
         entryPrice = price;
         foundEntry = true;
      }
      if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY || entry == DEAL_ENTRY_INOUT)
      {
         closePrice = price;
         foundClose = true;
      }
      ticket = deal;
      fillTime = HistoryDealGetInteger(deal, DEAL_TIME);
   }
   return (action == "long" || action == "short") ? foundEntry : foundClose;
}

double NormalizeVolume(const string symbol, const double requested)
{
   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   double minimum = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maximum = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0.0 || minimum <= 0.0 || maximum <= 0.0 || requested <= 0.0)
      return 0.0;
   double volume = MathRound(requested / step) * step;
   volume = MathMax(minimum, MathMin(maximum, volume));
   int digits = 0;
   double scaled = step;
   while(digits < 8 && MathRound(scaled) != scaled)
   {
      scaled *= 10.0;
      ++digits;
   }
   return NormalizeDouble(volume, digits);
}

ENUM_ORDER_TYPE_FILLING FillingMode(const string symbol)
{
   long modes = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
   if((modes & SYMBOL_FILLING_IOC) == SYMBOL_FILLING_IOC)
      return ORDER_FILLING_IOC;
   if((modes & SYMBOL_FILLING_FOK) == SYMBOL_FILLING_FOK)
      return ORDER_FILLING_FOK;
   return ORDER_FILLING_RETURN;
}

void ReportPositionsWhenDue()
{
   ulong now = GetTickCount64();
   if(now - LastPositionReport < (ulong)MathMax(PositionReportMs, 500))
      return;
   LastPositionReport = now;
   string body = "{\"token\":\"" + JsonEscape(BridgeToken) +
                 "\",\"login\":\"" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) +
                 "\",\"server\":\"" + JsonEscape(AccountInfoString(ACCOUNT_SERVER)) +
                 "\",\"positions\":[";
   bool first = true;
   for(int i = 0; i < PositionsTotal(); ++i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != ActiveMagic)
         continue;
      string type = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? "long" : "short";
      if(!first) body += ",";
      first = false;
      body += "{\"ticket\":" + IntegerToString((long)ticket) +
              ",\"type\":\"" + type +
              "\",\"symbol\":\"" + JsonEscape(PositionGetString(POSITION_SYMBOL)) +
              "\",\"volume\":" + DoubleToString(PositionGetDouble(POSITION_VOLUME), 8) +
              ",\"profit\":" + DoubleToString(PositionGetDouble(POSITION_PROFIT), 8) +
              ",\"open_price\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), 10) +
              ",\"open_time\":" + IntegerToString(PositionGetInteger(POSITION_TIME)) + "}";
   }
   body += "]}";
   PostJson("/api/march/mt5/bridge/positions", body);
}

bool PostJson(const string path, const string body)
{
   string response;
   int status = Http("POST", path, body, response);
   if(status >= 200 && status < 300)
      return true;
   Print("March bridge HTTP ", status, " for ", path, ": ", response);
   return false;
}

int Http(const string method, const string path, const string body, string &response)
{
   char data[];
   char result[];
   string resultHeaders;
   int copied = StringToCharArray(body, data, 0, -1, CP_UTF8);
   if(copied > 0)
      ArrayResize(data, copied - 1);
   ResetLastError();
   int status = WebRequest(method, BackendUrl + path,
      "Content-Type: application/json\r\n", HttpTimeoutMs, data, result, resultHeaders);
   response = CharArrayToString(result, 0, -1, CP_UTF8);
   if(status == -1)
      Print("March bridge WebRequest failed: ", GetLastError(),
            ". Add ", BackendUrl, " to Tools > Options > Expert Advisors > allowed WebRequest URLs.");
   return status;
}

string JsonEscape(string value)
{
   StringReplace(value, "\\", "\\\\");
   StringReplace(value, "\"", "\\\"");
   StringReplace(value, "\r", "\\r");
   StringReplace(value, "\n", "\\n");
   return value;
}
