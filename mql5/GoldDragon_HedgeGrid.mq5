//+------------------------------------------------------------------------+
//|                                       GoldDragon_HedgeGrid.mq5          |
//|  شبكة تحوّط Buy Stop / Sell Stop لـ XAUUSD — نسخة MQL5 مستقلة           |
//|  تعمل بنفس منطق src/strategies/grid_hedge.py في هذا المستودع بالضبط،    |
//|  لتشغيل مباشر من MetaTrader 5 كما تُظهر لقطات الشاشة المرجعية.          |
//+------------------------------------------------------------------------+
//
// الفكرة: أمر Buy Stop فوق السعر وأمر Sell Stop تحته في آن واحد. كل ما ينضرب
// أمر تُفتح صفقة بحجم ثابت (لا تدرّج/مارتينغيل) وتُمدَّد الشبكة بأمر جديد
// بنفس الاتجاه أبعد بخطوة واحدة، والجانب المقابل يبقى كما هو دون لمسه.
// تُغلق كل صفقات "السلة" معاً عند بلوغ ربح عائم إجمالي (Basket TP).
//
// تربح هذه الفكرة من التذبذب العرضي (Sideways) وتخسر بعنف في اختراق
// اتجاهي قوي بلا فلتر — لهذا كل حدود الحماية أدناه إلزامية وليست اختيارية:
//
//   1) MaxLevels            — سقف مطلق على عدد الصفقات+الأوامر المعلّقة معاً.
//   2) MaxBasketLossPercent — قطع مبكر لهذه السلة فقط عند خسارة عائمة بنسبة
//                             من الرصيد، قبل أن تصل الخسارة اليومية لحدها.
//   3) DailyLossLimitPercent / WeeklyLossLimitPercent / MaxDrawdownPercent /
//      ConsecutiveLossesLock — نفس حدود risk.yaml في نسخة بايثون من هذا
//      المشروع، بمنطق مطابق: خسارة يومية تتجاوز الحد تُغلق كل شيء وتقفل
//      التداول حتى منتصف الليل (وقت السيرفر)، لا حتى انتهاء صلاحية عملية
//      الطرفية — الحالة محفوظة في GlobalVariables فتنجو من إعادة تشغيل EA.
//   4) TrendGuard            — أقصى عدد صفقات متتالية بنفس الاتجاه قبل
//                             إيقاف تمديد تلك الجهة فقط؛ الجهة المقابلة
//                             تبقى مسلّحة. هذا هو الفرق الفعلي بين شبكة
//                             "تحرق" الحساب في اختراق قوي وشبكة تكبح توسّعها.
//
// تحذير إلزامي: أداة تعليمية/تقنية. اختبرها Demo لا يقل عن 3 أشهر قبل أي
// حساب حقيقي، وتأكد من مواصفات العقد (Point/ContractSize) لدى وسيطك.
//
// راجعها Claude يدوياً (بلا MetaEditor فعلي — لا بديل عن تصريفها فعلاً في
// طرفيتك قبل أي تشغيل): أُصلح خطأ توقف التصريف بالكامل (EnsureSide كانت
// تشير لمصفوفة من نطاق دالة أخرى)، وخطأ في CloseBasketAndCancelPending كان
// يُسجّل ربح/خسارة مركز فشل إغلاقه فعلياً كأنه أُغلق (يُحتسب مرتين لاحقاً)،
// وأُضيف رقم الحساب لبادئة GlobalVariables (كانت بلا هذا التمييز تتشارك
// حالة القفل/الخسارة اليومية بين أي حسابين بنفس الرمز والـ Magic على نفس
// الطرفية). لاحظ أيضاً: هذه النسخة لا تملك مكافئاً لطبقة KillSwitch
// النهائية (terminate) في بايثون — فقط قفل مؤقت يُفكّ تلقائياً بعد ساعات.

#property copyright "Gold Dragon Trading Bot Guide"
#property link      ""
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

//──────────────────────────────── الإعدادات ────────────────────────────────
input group "== الشبكة =="
input double InpLotPerLevel         = 0.01;   // حجم ثابت لكل مستوى — بلا تدرّج
input double InpGridStepUSD         = 3.0;    // مسافة $ بين كل مستوى وما يليه
input int    InpMaxLevels           = 12;     // سقف كلي: صفقات مفتوحة + أوامر معلّقة

input group "== هدف ربح السلة =="
input double InpBasketTargetUSD     = 5.0;    // إغلاق كل السلة عند هذا الربح العائم

input group "== حماية =="
input double InpMaxBasketLossPercent  = 3.0;   // % من الرصيد — قطع مبكر لهذه السلة فقط
input double InpDailyLossLimitPercent = 5.0;   // % من الرصيد — إيقاف حتى منتصف الليل (سيرفر)
input double InpWeeklyLossLimitPercent= 10.0;  // % من الرصيد — إيقاف أسبوع كامل
input double InpMaxDrawdownPercent    = 15.0;  // % من القمة التاريخية — إيقاف حتى يتعافى
input int    InpConsecutiveLossesLock = 3;     // عدد خسارات متتالية يقفل التداول
input int    InpConsecutiveLossesLockHours = 48;

input group "== فلتر الاتجاه =="
input bool   InpTrendGuardEnabled     = true;
input int    InpMaxDirectionalLevels  = 5;     // أقصى صفقات متتالية بنفس الاتجاه

input group "== عام =="
input int    InpMagic               = 20260816;
input int    InpSlippagePoints      = 20;
input bool   InpReinitAfterClose    = true;    // إعادة بناء شبكة جديدة تلقائياً بعد إغلاق السلة

//──────────────────────────────── حالة داخلية ────────────────────────────────
CTrade trade;
string gPrefix; // بادئة أسماء GlobalVariables — فريدة لكل رمز/Magic حتى لا تتصادم عدة نسخ

//+------------------------------------------------------------------------+
//| أدوات GlobalVariables — نفس دور RiskManager._save/_load في بايثون:     |
//| حالة يومية/أسبوعية/قفل تنجو من إعادة تشغيل EA أو الطرفية.               |
//+------------------------------------------------------------------------+
string GVName(const string key) { return gPrefix + key; }

double GVGet(const string key, const double def)
{
   string name = GVName(key);
   if(GlobalVariableCheck(name))
      return GlobalVariableGet(name);
   return def;
}

void GVSet(const string key, const double value)
{
   GlobalVariableSet(GVName(key), value);
}

// يوم/أسبوع بتوقيت السيرفر كعدد صحيح — أبسط من مقارنة تواريخ نصية
long DayStamp() { return (long)(TimeCurrent() / 86400); }
long WeekStamp() { return (long)(TimeCurrent() / (86400 * 7)); }

//+------------------------------------------------------------------------+
//| تصفير العدادات عند تغيّر اليوم/الأسبوع — يعادل RiskManager._roll_periods |
//+------------------------------------------------------------------------+
void RollPeriods()
{
   long today = DayStamp();
   if((long)GVGet("day", 0) != today)
   {
      GVSet("day", (double)today);
      GVSet("daily_pnl", 0.0);
   }
   long week = WeekStamp();
   if((long)GVGet("week", 0) != week)
   {
      GVSet("week", (double)week);
      GVSet("weekly_pnl", 0.0);
   }
}

//+------------------------------------------------------------------------+
//| قفل يدوي بختم زمني — لا يُقصَّر إن كان قفل أطول قائماً بالفعل            |
//+------------------------------------------------------------------------+
void Lock(const double hours, const string reason)
{
   datetime until = TimeCurrent() + (datetime)(hours * 3600);
   double current = GVGet("locked_until", 0.0);
   if(current > 0 && (datetime)current > until)
      return;
   GVSet("locked_until", (double)until);
   Print("🔒 قفل شبكة التحوّط حتى ", TimeToString((datetime)until), " — ", reason);
}

bool IsLocked()
{
   double until = GVGet("locked_until", 0.0);
   if(until <= 0)
      return false;
   if(TimeCurrent() >= (datetime)until)
   {
      GVSet("locked_until", 0.0);
      GVSet("consecutive_losses", 0.0);
      return false;
   }
   return true;
}

double HoursToMidnight()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   double secondsLeft = (23 - dt.hour) * 3600 + (59 - dt.min) * 60 + (60 - dt.sec);
   return MathMax(secondsLeft / 3600.0, 0.1);
}

//+------------------------------------------------------------------------+
//| بوابة الحماية اليومية/الأسبوعية/الـDrawdown/القفل الانتقامي —            |
//| مطابقة لـ RiskManager.daily_guard() في src/risk_manager.py             |
//+------------------------------------------------------------------------+
bool DailyGuardAllows(string &reason)
{
   RollPeriods();
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double peak = MathMax(GVGet("peak_balance", 0.0), balance);
   GVSet("peak_balance", peak);

   if(IsLocked())
   {
      reason = "قفل نشط";
      return false;
   }
   if(balance <= 0)
   {
      reason = "رصيد غير صالح";
      return false;
   }

   double dailyPct = GVGet("daily_pnl", 0.0) / balance;
   if(dailyPct <= -InpDailyLossLimitPercent / 100.0)
   {
      Lock(HoursToMidnight(), StringFormat("خسارة يومية %.2f%%", dailyPct * 100.0));
      reason = "تجاوز حد الخسارة اليومية";
      return false;
   }

   double weeklyPct = GVGet("weekly_pnl", 0.0) / balance;
   if(weeklyPct <= -InpWeeklyLossLimitPercent / 100.0)
   {
      Lock(24 * 7, StringFormat("خسارة أسبوعية %.2f%%", weeklyPct * 100.0));
      reason = "تجاوز حد الخسارة الأسبوعية";
      return false;
   }

   if((int)GVGet("consecutive_losses", 0) >= InpConsecutiveLossesLock)
   {
      Lock(InpConsecutiveLossesLockHours, "خسارات متتالية");
      reason = "قفل التداول الانتقامي";
      return false;
   }

   double drawdown = (peak > 0) ? (peak - balance) / peak : 0.0;
   if(drawdown >= InpMaxDrawdownPercent / 100.0)
   {
      reason = StringFormat("Drawdown %.2f%% تجاوز الحد", drawdown * 100.0);
      return false;
   }

   return true;
}

//+------------------------------------------------------------------------+
//| تسجيل نتيجة إغلاق سلة — يعادل RiskManager.record_result()               |
//+------------------------------------------------------------------------+
void RecordResult(const double pnl)
{
   RollPeriods();
   GVSet("daily_pnl", GVGet("daily_pnl", 0.0) + pnl);
   GVSet("weekly_pnl", GVGet("weekly_pnl", 0.0) + pnl);

   if(pnl < 0)
      GVSet("consecutive_losses", GVGet("consecutive_losses", 0.0) + 1);
   else
      GVSet("consecutive_losses", 0.0);
}

//+------------------------------------------------------------------------+
//| أدوات قراءة صفقات/أوامر هذه الشبكة فقط (Magic + الرمز)                  |
//+------------------------------------------------------------------------+
int CollectOurPositions(ulong &tickets[], double &entries[], int &types[], datetime &times[])
{
   int count = 0;
   int total = PositionsTotal();
   ArrayResize(tickets, total);
   ArrayResize(entries, total);
   ArrayResize(types, total);
   ArrayResize(times, total);

   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket))
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic || PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      tickets[count] = ticket;
      entries[count] = PositionGetDouble(POSITION_PRICE_OPEN);
      types[count]   = (int)PositionGetInteger(POSITION_TYPE);
      times[count]   = (datetime)PositionGetInteger(POSITION_TIME);
      count++;
   }
   ArrayResize(tickets, count);
   ArrayResize(entries, count);
   ArrayResize(types, count);
   ArrayResize(times, count);
   return count;
}

double BasketFloatingPnL(const ulong &tickets[])
{
   double total = 0.0;
   for(int i = 0; i < ArraySize(tickets); i++)
   {
      if(PositionSelectByTicket(tickets[i]))
         total += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
   }
   return total;
}

int CollectOurPending(ulong &tickets[], int &types[], double &prices[])
{
   int count = 0;
   int total = OrdersTotal();
   ArrayResize(tickets, total);
   ArrayResize(types, total);
   ArrayResize(prices, total);

   for(int i = 0; i < total; i++)
   {
      ulong ticket = OrderGetTicket(i);
      if(!OrderSelect(ticket))
         continue;
      if(OrderGetInteger(ORDER_MAGIC) != InpMagic || OrderGetString(ORDER_SYMBOL) != _Symbol)
         continue;
      int type = (int)OrderGetInteger(ORDER_TYPE);
      if(type != ORDER_TYPE_BUY_STOP && type != ORDER_TYPE_SELL_STOP)
         continue;
      tickets[count] = ticket;
      types[count]   = type;
      prices[count]  = OrderGetDouble(ORDER_PRICE_OPEN);
      count++;
   }
   ArrayResize(tickets, count);
   ArrayResize(types, count);
   ArrayResize(prices, count);
   return count;
}

//+------------------------------------------------------------------------+
//| إغلاق كل صفقات السلة + إلغاء كل الأوامر المعلّقة — يعادل _reset_basket   |
//+------------------------------------------------------------------------+
double CloseBasketAndCancelPending(const string reason)
{
   ulong posTickets[]; double entries[]; int posTypes[]; datetime times[];
   int posCount = CollectOurPositions(posTickets, entries, posTypes, times);

   // تُقرأ الأرباح فوراً قبل محاولة كل إغلاق — بعد النجاح لن تُعاد قراءتها
   // كمركز مفتوح. لا يُسجَّل إلا ما أُغلق فعلاً؛ مركز فشل إغلاقه يبقى مفتوحاً
   // ومتتبَّعاً بشكل طبيعي في CollectOurPositions بالدورة القادمة — تسجيل
   // ربحه/خسارته الآن كان سيُخطئ حالة المخاطرة (يُحتسب مرتين لاحقاً).
   double closedPnl = 0.0;
   int closedCount = 0;
   for(int i = 0; i < posCount; i++)
   {
      double pnl = 0.0;
      if(PositionSelectByTicket(posTickets[i]))
         pnl = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
      if(trade.PositionClose(posTickets[i]))
      {
         closedPnl += pnl;
         closedCount++;
      }
      else
         Print("⚠️ فشل إغلاق المركز #", posTickets[i], " retcode=", trade.ResultRetcode(),
               " — سيبقى مفتوحاً ومتتبَّعاً ضمن السلة الحالية");
   }

   ulong pendTickets[]; int pendTypes[]; double pendPrices[];
   int pendCount = CollectOurPending(pendTickets, pendTypes, pendPrices);
   for(int i = 0; i < pendCount; i++)
      if(!trade.OrderDelete(pendTickets[i]))
         Print("⚠️ فشل إلغاء الأمر المعلّق #", pendTickets[i], " retcode=", trade.ResultRetcode());

   if(closedCount > 0)
   {
      RecordResult(closedPnl);
      Print("📦 إغلاق السلة (", reason, ") — ", closedCount, "/", posCount, " صفقة، الناتج ",
            DoubleToString(closedPnl, 2), "$");
   }
   return closedPnl;
}

//+------------------------------------------------------------------------+
//| اتجاه وطول آخر سلسلة صفقات متتالية بلا انقطاع — يعادل _directional_streak|
//+------------------------------------------------------------------------+
void DirectionalStreak(const ulong &tickets[], const int &types[], const datetime &times[],
                        int &direction, int &streak)
{
   int n = ArraySize(tickets);
   direction = -1;
   streak = 0;
   if(n == 0)
      return;

   // فرز بالفقاعة حسب الوقت — عدد الصفقات صغير دوماً (بحدود InpMaxLevels)
   int order[]; ArrayResize(order, n);
   for(int i = 0; i < n; i++) order[i] = i;
   for(int i = 0; i < n - 1; i++)
      for(int j = 0; j < n - 1 - i; j++)
         if(times[order[j]] > times[order[j + 1]])
         {
            int tmp = order[j]; order[j] = order[j + 1]; order[j + 1] = tmp;
         }

   direction = types[order[n - 1]];
   for(int k = n - 1; k >= 0; k--)
   {
      if(types[order[k]] != direction)
         break;
      streak++;
   }
}

//+------------------------------------------------------------------------+
//| تمديد جانب واحد من الشبكة إن لم يكن مسلّحاً — يعادل _ensure_side        |
//+------------------------------------------------------------------------+
bool EnsureSide(const bool isBuy, const double &posEntries[], const int &posTypes[],
                const bool hasPending, const double price, const double step,
                const bool paused)
{
   if(hasPending || paused)
      return false;

   int wantType = isBuy ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
   bool found = false;
   double reference = price;
   for(int i = 0; i < ArraySize(posTypes); i++)
   {
      if(posTypes[i] != wantType)
         continue;
      if(!found) { reference = posEntries[i]; found = true; }
      else if(isBuy) reference = MathMax(reference, posEntries[i]);
      else           reference = MathMin(reference, posEntries[i]);
   }

   double nextPrice = isBuy ? reference + step : reference - step;
   nextPrice = NormalizeDouble(nextPrice, _Digits);

   if(isBuy)
      return trade.BuyStop(InpLotPerLevel, nextPrice, _Symbol, 0, 0, ORDER_TIME_GTC, 0, "GDGRID");
   return trade.SellStop(InpLotPerLevel, nextPrice, _Symbol, 0, 0, ORDER_TIME_GTC, 0, "GDGRID");
}

//+------------------------------------------------------------------------+
//| OnInit / OnDeinit                                                       |
//+------------------------------------------------------------------------+
int OnInit()
{
   // رقم الحساب داخل البادئة: بدونه تتشارك حالة القفل/الخسارة اليومية
   // بين أي حسابين يُشغَّلان بنفس الرمز والـ Magic على نفس الطرفية —
   // GlobalVariables في MT5 على مستوى الطرفية كاملة لا الحساب وحده،
   // فينتقل خطأً قفل أو خسارة حساب تجريبي إلى حساب حقيقي بعد تبديل الدخول.
   gPrefix = StringFormat("GDGRID_%I64d_%s_%d_",
                          (long)AccountInfoInteger(ACCOUNT_LOGIN), _Symbol, InpMagic);
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);
   RollPeriods();
   Print("🕸️ GoldDragon_HedgeGrid جاهز — ", _Symbol, " | لوت/مستوى ", InpLotPerLevel,
         " | خطوة ", InpGridStepUSD, "$ | حد يومي ", InpDailyLossLimitPercent, "%");
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   Print("🛑 توقف GoldDragon_HedgeGrid — السبب ", reason);
}

//+------------------------------------------------------------------------+
//| OnTick — الدورة الرئيسية، تعادل HedgeGridStrategy.sync() بايثون بالضبط  |
//+------------------------------------------------------------------------+
void OnTick()
{
   ulong posTickets[]; double posEntries[]; int posTypes[]; datetime posTimes[];
   int posCount = CollectOurPositions(posTickets, posEntries, posTypes, posTimes);

   // 1) الحماية أولاً — نفس ترتيب bot_engine.py: القفل/الحدود قبل أي تداول
   string blockReason;
   if(!DailyGuardAllows(blockReason))
   {
      // يُغلق السلة (إن وُجدت) ويُلغي كل الأوامر المعلّقة في استدعاء واحد —
      // آمن حتى لو لم تكن هناك صفقات مفتوحة أصلاً (الحلقة الداخلية لا تُنفَّذ).
      CloseBasketAndCancelPending("risk_blocked:" + blockReason);
      Comment("🔒 شبكة التحوّط متوقفة: ", blockReason);
      return;
   }
   Comment("");

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double price = (bid + ask) / 2.0;

   // 2) قطع مبكر لخسارة السلة العائمة — مستقل عن حد الخسارة اليومي
   double floating = BasketFloatingPnL(posTickets);
   bool basketClosed = false;
   if(posCount > 0 && balance > 0 && (-floating / balance) >= InpMaxBasketLossPercent / 100.0)
   {
      CloseBasketAndCancelPending("basket_loss_cutoff");
      basketClosed = true;
   }
   // 3) هدف ربح السلة
   else if(posCount > 0 && floating >= InpBasketTargetUSD)
   {
      CloseBasketAndCancelPending("basket_tp");
      basketClosed = true;
   }

   if(basketClosed)
   {
      if(!InpReinitAfterClose)
         return;
      posCount = CollectOurPositions(posTickets, posEntries, posTypes, posTimes);
   }

   // 4) تمديد/تهيئة الشبكة
   ulong pendTickets[]; int pendTypes[]; double pendPrices[];
   int pendCount = CollectOurPending(pendTickets, pendTypes, pendPrices);
   int totalSlots = posCount + pendCount;
   if(totalSlots >= InpMaxLevels)
      return;

   int streakDirection, streakLen;
   DirectionalStreak(posTickets, posTypes, posTimes, streakDirection, streakLen);

   bool hasBuyPending = false, hasSellPending = false;
   for(int i = 0; i < pendCount; i++)
   {
      if(pendTypes[i] == ORDER_TYPE_BUY_STOP) hasBuyPending = true;
      if(pendTypes[i] == ORDER_TYPE_SELL_STOP) hasSellPending = true;
   }

   bool buyPaused = InpTrendGuardEnabled && streakDirection == POSITION_TYPE_BUY
                    && streakLen >= InpMaxDirectionalLevels;
   bool sellPaused = InpTrendGuardEnabled && streakDirection == POSITION_TYPE_SELL
                     && streakLen >= InpMaxDirectionalLevels;

   if(totalSlots < InpMaxLevels)
   {
      if(EnsureSide(true, posEntries, posTypes, hasBuyPending, price, InpGridStepUSD, buyPaused))
         totalSlots++;
   }
   if(totalSlots < InpMaxLevels)
      EnsureSide(false, posEntries, posTypes, hasSellPending, price, InpGridStepUSD, sellPaused);
}
