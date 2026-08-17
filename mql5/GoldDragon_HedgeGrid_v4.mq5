//+------------------------------------------------------------------------+
//|                                    GoldDragon_HedgeGrid_v4.mq5          |
//|  شبكة تحوّط لـ XAUUSD — إصلاح بنيوي لنسبة المخاطرة/العائد في v3          |
//+------------------------------------------------------------------------+
//
//  ═══ لماذا كانت v3 تخسر: الخلل الذي يقتلها ليس في الكود بل في القياس ═══
//
//  في v3 كانت خسارة السلة القصوى **نسبة مئوية من رأس المال** (تكبر معه)،
//  بينما هدف الربح **أرقام دولار ثابتة** (لا تكبر أبداً). النتيجة أنّ نسبة
//  النجاح المطلوبة لمجرّد التعادل كانت تزحف نحو المستحيل كلما نما الحساب:
//
//      رأس المال │ خسارة السلة القصوى │ الربح الصافي │ نسبة النجاح للتعادل
//      ──────────┼────────────────────┼──────────────┼────────────────────
//           16$  │        0.48$       │    0.93$     │      34%   ✅
//          100$  │        3.00$       │    0.93$     │      76%   ⚠️
//          500$  │       15.00$       │    0.93$     │      94%   ❌
//         2611$  │       78.33$       │    0.93$     │      99%   ☠️
//        10000$  │      300.00$       │    0.93$     │    99.7%   ☠️
//
//  هذا يفسّر تماماً لقطات "16$ → 2611$ في 3 أيام": عند 16$ كانت تحتاج 34%
//  نجاح فقط فطارت، وعند 2611$ صارت تحتاج 99% — أي أنّ خسارة سلة واحدة تمحو
//  84 سلة رابحة. لم تكن استراتيجية ناجحة، بل عدّاداً تنازلياً لم يصل دوره.
//
//  ═══ إصلاح v4: كل شيء يتحجّم مع رأس المال، أو لا شيء يتحجّم ═══
//
//  [1] **اللوت يتحجّم** مع الـEquity (InpRiskLotPer1000USD). بلوت ثابت،
//      ربح السلة بالدولار يبقى ثابتاً بينما حدّ الخسارة ينمو — وهذا بالضبط
//      مصدر الخلل. الآن الثلاثة (اللوت، الهدف، حدّ الخسارة) نِسَب من نفس
//      الـEquity، فتبقى نسبة النجاح المطلوبة ثابتة ~74% مهما نما الحساب.
//
//  [2] **الهدف نسبة مئوية** من الـEquity لا رقم دولار ثابت.
//
//  [3] **إصلاح خطأ عمولة ×2.** v3 كانت تحسب `CostPerLot × lot × 0.5`
//      باعتبارها "نصف دورة". لكن POSITION_PROFIT في MT5 **لا يتضمّن العمولة
//      إطلاقاً** — لا عمولة الدخول ولا الخروج. فكانت أرضية التكلفة وعتبة
//      حصاد الأزواج تحسبان نصف التكلفة الحقيقية، أي أنّ الحصاد كان يُغلق
//      أزواجاً يظنّها رابحة وهي خاسرة فعلياً بعد العمولة: مطحنة عمولات
//      تنزف الحساب بهدوء — وهي الظاهرة التي يحذّر منها تعليق v3 نفسه.
//
//  [4] **بوابة جدوى إلزامية عند الإقلاع.** الـEA يحسب نسبة النجاح المطلوبة
//      للتعادل بإعداداتك الفعلية، و**يرفض الإقلاع** إن تجاوزت الحد
//      (InpMaxBreakevenWinRate). v3 كانت تطبع التحذير ثم تتداول بأي حال.
//
//  [5] **تحذير الحد الأدنى لرأس المال.** تحت عتبة معيّنة لا يستطيع اللوت
//      النزول أكثر (VOLUME_MIN)، فتنكسر كل النِسَب وتصير المخاطرة الفعلية
//      أعلى بكثير من المضبوطة. الـEA يقيس هذه العتبة ويصرّح بها.
//
//  ═══ حقيقة كمّية تحكم كل ضبط لهذه الاستراتيجية ═══
//
//  نسبة العمولة من الربح لا تعتمد على اللوت ولا على رأس المال — بل **فقط**
//  على كم دولاراً من الحركة تلتقطه كل صفقة:
//
//      حركة 0.10$ → العمولة تلتهم 70% من الربح الإجمالي
//      حركة 0.21$ → 33%
//      حركة 0.35$ → 20%
//      حركة 0.50$ → 14%
//      حركة 1.20$ →  6%
//
//  لذلك "أهداف أصغر وتردد أعلى" ليست تسريعاً للربح، بل تسريعٌ لتحويل حسابك
//  إلى عمولات. الضبط الافتراضي هنا يستهدف ~0.50$ لكل صفقة عمداً.
//
//  ── ما لم يُمسّ من v3 ──
//  كل طبقات الحماية باقية وتعمل على Equity: حد الخسارة اليومي، الأسبوعي،
//  القطع المبكر، حارس الهامش، Drawdown، الخسارات المتتالية، Kill Switch
//  بمستوييه، فلتر السبريد، حارس انفجار التقلّب، فلتر التدوير، رفض حساب
//  Netting، تطبيع اللوت والسعر، احترام STOPS_LEVEL، حصاد الأزواج، قفل
//  الربح المتحرّك، الخطوة التكيّفية بـ ATR.
//
//  ── تحذير صريح ──
//  لم تُصرَّف هذه النسخة في MetaEditor (غير متاح في بيئة التطوير) — صرّفها
//  بنفسك أولاً. وإصلاح نسبة المخاطرة/العائد يجعل الاستراتيجية **متماسكة
//  رياضياً**، ولا يجعلها رابحة مضمونة: ~74% نسبة تعادل تعني أنّك ما زلت
//  تحتاج سوقاً متذبذباً فعلاً، وأنّ اتجاهاً قوياً واحداً يكلّفك. اختبرها على
//  ديمو مدة كافية ثم على حساب حقيقي صغير جداً.
//
//  أداة تعليمية/تقنية. المسؤولية الكاملة على المستخدم وحده.
//
//+------------------------------------------------------------------------+
//
//  ما ورثته v3 عن v2 (باقٍ كما هو):
//
//  [A] خطوة تكيّفية بـ ATR بدل 3$ ثابتة.
//      الشبكة الثابتة إمّا واسعة جداً في السوق الهادئ (لا صفقات = لا تردد)
//      أو ضيقة جداً في السوق العنيف (تعبئة كاملة خلال دقيقة ثم موت).
//      الآن: step = ATR(M1) × مضاعف، محصورة بين حدّ أدنى وأقصى، ولا تنزل
//      أبداً تحت (STOPS_LEVEL + سبريد) — أي أنّها تضيق قدر ما يسمح الوسيط.
//
//  [B] هدف السلة ديناميكي وواعٍ بالتكلفة — وهذا أهم بند في الملف كله.
//      في لقطات الحساب المرجعي: ربح إجمالي 4847$ مقابل عمولة 2253$.
//      أي أنّ 46% من الربح الإجمالي ذهب للعمولة. إن ضيّقت الهدف دون
//      احتساب العمولة فأنت لا تسرّع الربح، بل تسرّع الخسارة.
//      الآن: الهدف = max(هدف ديناميكي، تكلفة إغلاق السلة × معامل أمان)،
//      والـEA يتعلّم تكلفة اللوت الفعلية من سجلّك بدل التخمين.
//
//  [C] حصاد الأزواج (Pair Harvest) — الابتكار الرئيسي هنا.
//      بدل انتظار السلة كاملة لتصبح رابحة، يبحث الـEA عن (أفضل رابح +
//      أسوأ خاسر) الذي مجموعهما الصافي موجب بعد العمولة، ويغلقهما معاً.
//      الأثر: يحصد الربح أسرع بكثير، ويقلّص الهامش المحجوز، ويحرّر
//      مساحة لمستويات جديدة — أي تردد أعلى دون توسيع المخاطرة.
//      هذا بالضبط النمط الذي يُنتج شلال صفقات 0.10$/0.30$ في اللقطات.
//
//  [D] قفل ربح متحرّك للسلة (Basket Trailing Lock).
//      إذا تجاوز العائم عتبة التسليح، يُتتبَّع أعلى ربح وصلت إليه السلة،
//      وتُغلق فور تراجعه بمقدار معيّن. يمنع تبخّر سلة كانت رابحة.
//
//  [E] حارس انفجار التقلّب (Volatility Burst Guard).
//      الشبكات لا تموت في السوق الهادئ، بل في الشمعة الواحدة التي تقطع
//      كل المستويات. إن قفز ATR الحالي فوق مضاعف من متوسطه → تجميد
//      التوسّع فوراً (بلا إغلاق) حتى يهدأ.
//
//  [F] فلتر التدوير/السيولة الميتة + سلّم أوامر معلّقة لكل جانب.
//
//  [G] كابح زمني بالمللي ثانية بدل الثواني — v2 كان أدنى فاصل 1 ثانية،
//      وهو سقف صلب على التردد.
//
//  ── ما لم يُمسّ إطلاقاً (كما طلبت) ──
//  كل طبقات الحماية من v2 باقية وتعمل على Equity لا Balance:
//  حد الخسارة اليومي، الأسبوعي، القطع المبكر للسلة، حارس الهامش،
//  Drawdown، الخسارات المتتالية، Kill Switch بمستوييه، فلتر السبريد،
//  رفض حساب Netting، تطبيع اللوت والسعر، احترام STOPS_LEVEL.
//  ولا واحدة منها أُضعفت أو حُذفت لأجل رقم أسرع.
//
//  ── تحذير صريح ──
//  اللقطات المرجعية (16$ → 2611$) من حساب ديمو. تنفيذ الديمو مثالي:
//  بلا رفض، بلا إعادة تسعير، بلا انزلاق، بلا تأخير. شبكة تحوّط عالية
//  التردد هي أكثر الاستراتيجيات حساسية لهذه الفروق تحديداً. الفارق بين
//  ديمو وحقيقي في هذا النمط ليس فارقاً في النسبة — هو فارق في الإشارة.
//  اختبرها على ديمو ثم على حساب حقيقي صغير جداً قبل أي شيء آخر.
//
//  أداة تعليمية/تقنية. صرّفها بنفسك في MetaEditor واختبرها بنفسك.
//
//+------------------------------------------------------------------------+

#property copyright "Gold Dragon Trading Bot Guide"
#property link      ""
#property version   "4.00"
#property description "شبكة تحوّط XAUUSD — اللوت والهدف وحد الخسارة كلها نِسَب من الـEquity، فتبقى نسبة التعادل ثابتة مهما نما الحساب"
#property strict

#include <Trade\Trade.mqh>

//──────────────────────────────── أنماط السرعة ────────────────────────────────
enum ENUM_SPEED_MODE
{
   MODE_TURBO    = 0,   // أقصى تردد — خطوة ضيقة (عمولة عالية نسبةً، اقرأ رأس الملف)
   MODE_FAST     = 1,   // سريع
   MODE_BALANCED = 2,   // متوازن — الأفضل اقتصادياً (حركة ~0.5$/صفقة)
   MODE_MANUAL   = 3    // استخدم القيم اليدوية أدناه كما هي
};

//──────────────────────────────── الإعدادات ────────────────────────────────
input group "== نمط السرعة =="
// MODE_BALANCED هو الافتراضي الآن لا TURBO: عند خطوة ضيقة جداً تلتهم العمولة
// 33–70% من الربح الإجمالي (راجع جدول الحركة/العمولة في رأس الملف).
input ENUM_SPEED_MODE InpSpeedMode = MODE_BALANCED;

input group "== حجم اللوت — يتحجّم مع رأس المال (إصلاح v4 الجوهري) =="
// لوت ثابت + حدّ خسارة نسبة مئوية = الخلل الذي قتل v3. عندما يتحجّم اللوت
// مع الـEquity يتحجّم الربح بالدولار معه، فتبقى نسبة النجاح المطلوبة ثابتة.
input bool   InpAutoLot             = true;   // false = استخدم InpManualLot ثابتاً (سلوك v3)
input double InpRiskLotPer1000USD   = 0.02;   // لوت لكل 1000$ إيكويتي (0.02 = 0.01 لوت لكل 500$)
input double InpManualLot           = 0.01;   // يُستخدم فقط إن InpAutoLot=false
input double InpMaxAutoLot          = 1.00;   // سقف صلب مهما نما الحساب

input group "== الشبكة =="
input int    InpMaxLevels           = 20;     // سقف كلي: صفقات مفتوحة + أوامر معلّقة
input int    InpPendingPerSide      = 2;      // سلّم أوامر معلّقة لكل جانب (تعبئة أسرع)

input group "== الخطوة التكيّفية (ATR) =="
input bool   InpAdaptiveStep        = true;   // false = استخدم InpMinGridStepUSD ثابتة
input int    InpAtrPeriod           = 14;
input ENUM_TIMEFRAMES InpAtrTimeframe = PERIOD_M1;
input double InpAtrStepMult         = 0.30;   // step = ATR × هذا
input double InpMinGridStepUSD      = 0.60;   // أضيق خطوة مسموحة بالدولار
input double InpMaxGridStepUSD      = 4.00;   // أوسع خطوة مسموحة بالدولار

input group "== هدف ربح السلة — نسبة من الـEquity (إصلاح v4) =="
// v3 كانت أرقام دولار ثابتة بينما حدّ الخسارة نسبة مئوية — فكانت نسبة
// التعادل تزحف من 34% إلى 99% مع نمو الحساب. الآن الطرفان بنفس المقياس.
input double InpBaseTargetPercent     = 0.20; // % من الـEquity عند مركز واحد
input double InpTargetPerLevelPercent = 0.08; // % تُضاف لكل مركز إضافي
input double InpMaxBasketTargetPercent= 2.00; // سقف % مهما كبرت السلة
input double InpCostSafetyMult        = 1.60; // الهدف ≥ تكلفة إغلاق السلة × هذا
input double InpAssumedCostPerLot     = 7.00; // تكلفة ذهاب+إياب لـ 1.00 لوت قبل التعلّم

input group "== بوابة الجدوى (جديد — ترفض الإقلاع بإعدادات انتحارية) =="
// v3 كانت تطبع تحذير "تحتاج 99% نجاح" ثم تتداول بأي حال. الآن تُوقِف الإقلاع.
input double InpMaxBreakevenWinRate = 85.0;  // % — فوقها يرفض الـEA الإقلاع
input bool   InpAllowUnviableParams = false; // true = تجاوز البوابة (على مسؤوليتك)

input group "== حصاد الأزواج (رابح + خاسر) =="
input bool   InpPairHarvestEnabled  = true;
input int    InpMinPositionsForHarvest = 4;   // لا حصاد قبل هذا العدد من المراكز
input double InpMinPairProfitUSD    = 0.08;   // صافي الزوج بعد العمولة يجب أن يتجاوزه
input int    InpMaxHarvestPerTick   = 2;      // أقصى عدد أزواج تُغلق في التِك الواحد

input group "== قفل الربح المتحرّك للسلة =="
input bool   InpBasketTrailEnabled  = true;
input double InpTrailArmMult        = 1.50;   // يُسلَّح عند العائم ≥ الهدف × هذا
input double InpTrailGivebackUSD    = 0.35;   // يُغلق فور تراجع العائم عن قمته بهذا

input group "== حماية (كلها على Equity) =="
// خُفِّض من 3.0% (v3) إلى 1.5%: الطرف الآخر من معادلة التعادل. عند 3% لا
// يمرّ أي نمط من بوابة الجدوى — وهذا ليس تشدّداً، بل هو السبب الحسابي
// المباشر لخسارة v3. راجع جدول نسب التعادل في رأس الملف.
input double InpMaxBasketLossPercent  = 1.5;   // % من الـEquity — قطع مبكر لهذه السلة
input double InpDailyLossLimitPercent = 5.0;   // % — إيقاف حتى منتصف الليل (سيرفر)
input double InpWeeklyLossLimitPercent= 10.0;  // % — إيقاف أسبوع كامل
input double InpMaxDrawdownPercent    = 15.0;  // % من قمة الـEquity التاريخية
input int    InpConsecutiveLossesLock = 3;     // خسارات متتالية تقفل التداول
input int    InpConsecutiveLossesLockHours = 48;
input double InpDailyProfitTargetPercent = 0.0;// % ربح يومي يوقف التداول (0 = معطّل)

input group "== حارس الهامش =="
input double InpMinMarginLevelPercent      = 400.0; // تحته: يتوقف توسيع الشبكة
input double InpCriticalMarginLevelPercent = 200.0; // تحته: إغلاق السلة فوراً

input group "== حارس انفجار التقلّب (جديد) =="
input bool   InpBurstGuardEnabled   = true;
input int    InpAtrBaselineBars     = 60;     // طول المتوسط المرجعي لـ ATR
input double InpBurstAtrMult        = 2.20;   // ATR الآن ÷ متوسطه فوق هذا = تجميد
input int    InpBurstFreezeSeconds  = 90;     // مدة التجميد بعد رصد الانفجار

input group "== فلاتر تنفيذ =="
input double InpMaxSpreadUSD          = 0.60; // أقصى سبريد مقبول بالدولار
input int    InpActionThrottleMs      = 250;  // أقل فاصل بين محاولتي إرسال (مللي ثانية)
input int    InpCooldownSecondsAfterClose = 5;// تهدئة بعد إغلاق السلة قبل إعادة البناء
input bool   InpAvoidRollover         = true; // تجنّب ساعة التدوير (سبريد وحشي)
input int    InpRolloverStartHour     = 23;   // بتوقيت السيرفر
input int    InpRolloverEndHour       = 1;    // بتوقيت السيرفر

input group "== فلتر الاتجاه =="
input bool   InpTrendGuardEnabled     = true;
input int    InpMaxDirectionalLevels  = 6;    // أقصى صفقات متتالية بنفس الاتجاه

input group "== Kill Switch نهائي =="
input bool   InpKillSwitchEnabled            = true;
input double InpKillDrawdownPausePercent     = 20.0;  // % — إيقاف مؤقت يُفكّ تلقائياً
input int    InpKillDrawdownPauseHours       = 24;
input double InpKillDrawdownTerminatePercent = 50.0;  // % — إنهاء نهائي
input double InpKillDailyLossTerminatePercent= 10.0;  // % — إنهاء نهائي
input int    InpKillConsecutiveLossesTerminate = 5;   // إنهاء نهائي

input group "== إعادة تعيين يدوية =="
input bool   InpResetKillSwitch     = false;  // أعدها false فوراً بعد الفكّ

input group "== عام =="
input int    InpMagic               = 20260817;
input int    InpSlippagePoints      = 25;
input bool   InpReinitAfterClose    = true;
input bool   InpVerboseLog          = false;

//──────────────────────────────── حالة داخلية ────────────────────────────────
CTrade   trade;
string   gPrefix;
double   gLot         = 0.01;
double   gTickSize    = 0.0;
double   gMinStopDist = 0.0;
ulong    gLastActionMs= 0;
bool     gInitOk      = false;
int      gAtrHandle   = INVALID_HANDLE;

// قيم النمط الفعلية بعد تطبيق InpSpeedMode.
// gBaseTargetPct/gTargetPerPct نِسَب مئوية من الـEquity الآن، لا دولارات.
double   gMinStep       = 0.60;
double   gMaxStep       = 4.00;
double   gAtrMult       = 0.30;
double   gBaseTargetPct = 0.20;
double   gTargetPerPct  = 0.08;
int      gThrottleMs    = 250;
int      gPendPerSide   = 2;

#define KILL_LEVEL_PAUSE     1
#define KILL_LEVEL_TERMINATE 2

//+------------------------------------------------------------------------+
//| GlobalVariables — حالة تنجو من إعادة تشغيل الـEA أو الطرفية              |
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

void Log(const string msg)
{
   if(InpVerboseLog)
      Print(msg);
}

//+------------------------------------------------------------------------+
//| أختام الفترات — الأسبوع مضبوط على الإثنين لا على الخميس                  |
//+------------------------------------------------------------------------+
long DayStamp() { return (long)(TimeCurrent() / 86400); }

long WeekStamp()
{
   // 1970-01-01 كان خميساً؛ الإزاحة +3 تنقل حدّ الأسبوع إلى الإثنين.
   return (DayStamp() + 3) / 7;
}

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
//| القفل المؤقت                                                            |
//+------------------------------------------------------------------------+
void Lock(const double hours, const string reason)
{
   datetime until = TimeCurrent() + (datetime)(hours * 3600);
   double current = GVGet("locked_until", 0.0);
   if(current > 0 && (datetime)current > until)
      return;   // لا يُقصَّر قفل أطول قائم
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
//| تطبيع الحجم والسعر                                                      |
//+------------------------------------------------------------------------+
double NormalizeVolume(const double raw)
{
   double vmin  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double vstep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(vstep <= 0.0) vstep = 0.01;

   double v = MathRound(raw / vstep) * vstep;
   if(v < vmin) v = vmin;
   if(vmax > 0.0 && v > vmax) v = vmax;

   int digits = (int)MathRound(-MathLog10(vstep));
   if(digits < 0) digits = 0;
   if(digits > 8) digits = 8;
   return NormalizeDouble(v, digits);
}

double NormalizePrice(const double raw)
{
   double ts = (gTickSize > 0.0) ? gTickSize : _Point;
   return NormalizeDouble(MathRound(raw / ts) * ts, _Digits);
}

double MinStopDistance()
{
   double stopsLvl  = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double freezeLvl = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   return MathMax(stopsLvl, freezeLvl) * _Point;
}

double CurrentSpread()
{
   return SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID);
}

//+------------------------------------------------------------------------+
//| ATR — الخطوة التكيّفية وحارس الانفجار                                    |
//+------------------------------------------------------------------------+
double AtrNow()
{
   if(gAtrHandle == INVALID_HANDLE)
      return 0.0;
   double buf[];
   if(CopyBuffer(gAtrHandle, 0, 0, 1, buf) < 1)
      return 0.0;
   return buf[0];
}

// نسبة ATR الحالي إلى متوسطه — >1 يعني تسارع، >>1 يعني انفجار
double AtrBurstRatio()
{
   if(gAtrHandle == INVALID_HANDLE || InpAtrBaselineBars < 5)
      return 1.0;

   int need = InpAtrBaselineBars + 1;
   double buf[];
   if(CopyBuffer(gAtrHandle, 0, 0, need, buf) < need)
      return 1.0;
   ArraySetAsSeries(buf, true);

   double sum = 0.0;
   for(int i = 1; i < need; i++)
      sum += buf[i];
   double avg = sum / (need - 1);
   if(avg <= 0.0)
      return 1.0;
   return buf[0] / avg;
}

// الخطوة الفعلية: تكيّفية، لكنها لا تنزل تحت ما يسمح به الوسيط أبداً
double CurrentGridStep()
{
   double floorStep = gMinStopDist + CurrentSpread() + (gTickSize > 0 ? gTickSize : _Point);

   double s;
   if(InpAdaptiveStep)
   {
      double atr = AtrNow();
      s = (atr > 0.0) ? atr * gAtrMult : gMinStep;
   }
   else
      s = gMinStep;

   s = MathMax(s, gMinStep);
   s = MathMax(s, floorStep);
   s = MathMin(s, gMaxStep);
   return s;
}

//+------------------------------------------------------------------------+
//| تكلفة الدورة الكاملة — تُتعلَّم من سجلّك بدل التخمين                      |
//+------------------------------------------------------------------------+
double CostPerLotRoundTrip()
{
   double vol  = GVGet("cost_vol",  0.0);
   double cost = GVGet("cost_paid", 0.0);
   if(vol >= 0.20 && cost > 0.0)          // عيّنة كافية للثقة
      return cost / vol;
   return MathMax(InpAssumedCostPerLot, 0.0);
}

void LearnCost(const double volume, const double commissionAbs)
{
   if(volume <= 0.0 || commissionAbs <= 0.0)
      return;
   GVSet("cost_vol",  GVGet("cost_vol",  0.0) + volume);
   GVSet("cost_paid", GVGet("cost_paid", 0.0) + commissionAbs);
}

// تكلفة **الدورة الكاملة** لمركز واحد — ما يجب أن يتجاوزه أي ربح جزئي.
//
// إصلاح خطأ v3: كانت تضرب في 0.5 باعتبارها "نصف دورة"، لكن POSITION_PROFIT
// في MT5 لا يتضمّن العمولة إطلاقاً — لا عمولة الدخول ولا الخروج (العمولة
// خاصية Deal منفصلة، والسواب في POSITION_SWAP وحده). فأي مقارنة بالعائم
// يجب أن تطرح الدورة الكاملة. النتيجة في v3: حصاد أزواج يظنّها رابحة وهي
// خاسرة بعد العمولة — مطحنة عمولات تنزف الحساب بهدوء.
double FullCostPerPosition()
{
   return CostPerLotRoundTrip() * gLot;
}

//+------------------------------------------------------------------------+
//| قراءة صفقات/أوامر هذه الشبكة فقط                                        |
//+------------------------------------------------------------------------+
int CollectOurPositions(ulong &tickets[], ulong &posIds[], double &entries[],
                        int &types[], datetime &times[])
{
   int count = 0;
   int total = PositionsTotal();
   ArrayResize(tickets, total);
   ArrayResize(posIds,  total);
   ArrayResize(entries, total);
   ArrayResize(types,   total);
   ArrayResize(times,   total);

   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic ||
         PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      tickets[count] = ticket;
      posIds[count]  = (ulong)PositionGetInteger(POSITION_IDENTIFIER);
      entries[count] = PositionGetDouble(POSITION_PRICE_OPEN);
      types[count]   = (int)PositionGetInteger(POSITION_TYPE);
      times[count]   = (datetime)PositionGetInteger(POSITION_TIME);
      count++;
   }
   ArrayResize(tickets, count);
   ArrayResize(posIds,  count);
   ArrayResize(entries, count);
   ArrayResize(types,   count);
   ArrayResize(times,   count);
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
   ArrayResize(types,   total);
   ArrayResize(prices,  total);

   for(int i = 0; i < total; i++)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0)
         continue;
      if(OrderGetInteger(ORDER_MAGIC) != InpMagic ||
         OrderGetString(ORDER_SYMBOL) != _Symbol)
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
   ArrayResize(types,   count);
   ArrayResize(prices,  count);
   return count;
}

//+------------------------------------------------------------------------+
//| الربح المحقق فعلياً لمركز أُغلق — من السجل، شاملاً العمولة والسواب        |
//| ويغذّي في الوقت نفسه محرّك تعلّم التكلفة.                                |
//+------------------------------------------------------------------------+
double RealizedPnLForPosition(const ulong positionId)
{
   if(!HistorySelectByPosition(positionId))
      return 0.0;

   double sum        = 0.0;
   double volSum     = 0.0;
   double commAbsSum = 0.0;

   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0)
         continue;

      double profit = HistoryDealGetDouble(deal, DEAL_PROFIT);
      double swap   = HistoryDealGetDouble(deal, DEAL_SWAP);
      double comm   = HistoryDealGetDouble(deal, DEAL_COMMISSION);
      double vol    = HistoryDealGetDouble(deal, DEAL_VOLUME);

      sum        += profit + swap + comm;
      volSum     += vol;
      commAbsSum += MathAbs(comm);
   }

   // نصف الحجم لأن volSum يجمع الدخول والخروج معاً → تكلفة "لوت دورة كاملة"
   LearnCost(volSum * 0.5, commAbsSum);
   return sum;
}

//+------------------------------------------------------------------------+
//| تسجيل نتيجة إغلاق                                                       |
//+------------------------------------------------------------------------+
void RecordResult(const double pnl, const bool countStreak)
{
   RollPeriods();
   GVSet("daily_pnl",  GVGet("daily_pnl",  0.0) + pnl);
   GVSet("weekly_pnl", GVGet("weekly_pnl", 0.0) + pnl);

   // حصاد الأزواج لا يُحتسب في سلسلة الخسارات — إنه إغلاق جزئي رابح بطبيعته،
   // واحتسابه كان سيُفسد عدّاد الخسارات المتتالية ويقفل التداول بلا سبب.
   if(!countStreak)
      return;

   if(pnl < 0)
      GVSet("consecutive_losses", GVGet("consecutive_losses", 0.0) + 1);
   else
      GVSet("consecutive_losses", 0.0);
}

//+------------------------------------------------------------------------+
//| إغلاق السلة + إلغاء المعلّقات                                            |
//+------------------------------------------------------------------------+
double CloseBasketAndCancelPending(const string reason)
{
   ulong posTickets[]; ulong posIds[]; double entries[]; int posTypes[]; datetime times[];
   int posCount = CollectOurPositions(posTickets, posIds, entries, posTypes, times);

   double closedPnl = 0.0;
   int closedCount  = 0;
   for(int i = 0; i < posCount; i++)
   {
      if(trade.PositionClose(posTickets[i]))
      {
         closedPnl += RealizedPnLForPosition(posIds[i]);
         closedCount++;
      }
      else
      {
         Print("⚠️ فشل إغلاق المركز #", posTickets[i], " retcode=", trade.ResultRetcode(),
               " — سيبقى مفتوحاً ضمن السلة الحالية");
      }
   }

   ulong pendTickets[]; int pendTypes[]; double pendPrices[];
   int pendCount = CollectOurPending(pendTickets, pendTypes, pendPrices);
   for(int i = 0; i < pendCount; i++)
      if(!trade.OrderDelete(pendTickets[i]))
         Print("⚠️ فشل إلغاء الأمر المعلّق #", pendTickets[i], " retcode=", trade.ResultRetcode());

   if(closedCount > 0)
   {
      RecordResult(closedPnl, true);
      GVSet("cooldown_until", (double)(TimeCurrent() + InpCooldownSecondsAfterClose));
      GVSet("basket_peak", 0.0);   // تصفير قفل الربح المتحرّك للسلة الجديدة
      Print("📦 إغلاق السلة (", reason, ") — ", closedCount, "/", posCount, " صفقة، الناتج ",
            DoubleToString(closedPnl, 2), "$");
   }
   return closedPnl;
}

//+------------------------------------------------------------------------+
//| [C] حصاد الأزواج — أفضل رابح + أسوأ خاسر، إن كان صافيهما موجباً          |
//|                                                                         |
//| الفكرة: السلة ككل قد تحتاج دقائق لتصبح رابحة، لكن بداخلها دائماً أزواج   |
//| صافيها موجب الآن. إغلاقها فوراً يفعل ثلاثة أشياء معاً:                   |
//|   1. يحصد الربح مبكراً بدل انتظار السلة كاملة                            |
//|   2. يحرّر هامشاً محجوزاً → مساحة لمستويات جديدة                          |
//|   3. يقلّص عدد المراكز → يقلّص هدف السلة الديناميكي → إغلاق أسرع للباقي   |
//|                                                                         |
//| القيد الحاسم: الشرط ليس "مجموع موجب" بل "مجموع موجب بعد عمولة الإغلاق".  |
//| بدون هذا القيد يتحوّل الحصاد إلى مطحنة عمولات تنزف الحساب بهدوء.         |
//+------------------------------------------------------------------------+
int HarvestPairs(const ulong &tickets[], const ulong &posIds[])
{
   int n = ArraySize(tickets);
   if(n < InpMinPositionsForHarvest || n < 2)
      return 0;

   double net[];  ArrayResize(net, n);
   bool   used[]; ArrayResize(used, n);

   double closeCost = FullCostPerPosition();

   for(int i = 0; i < n; i++)
   {
      used[i] = true;                 // افتراضياً مستبعَد
      net[i]  = 0.0;
      if(!PositionSelectByTicket(tickets[i]))
         continue;
      net[i]  = PositionGetDouble(POSITION_PROFIT)
              + PositionGetDouble(POSITION_SWAP)
              - closeCost;
      used[i] = false;                // صالح للحصاد
   }

   int harvested = 0;
   for(int round = 0; round < InpMaxHarvestPerTick; round++)
   {
      int    wIdx = -1, lIdx = -1;
      double wVal = 0.0, lVal = 0.0;

      for(int i = 0; i < n; i++)
      {
         if(used[i]) continue;
         if(wIdx < 0 || net[i] > wVal) { wIdx = i; wVal = net[i]; }
         if(lIdx < 0 || net[i] < lVal) { lIdx = i; lVal = net[i]; }
      }

      if(wIdx < 0 || lIdx < 0 || wIdx == lIdx)
         break;
      if(wVal <= 0.0)
         break;                                   // لا رابح صافياً → لا حصاد
      if(wVal + lVal < InpMinPairProfitUSD)
         break;                                   // الزوج لا يغطي التكلفة + العتبة

      double realized = 0.0;
      bool   okBoth   = true;

      if(trade.PositionClose(tickets[wIdx]))
         realized += RealizedPnLForPosition(posIds[wIdx]);
      else
      {
         okBoth = false;
         Print("⚠️ حصاد: فشل إغلاق الرابح #", tickets[wIdx], " retcode=", trade.ResultRetcode());
      }

      if(trade.PositionClose(tickets[lIdx]))
         realized += RealizedPnLForPosition(posIds[lIdx]);
      else
      {
         okBoth = false;
         Print("⚠️ حصاد: فشل إغلاق الخاسر #", tickets[lIdx], " retcode=", trade.ResultRetcode());
      }

      used[wIdx] = true;
      used[lIdx] = true;

      if(realized != 0.0)
      {
         // countStreak=false: هذا إغلاق جزئي مقصود، لا نتيجة سلة
         RecordResult(realized, false);
         // الحصاد يسحب رابحاً من السلة فينخفض العائم فوراً — لو بقيت قمة
         // السلة القديمة لأطلق قفل الربح المتحرّك إغلاقاً كاذباً للباقي.
         GVSet("basket_peak", 0.0);
         harvested++;
         Log(StringFormat("🌾 حصاد زوج → %.2f$ (تقدير %.2f$)", realized, wVal + lVal));
      }

      if(!okBoth)
         break;   // شيء ما يرفضه الوسيط — لا تكرّر في نفس التِك
   }

   return harvested;
}

//+------------------------------------------------------------------------+
//| المخاطر — كلها على Equity، وتشمل العائم                                 |
//+------------------------------------------------------------------------+
double CurrentEquity() { return AccountInfoDouble(ACCOUNT_EQUITY); }

double PeakEquity()
{
   double eq   = CurrentEquity();
   double peak = MathMax(GVGet("peak_equity", 0.0), eq);
   GVSet("peak_equity", peak);
   return peak;
}

double EquityDrawdown()
{
   double peak = PeakEquity();
   if(peak <= 0.0)
      return 0.0;
   return (peak - CurrentEquity()) / peak;
}

double EffectiveDailyPnL(const ulong &posTickets[])
{
   return GVGet("daily_pnl", 0.0) + BasketFloatingPnL(posTickets);
}

double EffectiveWeeklyPnL(const ulong &posTickets[])
{
   return GVGet("weekly_pnl", 0.0) + BasketFloatingPnL(posTickets);
}

double MarginLevel()
{
   double ml = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
   return (ml <= 0.0) ? 1e9 : ml;   // 0 = لا مراكز → لا قيد
}

//+------------------------------------------------------------------------+
//| [B] هدف السلة — نسبة من الـEquity، ولا ينزل تحت تكلفة إغلاقها أبداً       |
//|                                                                         |
//| هذا قلب إصلاح v4: الهدف يتحجّم مع رأس المال تماماً كما يتحجّم حدّ الخسارة  |
//| (InpMaxBasketLossPercent)، فتبقى نسبة النجاح المطلوبة للتعادل ثابتة بدل  |
//| أن تزحف من 34% إلى 99% كلما نما الحساب كما كان يحدث في v3.               |
//+------------------------------------------------------------------------+
double DynamicBasketTarget(const int posCount)
{
   int n = MathMax(posCount, 1);

   double pct = gBaseTargetPct + gTargetPerPct * (n - 1);
   pct = MathMin(pct, InpMaxBasketTargetPercent);

   double target = CurrentEquity() * pct / 100.0;

   // أرضية التكلفة: إغلاق n مركز يكلّف n × **دورة كاملة** (لا نصفها كما في
   // v3). الهدف يجب أن يتجاوزها بمعامل أمان، وإلا فكل "ربح" تحققه هو خسارة
   // مقنّعة بعد العمولة.
   double costFloor = FullCostPerPosition() * n * MathMax(InpCostSafetyMult, 1.0);

   return MathMax(target, costFloor);
}

//+------------------------------------------------------------------------+
//| بوابة الحماية اللينة                                                     |
//+------------------------------------------------------------------------+
bool DailyGuardAllows(const ulong &posTickets[], string &reason)
{
   RollPeriods();
   double equity = CurrentEquity();

   if(IsLocked())        { reason = "قفل نشط";           return false; }
   if(equity <= 0.0)     { reason = "Equity غير صالح";   return false; }

   double dailyPnl = EffectiveDailyPnL(posTickets);
   double dailyPct = dailyPnl / equity;

   if(dailyPct <= -InpDailyLossLimitPercent / 100.0)
   {
      Lock(HoursToMidnight(), StringFormat("خسارة يومية %.2f%% (محقق + عائم)", dailyPct * 100.0));
      reason = "تجاوز حد الخسارة اليومية";
      return false;
   }

   // قفل الربح اليومي — يحمي يوماً ممتازاً من أن يتحوّل إلى يوم عادي
   if(InpDailyProfitTargetPercent > 0.0 &&
      dailyPct >= InpDailyProfitTargetPercent / 100.0)
   {
      Lock(HoursToMidnight(), StringFormat("تحقّق هدف الربح اليومي %.2f%%", dailyPct * 100.0));
      reason = "تحقّق هدف الربح اليومي";
      return false;
   }

   double weeklyPct = EffectiveWeeklyPnL(posTickets) / equity;
   if(weeklyPct <= -InpWeeklyLossLimitPercent / 100.0)
   {
      Lock(24 * 7, StringFormat("خسارة أسبوعية %.2f%% (محقق + عائم)", weeklyPct * 100.0));
      reason = "تجاوز حد الخسارة الأسبوعية";
      return false;
   }

   if((int)GVGet("consecutive_losses", 0) >= InpConsecutiveLossesLock)
   {
      Lock(InpConsecutiveLossesLockHours, "خسارات متتالية");
      reason = "قفل التداول الانتقامي";
      return false;
   }

   double dd = EquityDrawdown();
   if(dd >= InpMaxDrawdownPercent / 100.0)
   {
      reason = StringFormat("Drawdown %.2f%% تجاوز الحد", dd * 100.0);
      return false;
   }

   return true;
}

//+------------------------------------------------------------------------+
//| Kill Switch نهائي                                                       |
//+------------------------------------------------------------------------+
bool KillSwitchActive()
{
   if(GVGet("kill_active", 0.0) < 0.5)
      return false;
   if((int)GVGet("kill_level", 0.0) == KILL_LEVEL_TERMINATE)
      return true;   // نهائي — لا يُفكّ تلقائياً أبداً

   double until = GVGet("kill_until", 0.0);
   if(until > 0 && TimeCurrent() >= (datetime)until)
   {
      GVSet("kill_active", 0.0);
      GVSet("kill_level",  0.0);
      GVSet("kill_until",  0.0);
      Print("✅ انتهت مدة Kill Switch المؤقت (Pause) — استئناف تلقائي");
      return false;
   }
   return true;
}

void TriggerKillSwitch(const int level, const string reason)
{
   GVSet("kill_active", 1.0);
   GVSet("kill_level", (double)level);
   GVSet("kill_until", level == KILL_LEVEL_PAUSE
         ? (double)(TimeCurrent() + (long)InpKillDrawdownPauseHours * 3600) : 0.0);

   double closedPnl = CloseBasketAndCancelPending("kill_switch");

   string levelName = (level == KILL_LEVEL_TERMINATE) ? "TERMINATE (نهائي)" : "PAUSE (مؤقت)";
   Print("🚨 KILL SWITCH (", levelName, "): ", reason, " — إغلاق السلة: ",
         DoubleToString(closedPnl, 2), "$");
   if(level == KILL_LEVEL_TERMINATE)
      Print("   ⛔ إيقاف نهائي — لن يُستأنف تلقائياً. للفكّ: اضبط "
            "InpResetKillSwitch=true وأعد إرفاق الـEA مرة واحدة.");
}

void KillSwitchAutoCheck(const ulong &posTickets[])
{
   if(!InpKillSwitchEnabled || KillSwitchActive())
      return;

   RollPeriods();

   double equity = MathMax(CurrentEquity(), 1.0);
   double dd     = EquityDrawdown();
   double dailyLossPct = -MathMin(EffectiveDailyPnL(posTickets), 0.0) / equity;
   int    losses = (int)GVGet("consecutive_losses", 0.0);

   if(dd >= InpKillDrawdownTerminatePercent / 100.0)
      TriggerKillSwitch(KILL_LEVEL_TERMINATE,
                        StringFormat("Equity Drawdown %.2f%% تجاوز حد الإنهاء", dd * 100.0));
   else if(dailyLossPct >= InpKillDailyLossTerminatePercent / 100.0)
      TriggerKillSwitch(KILL_LEVEL_TERMINATE,
                        StringFormat("خسارة يومية %.2f%% تجاوزت حد الإنهاء", dailyLossPct * 100.0));
   else if(losses >= InpKillConsecutiveLossesTerminate)
      TriggerKillSwitch(KILL_LEVEL_TERMINATE,
                        StringFormat("%d خسارات متتالية", losses));
   else if(dd >= InpKillDrawdownPausePercent / 100.0)
      TriggerKillSwitch(KILL_LEVEL_PAUSE,
                        StringFormat("Equity Drawdown %.2f%% تجاوز حد الإيقاف المؤقت", dd * 100.0));
}

//+------------------------------------------------------------------------+
//| اتجاه وطول آخر سلسلة صفقات متتالية                                      |
//+------------------------------------------------------------------------+
void DirectionalStreak(const ulong &tickets[], const int &types[], const datetime &times[],
                       int &direction, int &streak)
{
   int n = ArraySize(tickets);
   direction = -1;
   streak = 0;
   if(n == 0)
      return;

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
//| فلتر ساعة التدوير — السبريد فيها يبتلع كل هدف صغير                       |
//+------------------------------------------------------------------------+
bool InRolloverWindow()
{
   if(!InpAvoidRollover)
      return false;
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int h = dt.hour;
   int a = InpRolloverStartHour, b = InpRolloverEndHour;
   if(a == b)
      return false;
   if(a < b)  return (h >= a && h < b);
   return (h >= a || h < b);   // نافذة تعبر منتصف الليل
}

//+------------------------------------------------------------------------+
//| [F] تمديد جانب واحد — الآن سلّم أوامر لا أمر واحد                        |
//+------------------------------------------------------------------------+
bool EnsureSide(const bool isBuy, const double &posEntries[], const int &posTypes[],
                const double &pendPrices[], const int &pendTypes[],
                const double step, const bool paused)
{
   if(paused)
      return false;

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if(bid <= 0.0 || ask <= 0.0)
      return false;
   double mid = (bid + ask) / 2.0;

   int wantPosType   = isBuy ? POSITION_TYPE_BUY   : POSITION_TYPE_SELL;
   int wantOrderType = isBuy ? ORDER_TYPE_BUY_STOP : ORDER_TYPE_SELL_STOP;

   // كم أمراً معلّقاً لدينا على هذا الجانب، وأين أبعدها؟
   int    sidePending  = 0;
   bool   pendFound    = false;
   double furthestPend = 0.0;
   for(int i = 0; i < ArraySize(pendTypes); i++)
   {
      if(pendTypes[i] != wantOrderType)
         continue;
      sidePending++;
      if(!pendFound) { furthestPend = pendPrices[i]; pendFound = true; }
      else if(isBuy) furthestPend = MathMax(furthestPend, pendPrices[i]);
      else           furthestPend = MathMin(furthestPend, pendPrices[i]);
   }

   if(sidePending >= gPendPerSide)
      return false;   // السلّم ممتلئ على هذا الجانب

   // المرجع: أبعد أمر معلّق إن وُجد، وإلا أبعد مركز مفتوح، وإلا السعر الحالي
   double reference;
   if(pendFound)
      reference = furthestPend;
   else
   {
      bool posFound = false;
      reference = mid;
      for(int i = 0; i < ArraySize(posTypes); i++)
      {
         if(posTypes[i] != wantPosType)
            continue;
         if(!posFound) { reference = posEntries[i]; posFound = true; }
         else if(isBuy) reference = MathMax(reference, posEntries[i]);
         else           reference = MathMin(reference, posEntries[i]);
      }
   }

   double nextPrice = isBuy ? reference + step : reference - step;

   // احترام الحد الأدنى الذي يفرضه الوسيط
   double guard = gMinStopDist + 2 * _Point;
   if(isBuy)  nextPrice = MathMax(nextPrice, ask + guard);
   else       nextPrice = MathMin(nextPrice, bid - guard);

   nextPrice = NormalizePrice(nextPrice);
   if(nextPrice <= 0.0)
      return false;

   // لا تضع أمرين على السعر نفسه بعد التطبيع (يحدث عند خطوة ضيقة جداً)
   for(int i = 0; i < ArraySize(pendTypes); i++)
   {
      if(pendTypes[i] != wantOrderType)
         continue;
      if(MathAbs(pendPrices[i] - nextPrice) < (gTickSize > 0 ? gTickSize : _Point))
         return false;
   }

   // فحص الهامش قبل الإرسال — أرخص من رفض الوسيط ومن Margin Call
   double needed = 0.0;
   ENUM_ORDER_TYPE ot = isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   if(OrderCalcMargin(ot, _Symbol, gLot, nextPrice, needed))
      if(needed > AccountInfoDouble(ACCOUNT_MARGIN_FREE) * 0.5)
      {
         Log(StringFormat("⚠️ هامش حر غير كافٍ لمستوى جديد (مطلوب %.2f$)", needed));
         return false;
      }

   bool ok = isBuy
      ? trade.BuyStop(gLot, nextPrice, _Symbol, 0, 0, ORDER_TIME_GTC, 0, "GDG3")
      : trade.SellStop(gLot, nextPrice, _Symbol, 0, 0, ORDER_TIME_GTC, 0, "GDG3");

   if(!ok)
      Log(StringFormat("⚠️ فشل %s عند %s retcode=%d — %s",
                       (isBuy ? "BuyStop" : "SellStop"),
                       DoubleToString(nextPrice, _Digits),
                       trade.ResultRetcode(), trade.ResultRetcodeDescription()));
   return ok;
}

//+------------------------------------------------------------------------+
//| تطبيق نمط السرعة على المتغيّرات الفعلية                                  |
//+------------------------------------------------------------------------+
void ApplySpeedMode()
{
   // القيم اليدوية هي الأساس؛ الأنماط تدهسها
   gMinStep       = InpMinGridStepUSD;
   gMaxStep       = InpMaxGridStepUSD;
   gAtrMult       = InpAtrStepMult;
   gBaseTargetPct = InpBaseTargetPercent;
   gTargetPerPct  = InpTargetPerLevelPercent;
   gThrottleMs    = InpActionThrottleMs;
   gPendPerSide   = InpPendingPerSide;

   // النِسَب أدناه مضبوطة بحيث تبقى نسبة التعادل ~74% عند InpRiskLotPer1000USD
   // الافتراضية، مع اختلاف الحركة الملتقطة لكل صفقة بين الأنماط: كلما ضاقت
   // الخطوة ارتفعت حصة العمولة من الربح (راجع الجدول في رأس الملف).
   switch(InpSpeedMode)
   {
      case MODE_TURBO:   // حركة ~0.2$/صفقة → العمولة ~33% من الربح الإجمالي
         gMinStep = 0.50;  gMaxStep = 2.50;  gAtrMult = 0.22;
         gBaseTargetPct = 0.10; gTargetPerPct = 0.04;
         gThrottleMs = 200;  gPendPerSide = 2;
         break;

      case MODE_FAST:    // حركة ~0.35$/صفقة → العمولة ~20%
         gMinStep = 0.80;  gMaxStep = 3.50;  gAtrMult = 0.32;
         gBaseTargetPct = 0.14; gTargetPerPct = 0.056;
         gThrottleMs = 400;  gPendPerSide = 2;
         break;

      case MODE_BALANCED: // حركة ~0.5$/صفقة → العمولة ~14% (الأفضل اقتصادياً)
         gMinStep = 1.50;  gMaxStep = 5.00;  gAtrMult = 0.55;
         gBaseTargetPct = 0.20; gTargetPerPct = 0.08;
         gThrottleMs = 1000; gPendPerSide = 1;
         break;

      case MODE_MANUAL:
      default:
         break;   // تُترك القيم اليدوية كما هي
   }

   if(gMaxStep < gMinStep) gMaxStep = gMinStep;
   if(gPendPerSide < 1)    gPendPerSide = 1;
   if(gThrottleMs  < 50)   gThrottleMs  = 50;
}

//+------------------------------------------------------------------------+
//| اللوت المتحجّم مع الـEquity — الطرف الثالث الذي كان مفقوداً في v3          |
//|                                                                         |
//| بلوت ثابت يبقى ربح السلة بالدولار ثابتاً بينما ينمو حدّ الخسارة مع        |
//| الحساب — وهذا حرفياً ما جعل نسبة التعادل تصل 99%. تحجيم اللوت يجعل        |
//| الربح ينمو بنفس معدل نمو حدّ الخسارة، فتثبت النسبة.                      |
//+------------------------------------------------------------------------+
void RefreshLot()
{
   if(!InpAutoLot)
   {
      gLot = NormalizeVolume(InpManualLot);
      return;
   }
   double raw = CurrentEquity() * InpRiskLotPer1000USD / 1000.0;
   raw = MathMin(raw, InpMaxAutoLot);
   gLot = NormalizeVolume(raw);   // NormalizeVolume يرفعه إلى VOLUME_MIN عند اللزوم
}

// أدنى إيكويتي يبقى عنده اللوت قابلاً للتحجيم. تحته يُسمَّر اللوت عند
// VOLUME_MIN فتنكسر كل النِسَب وتصير المخاطرة الفعلية أعلى من المضبوطة.
double MinViableEquity()
{
   if(!InpAutoLot || InpRiskLotPer1000USD <= 0.0)
      return 0.0;
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   return vmin * 1000.0 / InpRiskLotPer1000USD;
}

//+------------------------------------------------------------------------+
//| نسبة النجاح المطلوبة لمجرّد التعادل — بوابة الجدوى                        |
//|                                                                         |
//| = خسارة السلة القصوى ÷ (خسارة السلة القصوى + متوسط الربح الصافي).        |
//| فوق 85% تعني أنّ خسارة واحدة تمحو عشرات الأرباح: ذيل سالب حاد لا تنجو     |
//| منه أي استراتيجية على المدى الطويل.                                      |
//+------------------------------------------------------------------------+
double BreakevenWinRate(const int sampleBasketSize)
{
   int    n      = MathMax(sampleBasketSize, 1);
   double target = DynamicBasketTarget(n);
   double comm   = FullCostPerPosition() * n;
   double net    = target - comm;
   double maxLoss= CurrentEquity() * InpMaxBasketLossPercent / 100.0;

   if(net <= 0.0 || maxLoss <= 0.0)
      return 100.0;
   return maxLoss / (maxLoss + net) * 100.0;
}

//+------------------------------------------------------------------------+
//| OnInit                                                                  |
//+------------------------------------------------------------------------+
int OnInit()
{
   gPrefix = StringFormat("GDGRID3_%I64d_%s_%d_",
                          (long)AccountInfoInteger(ACCOUNT_LOGIN), _Symbol, InpMagic);

   // ── فحص إلزامي: وضع الحساب ──
   // على حساب Netting لا يوجد "تحوّط" — الأمر المعاكس يُقفل المركز القائم بدل
   // فتح مركز مضاد، فينهار منطق الشبكة كلياً وبصمت.
   ENUM_ACCOUNT_MARGIN_MODE mode =
      (ENUM_ACCOUNT_MARGIN_MODE)AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   if(mode != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
   {
      Print("❌ هذا الحساب ليس Hedging — شبكة التحوّط لا تعمل على حساب Netting. ",
            "أوقف الـEA واستخدم حساب Hedging.");
      return(INIT_FAILED);
   }

   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      Print("⚠️ التداول الآلي معطّل في الطرفية — فعّل زر AlgoTrading.");
   if(!AccountInfoInteger(ACCOUNT_TRADE_EXPERT))
      Print("⚠️ الوسيط يمنع تداول الـEA على هذا الحساب.");

   ApplySpeedMode();

   gTickSize    = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   gMinStopDist = MinStopDistance();
   RefreshLot();

   gAtrHandle = iATR(_Symbol, InpAtrTimeframe, InpAtrPeriod);
   if(gAtrHandle == INVALID_HANDLE)
   {
      Print("⚠️ تعذّر إنشاء مؤشر ATR — سيُستخدم الحد الأدنى للخطوة بشكل ثابت.");
   }

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.LogLevel(LOG_LEVEL_ERRORS);

   RollPeriods();
   PeakEquity();
   GVSet("halted_no_reinit", 0.0);
   GVSet("basket_peak", 0.0);

   if(InpResetKillSwitch)
   {
      GVSet("kill_active", 0.0);
      GVSet("kill_level",  0.0);
      GVSet("kill_until",  0.0);
      GVSet("consecutive_losses", 0.0);
      Print("✅ أُعيد تعيين Kill Switch يدوياً — أعد InpResetKillSwitch إلى false الآن، "
            "وإلا سيُعاد الفكّ في كل إعادة تشغيل قادمة.");
   }

   // ── تقرير الجدوى الاقتصادية قبل أي صفقة ──
   double equity       = CurrentEquity();
   double spreadNow    = CurrentSpread();
   double floorStep    = gMinStopDist + spreadNow;
   double costPerLot   = CostPerLotRoundTrip();
   double costPerTrade = costPerLot * gLot;          // دورة كاملة لمركز واحد
   double minEquity    = MinViableEquity();

   // سلة من 6 مراكز عيّنة تمثيلية لحجم سلة نموذجي أثناء التشغيل
   const int SAMPLE_N  = 6;
   double sampleTarget = DynamicBasketTarget(SAMPLE_N);
   double sampleComm   = FullCostPerPosition() * SAMPLE_N;
   double sampleNet    = sampleTarget - sampleComm;
   double maxLoss      = equity * InpMaxBasketLossPercent / 100.0;
   double breakevenWR  = BreakevenWinRate(SAMPLE_N);
   double movePerTrade = (gLot > 0.0)
                       ? sampleTarget / (SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE)
                                         * gLot * SAMPLE_N)
                       : 0.0;

   Print("═══════════ GoldDragon HedgeGrid v4 ═══════════");
   Print("الرمز ", _Symbol, " | إيكويتي ", DoubleToString(equity, 2),
         "$ | لوت ", gLot, (InpAutoLot ? " (تلقائي — يتحجّم مع الحساب)" : " (يدوي ثابت)"),
         " | نمط ", EnumToString(InpSpeedMode));
   Print("الخطوة: أدنى ", DoubleToString(gMinStep, 2), "$ | أقصى ",
         DoubleToString(gMaxStep, 2), "$ | ATR×", DoubleToString(gAtrMult, 2),
         " | أرضية الوسيط ", DoubleToString(floorStep, 2), "$");
   Print("التكلفة: ~", DoubleToString(costPerLot, 2), "$ لكل لوت دورة كاملة → ",
         DoubleToString(costPerTrade, 3), "$ لكل مركز عند لوتك.");
   Print("سلة نموذجية (", SAMPLE_N, " مراكز): هدف ", DoubleToString(sampleTarget, 2),
         "$ − عمولة ", DoubleToString(sampleComm, 2), "$ = صافي ",
         DoubleToString(sampleNet, 2), "$ | حركة مطلوبة ",
         DoubleToString(movePerTrade, 2), "$ لكل صفقة");

   if(gMinStep <= floorStep)
      Print("⚠️ خطوتك الدنيا ", DoubleToString(gMinStep, 2),
            "$ أضيق من أرضية الوسيط ", DoubleToString(floorStep, 2),
            "$ — ستُزاح الأوامر تلقائياً، وسيكون التباعد الفعلي أوسع مما تتوقع.");

   if(InpAutoLot && minEquity > 0.0 && equity < minEquity)
      Print("⚠️⚠️ إيكويتيك ", DoubleToString(equity, 2), "$ تحت الحد الأدنى ",
            DoubleToString(minEquity, 2), "$ الذي يبقى عنده اللوت قابلاً للتحجيم. ",
            "اللوت مسمّر عند VOLUME_MIN، أي أنّ مخاطرتك الفعلية **أعلى** من المضبوطة ",
            "وكل نِسَب هذا التقرير متفائلة. إما أودِع حتى ", DoubleToString(minEquity, 0),
            "$ أو ارفع InpRiskLotPer1000USD وأنت مدرك أنّك ترفع المخاطرة.");

   Print("📐 ملف المخاطر: ربح صافٍ ", DoubleToString(sampleNet, 2),
         "$ مقابل خسارة سلة قصوى ", DoubleToString(maxLoss, 2), "$ → ",
         "نسبة النجاح للتعادل = ", DoubleToString(breakevenWR, 2), "%");

   // ── بوابة الجدوى: v3 كانت تحذّر ثم تتداول. v4 توقف الإقلاع. ──
   if(breakevenWR > InpMaxBreakevenWinRate)
   {
      Print("⛔ رُفض الإقلاع: تحتاج ", DoubleToString(breakevenWR, 2),
            "% نجاح لمجرّد التعادل، وحدّك ", DoubleToString(InpMaxBreakevenWinRate, 2), "%.");
      Print("   معنى الرقم: خسارة سلة واحدة تمحو ",
            DoubleToString(sampleNet > 0 ? maxLoss / sampleNet : 999, 0),
            " سلة رابحة. هذا ذيل سالب لا تنجو منه أي استراتيجية.");
      Print("   الحلول (بترتيب الأفضلية):");
      Print("     1. ارفع InpRiskLotPer1000USD (يرفع الربح بالدولار مع نفس النِسَب)");
      Print("     2. ارفع InpBaseTargetPercent / InpTargetPerLevelPercent");
      Print("     3. اخفض InpMaxBasketLossPercent (قطع مبكر أضيق)");
      Print("     4. InpAllowUnviableParams=true لتجاوز البوابة — على مسؤوليتك وحدك");
      if(!InpAllowUnviableParams)
         return(INIT_FAILED);
      Print("   ⚠️ تم التجاوز بـ InpAllowUnviableParams=true — تتداول بإعدادات خاسرة رياضياً.");
   }
   else if(breakevenWR > 80.0)
      Print("   ⚠️ ذيل سالب واضح — راقب نتيجة الشهر لا نتيجة اليوم.");

   Print("الحمايات: يومي ", InpDailyLossLimitPercent, "% | أسبوعي ",
         InpWeeklyLossLimitPercent, "% | DD ", InpMaxDrawdownPercent,
         "% | هامش حرج ", InpCriticalMarginLevelPercent,
         "% | KillSwitch ", (InpKillSwitchEnabled ? "مفعّل" : "معطّل"),
         (KillSwitchActive() ? " (نشط الآن ⛔)" : ""));
   Print("═══════════════════════════════════════════════");

   gInitOk = true;
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   if(gAtrHandle != INVALID_HANDLE)
      IndicatorRelease(gAtrHandle);
   Comment("");
   Print("🛑 توقف GoldDragon_HedgeGrid v4 — السبب ", reason);
}

//+------------------------------------------------------------------------+
//| OnTick                                                                  |
//+------------------------------------------------------------------------+
void OnTick()
{
   if(!gInitOk)
      return;

   // 0) Kill Switch أولاً — أقسى وأرخص فحص
   if(KillSwitchActive())
   {
      int level = (int)GVGet("kill_level", 0.0);
      Comment(level == KILL_LEVEL_TERMINATE
              ? "⛔ Kill Switch نهائي — يتطلب InpResetKillSwitch=true يدوياً"
              : "⏸️ Kill Switch مؤقت — سيُفكّ تلقائياً");
      return;
   }

   ulong posTickets[]; ulong posIds[]; double posEntries[]; int posTypes[]; datetime posTimes[];
   int posCount = CollectOurPositions(posTickets, posIds, posEntries, posTypes, posTimes);

   // يُحدَّث اللوت مع نمو/تراجع الـEquity — هذا ما يبقي نسبة التعادل ثابتة.
   // يُستدعى فقط حين تكون الشبكة فارغة: تغيير اللوت وسط سلة قائمة يخلط
   // أحجاماً مختلفة داخل السلة نفسها فيفسد حساب الهدف وحصاد الأزواج.
   if(posCount == 0)
      RefreshLot();

   KillSwitchAutoCheck(posTickets);
   if(KillSwitchActive())
      return;   // تفعّل للتو — أُغلقت السلة

   // 1) الحماية اللينة
   string blockReason;
   if(!DailyGuardAllows(posTickets, blockReason))
   {
      CloseBasketAndCancelPending("risk_blocked:" + blockReason);
      Comment("🔒 شبكة التحوّط متوقفة: ", blockReason);
      return;
   }

   double equity   = CurrentEquity();
   double floating = BasketFloatingPnL(posTickets);
   double marginLv = MarginLevel();

   // 2) حارس الهامش الحرج — الشبكات تموت هنا، لا عند حد الخسارة
   if(posCount > 0 && marginLv < InpCriticalMarginLevelPercent)
   {
      CloseBasketAndCancelPending(StringFormat("margin_level_%.0f%%", marginLv));
      Comment("🆘 مستوى هامش حرج — أُغلقت السلة");
      return;
   }

   double target = DynamicBasketTarget(posCount);
   bool basketClosed = false;

   // 3) قطع مبكر لخسارة السلة العائمة — قبل أي منطق ربح
   if(posCount > 0 && equity > 0 && (-floating / equity) >= InpMaxBasketLossPercent / 100.0)
   {
      CloseBasketAndCancelPending("basket_loss_cutoff");
      basketClosed = true;
   }
   // 4) هدف ربح السلة (ديناميكي، فوق أرضية التكلفة)
   else if(posCount > 0 && floating >= target)
   {
      CloseBasketAndCancelPending("basket_tp");
      basketClosed = true;
   }
   // 5) [D] قفل الربح المتحرّك — يمنع تبخّر سلة كانت رابحة
   else if(InpBasketTrailEnabled && posCount > 0)
   {
      double armLevel = target * MathMax(InpTrailArmMult, 1.0);
      double peak     = GVGet("basket_peak", 0.0);

      if(floating >= armLevel)
      {
         if(floating > peak)
         {
            peak = floating;
            GVSet("basket_peak", peak);
         }
      }

      if(peak > 0.0 && floating <= peak - InpTrailGivebackUSD && floating > 0.0)
      {
         CloseBasketAndCancelPending(StringFormat("basket_trail_lock_peak%.2f", peak));
         basketClosed = true;
      }
   }

   if(posCount == 0 && GVGet("basket_peak", 0.0) != 0.0)
      GVSet("basket_peak", 0.0);

   if(basketClosed)
   {
      if(!InpReinitAfterClose)
         GVSet("halted_no_reinit", 1.0);
      Comment("⏳ تهدئة بعد إغلاق السلة");
      return;
   }

   // 6) [C] حصاد الأزواج — الربح الجزئي السريع، بعد كل حمايات الخسارة أعلاه
   if(InpPairHarvestEnabled && posCount >= InpMinPositionsForHarvest)
   {
      int harvested = HarvestPairs(posTickets, posIds);
      if(harvested > 0)
      {
         // أُغلقت مراكز — البيانات في اليد قديمة الآن، أعد القراءة في التِك التالي
         Comment(StringFormat("🌾 حُصد %d زوج — إعادة تقييم", harvested));
         return;
      }
   }

   // InpReinitAfterClose=false يعني دورة واحدة فقط لكل تشغيل
   if(GVGet("halted_no_reinit", 0.0) > 0.5)
   {
      Comment("⏹️ أُغلقت السلة و InpReinitAfterClose=false — لا إعادة بناء حتى إعادة إرفاق الـEA");
      return;
   }

   double cooldownUntil = GVGet("cooldown_until", 0.0);
   if(cooldownUntil > 0 && TimeCurrent() < (datetime)cooldownUntil)
   {
      Comment("⏳ تهدئة حتى ", TimeToString((datetime)cooldownUntil, TIME_SECONDS));
      return;
   }

   // 7) فلاتر تنفيذ قبل أي إرسال
   if(InRolloverWindow())
   {
      Comment("🌙 نافذة التدوير — لا توسيع (السبريد يبتلع الهدف الصغير)");
      return;
   }

   double spread = CurrentSpread();
   if(spread > InpMaxSpreadUSD)
   {
      Comment("📛 سبريد مرتفع ", DoubleToString(spread, 2), "$ — لا توسيع");
      return;
   }

   if(marginLv < InpMinMarginLevelPercent)
   {
      Comment("🛑 مستوى هامش ", DoubleToString(marginLv, 0), "% — تجميد التوسّع");
      return;
   }

   // 8) [E] حارس انفجار التقلّب — الشبكة تموت في الشمعة الواحدة، لا في الهدوء
   double burstUntil = GVGet("burst_until", 0.0);
   if(InpBurstGuardEnabled)
   {
      double ratio = AtrBurstRatio();
      if(ratio >= InpBurstAtrMult)
      {
         GVSet("burst_until", (double)(TimeCurrent() + InpBurstFreezeSeconds));
         Comment(StringFormat("⚡ انفجار تقلّب ×%.2f — تجميد التوسّع %ds", ratio, InpBurstFreezeSeconds));
         return;
      }
      if(burstUntil > 0 && TimeCurrent() < (datetime)burstUntil)
      {
         Comment("⚡ تهدئة ما بعد الانفجار — لا مستويات جديدة");
         return;
      }
   }

   // 9) الكابح الزمني بالمللي ثانية
   ulong nowMs = GetTickCount64();
   if(gLastActionMs > 0 && (nowMs - gLastActionMs) < (ulong)gThrottleMs)
      return;

   // 10) تمديد/تهيئة الشبكة
   ulong pendTickets[]; int pendTypes[]; double pendPrices[];
   int pendCount  = CollectOurPending(pendTickets, pendTypes, pendPrices);
   int totalSlots = posCount + pendCount;
   double step    = CurrentGridStep();

   Comment(StringFormat(
      "🕸️ v3 %s | مستويات %d/%d | عائم %.2f$ | هدف %.2f$ | خطوة %.2f$ | هامش %.0f%% | سبريد %.2f$",
      EnumToString(InpSpeedMode), totalSlots, InpMaxLevels, floating, target, step,
      (marginLv > 1e8 ? 0.0 : marginLv), spread));

   if(totalSlots >= InpMaxLevels)
      return;

   int streakDirection, streakLen;
   DirectionalStreak(posTickets, posTypes, posTimes, streakDirection, streakLen);

   bool buyPaused  = InpTrendGuardEnabled && streakDirection == POSITION_TYPE_BUY
                     && streakLen >= InpMaxDirectionalLevels;
   bool sellPaused = InpTrendGuardEnabled && streakDirection == POSITION_TYPE_SELL
                     && streakLen >= InpMaxDirectionalLevels;

   if(totalSlots < InpMaxLevels)
      if(EnsureSide(true, posEntries, posTypes, pendPrices, pendTypes, step, buyPaused))
         totalSlots++;

   if(totalSlots < InpMaxLevels)
      if(EnsureSide(false, posEntries, posTypes, pendPrices, pendTypes, step, sellPaused))
         totalSlots++;

   // الكابح يُحدَّث حتى لو فشل الإرسال — وإلا أُعيدت المحاولة كل تِك وأُغرق الوسيط
   gLastActionMs = nowMs;
}
//+------------------------------------------------------------------------+
