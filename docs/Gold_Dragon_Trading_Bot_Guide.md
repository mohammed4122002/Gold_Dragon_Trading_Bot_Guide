# 🤖 دليل بناء بوت التداول الآلي المؤسساتي
## Gold Dragon Trading Bot -- الإصدار المؤسساتي v1.0
### بناء ونشر بوت تداول آلي على Claude Code

---

## ⚠️ تنويه قانوني ومالي إلزامي

> **هذا الدليل تعليمي/تقني فقط.** لا يشكل توصية مالية أو استثمارية. التداول الآلي ينطوي على مخاطرة كبيرة بفقدان رأس المال بالكامل. يجب على المستخدم:
> 1. اختبار البوت على حساب تجريبي (Demo) لمدة لا تقل عن 3 أشهر قبل أي استخدام حقيقي.
> 2. استشارة مستشار مالي مرخّص قبل نشر أي بوت على حساب حقيقي.
> 3. فهم أن أي بوت -- مهما كانت دقته -- قد يسبب خسائر كارثية.
> 4. عدم استخدام أموال ضرورية (إيجار، ديون، مصاريف) في التداول الآلي.
> 5. الالتزام بقوانين الضرائب والتنظيم المالي في بلد إقامته.

**المسؤولية الكاملة تقع على عاتق المستخدم فقط.**

---

## 📋 جدول المحتويات

1. [نظرة عامة على المشروع](#1-نظرة-عامة)
2. [المتطلبات التقنية](#2-المتطلبات-التقنية)
3. [هيكل المشروع](#3-هيكل-المشروع)
4. [الوحدات الأساسية](#4-الوحدات-الأساسية)
5. [بروتوكول Gold Dragon في البوت](#5-بروتوكول-gold-dragon)
6. [إدارة المخاطرة الآلية](#6-إدارة-المخاطرة)
7. [النشر على Claude Code](#7-النشر-على-claude-code)
8. [الاختبار والمحاكاة](#8-الاختبار)
9. [الصيانة والمراقبة](#9-الصيانة)
10. [Checklist قبل النشر الحي](#10-checklist)

---

## 1) نظرة عامة على المشروع

### 1.1) ما هو Gold Dragon Trading Bot؟
بوت تداول آلي متخصص في **XAU/USD** فقط، يعتمد على منهجية **Smart Money Concepts (SMC)** وهندسة السيولة، مدعوم بـ **Gold Dragon Algorithm Pro v4**.

### 1.2) المستوى المستهدف
- **المستوى:** مؤسساتي (Institutional Grade)
- **الزوج:** XAU/USD فقط
- **الأطر:** H1 (تحليل) + M15 (تريغر) + M5 (تأكيد نهائي)
- **الأتمتة:** شبه كاملة -- يتطلب مراقبة بشرية

### 1.3) الفلسفة: "البوت ينفذ، الإنسان يراقب"
> لا تترك أي بوت بدون مراقبة. حتى أفضل البوتات تحتاج "قبضة بشرية".

---

## 2) المتطلبات التقنية

### 2.1) البنية التقنية

| المكون | التوصية | البديل |
|---|---|---|
| **لغة البرمجة** | Python 3.10+ | Node.js / Go |
| **منصة التداول** | MetaTrader 5 (MT5) | cTrader / TradingView |
| **وسيط الاتصال** | MetaTrader5 Python API | ccxt (للبورصات) |
| **قاعدة البيانات** | SQLite (للتجريبي) / PostgreSQL (للإنتاج) | MongoDB |
| **الإشعارات** | Telegram Bot API | Discord / Email |
| **الاستضافة** | VPS (AWS/Vultr/DigitalOcean) | Raspberry Pi (للتجريبي) |
| **نظام التشغيل** | Ubuntu 22.04 LTS | Windows Server |

### 2.2) المكتبات المطلوبة (Python)

```bash
pip install metaapi-cloud-sdk pandas numpy talib-binary
pip install python-telegram-bot schedule pyyaml
pip install requests websocket-client logging
```

### 2.3) حسابات مطلوبة
- [ ] حساب وسيط (Broker) -- MT5 حقيقي أو تجريبي
- [ ] VPS (Virtual Private Server) -- للتشغيل 24/7
- [ ] بوت Telegram -- للإشعارات
- [ ] حساب Claude Code API -- للتحليل الذكي

---

## 3) هيكل المشروع

```
gold_dragon_bot/
│
├── config/
│   ├── settings.yaml          # الإعدادات العامة
│   ├── risk.yaml              # إعدادات المخاطرة
│   └── symbols.yaml           # أزواج التداول (XAU/USD فقط)
│
├── src/
│   ├── __init__.py
│   ├── main.py                # نقطة الدخول الرئيسية
│   ├── bot_engine.py          # محرك البوت
│   ├── gold_dragon_core.py    # بروتوكول Gold Dragon
│   ├── market_analyzer.py     # محلل السوق
│   ├── order_manager.py       # مدير الأوامر
│   ├── risk_manager.py        # مدير المخاطرة
│   ├── position_tracker.py    # متابعة الصفقات
│   ├── notification_service.py # خدمة الإشعارات
│   └── data_logger.py         # مسجل البيانات
│
├── strategies/
│   ├── smc_strategy.py        # استراتيجية Smart Money
│   ├── liquidity_engine.py    # هندسة السيولة
│   └── correlation_gate.py    # بوابة الارتباطات
│
├── models/
│   ├── trade.py               # نموذج الصفقة
│   ├── signal.py              # نموذج الإشارة
│   └── poi.py                 # نموذج منطقة الاهتمام
│
├── database/
│   ├── trades.db              # قاعدة بيانات SQLite
│   └── migrations/
│
├── tests/
│   ├── test_smc.py
│   ├── test_risk.py
│   └── backtest_results/
│
├── logs/
│   └── bot.log
│
├── docs/
│   └── README.md
│
├── requirements.txt
├── Dockerfile
└── docker-compose.yaml
```

---

## 4) الوحدات الأساسية

### 4.1) محرك البوت (bot_engine.py)

```python
"""
محرك البوت الرئيسي -- يدور كل شيء حوله
"""
import schedule
import time
from datetime import datetime

class GoldDragonBot:
    def __init__(self, config):
        self.config = config
        self.is_running = False
        self.analyzer = MarketAnalyzer(config)
        self.order_manager = OrderManager(config)
        self.risk_manager = RiskManager(config)
        self.notifier = NotificationService(config)
        self.tracker = PositionTracker(config)
        
    def start(self):
        """تشغيل البوت"""
        self.is_running = True
        self.notifier.send("🐉 Gold Dragon Bot Started")
        
        # جدولة المهام
        schedule.every(1).minutes.do(self.scan_market)
        schedule.every(5).minutes.do(self.check_positions)
        schedule.every(15).minutes.do(self.update_correlation)
        schedule.every().hour.at(":00").do(self.daily_audit)
        
        while self.is_running:
            schedule.run_pending()
            time.sleep(1)
    
    def scan_market(self):
        """فحص السوق كل دقيقة"""
        try:
            data = self.analyzer.fetch_data(timeframe="M15", bars=100)
            analysis = self.analyzer.gold_dragon_analysis(data)
            correlation = self.analyzer.check_correlation()
            acs = self.analyzer.calculate_acs(analysis, correlation)
            if acs >= 7:
                signal = self.analyzer.generate_signal(analysis, acs)
                self.evaluate_signal(signal)
        except Exception as e:
            self.notifier.send(f"⚠️ Error in scan: {str(e)}")
    
    def evaluate_signal(self, signal):
        """تقييم الإشارة واتخاذ القرار"""
        if not self.risk_manager.can_trade():
            self.notifier.send("🚫 Risk limit reached -- No new trades")
            return
        if self.analyzer.is_high_impact_news_within_30min():
            self.notifier.send("📰 High impact news soon -- Signal paused")
            return
        if not self.analyzer.is_inside_killzone():
            self.notifier.send("⏰ Outside Killzone -- Signal queued")
            return
        self.order_manager.execute(signal)
        self.notifier.send(f"✅ Trade executed: {signal.direction} at {signal.entry}")
    
    def check_positions(self):
        """مراقبة الصفقات المفتوحة كل 5 دقائق"""
        positions = self.tracker.get_open_positions()
        for pos in positions:
            if pos.should_partial_close_1():
                self.order_manager.partial_close(pos, 0.30)
                self.notifier.send(f"📊 Partial Close 1: {pos.ticket}")
            if pos.should_move_breakeven():
                self.order_manager.move_sl_to_breakeven(pos)
                self.notifier.send(f"🛡️ Breakeven: {pos.ticket}")
            if pos.should_trail():
                self.order_manager.update_trailing_stop(pos)
    
    def update_correlation(self):
        """تحديث بيانات الارتباطات كل 15 دقيقة"""
        self.analyzer.update_dxy_data()
        self.analyzer.update_us10y_data()
        self.analyzer.update_vix_data()
    
    def daily_audit(self):
        """تقرير التدقيق اليومي"""
        report = self.tracker.generate_daily_report()
        self.notifier.send(report)
        self.data_logger.save_report(report)
```

### 4.2) بروتوكول Gold Dragon (gold_dragon_core.py)

```python
"""
نواة بروتوكول Gold Dragon -- القلب النابض للبوت
"""
import pandas as pd
import numpy as np

class GoldDragonCore:
    """تنفيذ بروتوكول Gold Dragon Algorithm Pro v4"""
    def __init__(self):
        self.min_bars = 150
        self.fvg_threshold = 0.5
    
    def analyze(self, df_h1, df_m15, dxy_data=None, us10y_data=None):
        """التحليل الرئيسي -- يعيد قاموسا بالنتائج"""
        result = {
            'bias': None,
            'liquidity_map': {},
            'poi_list': [],
            'sweep_status': None,
            'acs': 0,
            'signal': None,
            'correlation': {},
            'alerts': []
        }
        result['bias'] = self.calculate_bias(df_h1)
        result['liquidity_map'] = self.map_liquidity(df_h1, df_m15)
        result['poi_list'] = self.find_poi(df_h1, df_m15, result['bias'])
        result['sweep_status'] = self.detect_sweep(df_m15, result['liquidity_map'])
        if dxy_data is not None:
            result['correlation'] = self.check_correlation_gate(dxy_data, us10y_data)
        result['acs'] = self.calculate_acs(result, dxy_data)
        if result['acs'] >= 7:
            result['signal'] = self.generate_signal(result)
            result['alerts'] = self.generate_alerts(result)
        return result
    
    def calculate_bias(self, df):
        """تحديد الاتجاه بناءً على HH/HL أو LH/LL"""
        highs = df['high'].values
        lows = df['low'].values
        swing_high = self.find_swing_highs(highs, lows)
        swing_low = self.find_swing_lows(highs, lows)
        if len(swing_high) < 2 or len(swing_low) < 2:
            return {'direction': 'neutral', 'confidence': 0}
        is_hh = swing_high[-1] > swing_high[-2]
        is_hl = swing_low[-1] > swing_low[-2]
        is_lh = swing_high[-1] < swing_high[-2]
        is_ll = swing_low[-1] < swing_low[-2]
        if is_hh and is_hl:
            return {'direction': 'bullish', 'confidence': 0.8}
        elif is_lh and is_ll:
            return {'direction': 'bearish', 'confidence': 0.8}
        else:
            return {'direction': 'ranging', 'confidence': 0.5}
    
    def map_liquidity(self, df_h1, df_m15):
        """رسم خريطة السيولة: BSL و SSL"""
        liquidity = {
            'bsl': [], 'ssl': [],
            'pdh': None, 'pdl': None,
            'pwh': None, 'pwl': None
        }
        liquidity['pdh'] = df_h1['high'].iloc[-24:].max()
        liquidity['pdl'] = df_h1['low'].iloc[-24:].min()
        liquidity['pwh'] = df_h1['high'].iloc[-120:].max()
        liquidity['pwl'] = df_h1['low'].iloc[-120:].min()
        highs_m15 = df_m15['high'].values
        lows_m15 = df_m15['low'].values
        liquidity['bsl'] = self.find_equal_levels(highs_m15, tolerance=0.5)
        liquidity['ssl'] = self.find_equal_levels(lows_m15, tolerance=0.5)
        return liquidity
    
    def find_poi(self, df_h1, df_m15, bias):
        """البحث عن مناطق الاهتمام المعتمدة"""
        poi_list = []
        obs = self.find_order_blocks(df_h1, bias['direction'])
        for ob in obs:
            poi_list.append({
                'type': 'OB',
                'level': ob['level'],
                'timeframe': 'H1',
                'confidence': ob['strength'],
                'reason': f"OB {bias['direction']} after displacement"
            })
        fvgs = self.find_fvg(df_h1, bias['direction'])
        for fvg in fvgs:
            poi_list.append({
                'type': 'FVG',
                'level': fvg['level'],
                'timeframe': 'H1',
                'confidence': 0.7,
                'reason': 'Unmitigated FVG'
            })
        poi_list = self.filter_poi_by_zone(poi_list, bias, df_h1)
        return poi_list
    
    def detect_sweep(self, df_m15, liquidity_map):
        """كشف Sweep للسيولة"""
        current_high = df_m15['high'].iloc[-1]
        current_low = df_m15['low'].iloc[-1]
        for bsl in liquidity_map['bsl']:
            if current_high > bsl and self.has_displacement(df_m15, 'bearish'):
                return {
                    'type': 'BSL_Sweep',
                    'level': bsl,
                    'status': 'confirmed',
                    'follow_up': self.check_choc_after_sweep(df_m15, 'bearish')
                }
        for ssl in liquidity_map['ssl']:
            if current_low < ssl and self.has_displacement(df_m15, 'bullish'):
                return {
                    'type': 'SSL_Sweep',
                    'level': ssl,
                    'status': 'confirmed',
                    'follow_up': self.check_choc_after_sweep(df_m15, 'bullish')
                }
        return {'type': 'none', 'status': 'waiting'}
    
    def calculate_acs(self, analysis, dxy_data):
        """حساب Advanced Confluence Score (0-10)"""
        acs = 0
        if analysis['bias']['confidence'] >= 0.8:
            acs += 2
        elif analysis['bias']['confidence'] >= 0.5:
            acs += 1
        best_poi = max(analysis['poi_list'], key=lambda x: x['confidence'], default=None)
        if best_poi and best_poi['confidence'] >= 0.8:
            acs += 2
        elif best_poi and best_poi['confidence'] >= 0.6:
            acs += 1
        if analysis['sweep_status']['status'] == 'confirmed':
            if analysis['sweep_status']['follow_up']:
                acs += 2
            else:
                acs += 1
        if self.is_killzone():
            acs += 1
        if analysis['sweep_status']['follow_up'] and len(analysis['poi_list']) > 0:
            acs += 2
        elif analysis['sweep_status']['follow_up']:
            acs += 1
        if not self.is_high_impact_news():
            acs += 1
        acs += 1  # Premium/Discount
        acs += 1  # Displacement size
        if dxy_data and analysis['correlation'].get('aligned', False):
            acs += 1
        acs += 1  # Psychology (default)
        return min(acs, 10)
    
    def generate_signal(self, analysis):
        """توليد إشارة التداول"""
        if analysis['acs'] < 7:
            return None
        best_poi = max(analysis['poi_list'], key=lambda x: x['confidence'])
        sweep = analysis['sweep_status']
        direction = 'buy' if sweep['type'] == 'SSL_Sweep' else 'sell'
        entry = best_poi['level']['entry']
        sl = best_poi['level']['stop']
        tp1 = best_poi['level']['tp1']
        tp2 = best_poi['level']['tp2']
        rr = abs(tp1 - entry) / abs(entry - sl)
        return {
            'direction': direction,
            'entry': entry, 'sl': sl,
            'tp1': tp1, 'tp2': tp2,
            'rr': rr, 'acs': analysis['acs'],
            'poi_type': best_poi['type'],
            'invalidation': sweep['level']
        }
    
    def is_killzone(self):
        """فحص إذا كنا داخل Killzone"""
        from datetime import datetime
        now = datetime.utcnow()
        hour = now.hour
        return (7 <= hour <= 10) or (12 <= hour <= 15)
    
    def is_high_impact_news(self):
        """فحص الأخبار العاجلة -- يتطلب تكامل API"""
        return False
```

### 4.3) مدير المخاطرة (risk_manager.py)

```python
"""
مدير المخاطرة -- القلب الحامي للبوت
"""
class RiskManager:
    def __init__(self, config):
        self.config = config
        self.max_risk_per_trade = config.get('max_risk_per_trade', 0.01)
        self.max_total_risk = config.get('max_total_risk', 0.04)
        self.max_drawdown = config.get('max_drawdown', 0.15)
        self.daily_loss_limit = config.get('daily_loss_limit', 0.05)
        self.daily_pnl = 0
        self.total_risk_exposure = 0
        self.consecutive_losses = 0
        self.is_locked = False
    
    def can_trade(self):
        """هل يمكن فتح صفقة جديدة"""
        if self.is_locked:
            return False
        if self.consecutive_losses >= 3:
            self.lock_for_hours(48)
            return False
        if self.total_risk_exposure >= self.max_total_risk:
            return False
        if self.daily_pnl <= -self.daily_loss_limit:
            self.lock_until_next_day()
            return False
        return True
    
    def calculate_position_size(self, account_balance, entry, stop_loss, risk_pct=None):
        """حساب حجم المركز باللوت"""
        if risk_pct is None:
            risk_pct = self.max_risk_per_trade
        risk_amount = account_balance * risk_pct
        sl_distance = abs(entry - stop_loss)
        pip_value = 1.0
        sl_pips = sl_distance / 0.01
        lot_size = risk_amount / (sl_pips * pip_value * 100)
        return round(lot_size, 2)
    
    def record_trade_result(self, pnl):
        """تسجيل نتيجة الصفقة"""
        self.daily_pnl += pnl
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
    
    def lock_for_hours(self, hours):
        """قفل البوت لعدد ساعات"""
        self.is_locked = True
        import threading
        threading.Timer(hours * 3600, self.unlock).start()
    
    def unlock(self):
        """فتح البوت"""
        self.is_locked = False
        self.consecutive_losses = 0
```

### 4.4) مدير الأوامر (order_manager.py)

```python
"""
مدير الأوامر -- يربط البوت مع MT5
"""
import MetaTrader5 as mt5

class OrderManager:
    def __init__(self, config):
        self.config = config
        self.symbol = 'XAUUSD'
        self.initialized = False
        self.initialize_mt5()
    
    def initialize_mt5(self):
        """تهيئة اتصال MT5"""
        if not mt5.initialize():
            raise Exception('MT5 initialization failed')
        login = self.config['mt5_login']
        password = self.config['mt5_password']
        server = self.config['mt5_server']
        if not mt5.login(login, password, server):
            raise Exception('MT5 login failed')
        self.initialized = True
    
    def execute(self, signal):
        """تنفيذ صفقة"""
        if not self.validate_signal(signal):
            return None
        order_type = mt5.ORDER_TYPE_BUY if signal['direction'] == 'buy' else mt5.ORDER_TYPE_SELL
        tick = mt5.symbol_info_tick(self.symbol)
        price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": signal["lot_size"],
            "type": order_type,
            "price": price,
            "sl": signal["sl"],
            "tp": signal["tp1"],
            "deviation": 10,
            "magic": 234000,
            "comment": f"GoldDragon_ACS{signal['acs']}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise Exception(f'Order failed: {result.retcode}')
        return result.order
    
    def validate_signal(self, signal):
        """التحقق من صحة الإشارة قبل التنفيذ"""
        required_fields = ['direction', 'entry', 'sl', 'tp1', 'lot_size', 'acs']
        return all(field in signal for field in required_fields)
```

### 4.5) خدمة الإشعارات (notification_service.py)

```python
"""
خدمة الإشعارات -- Telegram Bot
"""
import asyncio
from telegram import Bot

class NotificationService:
    def __init__(self, config):
        self.bot = Bot(token=config['telegram_token'])
        self.chat_id = config['telegram_chat_id']
    
    def send(self, message):
        """إرسال إشعار"""
        try:
            asyncio.run(self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode='Markdown'
            ))
        except Exception as e:
            print(f'Notification failed: {e}')
    
    def send_trade_alert(self, signal):
        """إشعار صفقة"""
        msg = f"""
🐉 *Gold Dragon Signal*

📊 Direction: {signal['direction'].upper()}
💰 Entry: {signal['entry']}
🛑 SL: {signal['sl']}
🎯 TP1: {signal['tp1']}
🎯 TP2: {signal['tp2']}
📈 RR: 1:{signal['rr']:.1f}
⭐ ACS: {signal['acs']}/10

⚠️ Trade at your own risk.
        """
        self.send(msg)
```

---

## 5) بروتوكول Gold Dragon في البوت

### 5.1) كيفية دمج Pro v4

```yaml
# config/settings.yaml

bot:
  name: "Gold Dragon Bot"
  version: "1.0.0"
  symbol: "XAUUSD"
  timeframes:
    analysis: "H1"
    trigger: "M15"
    confirmation: "M5"
  
gold_dragon:
  protocol_version: "Pro v4"
  min_acs: 7
  max_acs_for_alternative: 8
  killzone_only: true
  news_filter: true
  
  correlation_gate:
    enabled: true
    assets:
      - DXY
      - US10Y
      - VIX
      - SPX500
    
  psychology_gate:
    enabled: true
    questions_before_trade: 6
    fomo_filter: true
    revenge_lock: true
    
  adaptive_memory:
    enabled: true
    min_trades_for_adjustment: 20
    
  self_audit:
    enabled: true
    frequency: "daily"
    
  alert_protocol:
    enabled: true
    levels:
      - level_1: "POI proximity (+-20 pips)"
      - level_2: "Sweep imminent"
      - level_3: "Trigger confirmed"
      - level_4: "Killzone approaching"
      - level_5: "High impact news warning"
```

### 5.2) سير العمل (Workflow)

```
[بدء البوت]
    |
[جمع البيانات كل دقيقة]
    |
[تحليل Gold Dragon Core]
    |
[فحص Correlation Gate]
    |
[حساب ACS]
    |
[ACS < 7] -> [لا إشارة -- انتظار]
    |
[ACS >= 7] -> [فحص Psychology Gate]
    |
[Psychology = 0] -> [رفض -- إشعار: "أنت غاضب، لا تتداول"]
    |
[Psychology OK] -> [فحص Risk Manager]
    |
[Can Trade = False] -> [رفض -- إشعار: "Risk limit reached"]
    |
[Can Trade = True] -> [فحص Killzone]
    |
[Outside Killzone] -> [إشعار معلق -- "انتظر London/NY"]
    |
[Inside Killzone] -> [فحص الأخبار]
    |
[News within 30min] -> [تجميد -- إشعار: "أخبار وشيكة"]
    |
[No News] -> [تنفيذ الصفقة]
    |
[إرسال إشعار Telegram]
    |
[مراقبة كل 5 دقائق]
    |
[Partial Close / Breakeven / Trailing Stop]
    |
[إغلاق الصفقة]
    |
[تسجيل في Adaptive Memory]
    |
[العودة للبداية]
```

---

## 6) إدارة المخاطرة الآلية

### 6.1) القواعد المبرمجة

| القاعدة | الشرط | الإجراء |
|---|---|---|
| **Max Risk per Trade** | 2% | رفض الصفقة إن تجاوزت |
| **Max Total Risk** | 4% | رفض أي صفقة جديدة |
| **Max Drawdown** | 15% | إيقاف البوت + إشعار |
| **Daily Loss Limit** | 5% | إيقاف حتى اليوم التالي |
| **Consecutive Losses** | 3 | إيقاف 48 ساعة |
| **Min RR** | 1:3 | رفض الصفقة |
| **Counter-Trend RR** | 1:5 | رفض إن كانت أقل |
| **News Freeze** | +-30 min | تجميد جميع الأوامر |
| **Killzone Only** | London/NY | رفض خارج الجلسات |

### 6.2) Kill Switch (زر القتل)

```python
class KillSwitch:
    def __init__(self):
        self.active = False
        self.reason = None
    
    def trigger(self, reason):
        """تفعيل Kill Switch"""
        self.active = True
        self.reason = reason
        order_manager.close_all_positions()
        order_manager.cancel_all_orders()
        notifier.send(f"🚨 KILL SWITCH ACTIVATED: {reason}")
        logger.critical(f'Kill Switch: {reason}')
        bot_engine.stop()
    
    def auto_check(self, metrics):
        """فحص تلقائي"""
        if metrics['drawdown'] > 0.20:
            self.trigger("Drawdown exceeded 20%")
        elif metrics['daily_loss'] > 0.10:
            self.trigger("Daily loss exceeded 10%")
        elif metrics['consecutive_losses'] > 5:
            self.trigger("5 consecutive losses")
```

---

## 7) النشر على Claude Code

### 7.1) لماذا Claude Code
Claude Code هو بيئة تطوير متكاملة تتيح:
- تشغيل Python مباشرة
- الوصول إلى ملفات النظام
- تنفيذ أوامر Terminal
- تكامل مع APIs

### 7.2) خطوات النشر

**الخطوة 1: إعداد البيئة**
```bash
mkdir gold_dragon_bot
cd gold_dragon_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**الخطوة 2: إعداد MT5**
- تثبيت MT5 على VPS
- تفعيل "Allow Automated Trading"
- تفعيل "Allow DLL imports"
- كتابة بيانات تسجيل الدخول في config/settings.yaml

**الخطوة 3: إعداد Telegram Bot**
- إنشاء بوت عبر @BotFather
- الحصول على Token
- الحصول على Chat ID
- إضافتهما للإعدادات

**الخطوة 4: تشغيل البوت**
```bash
python src/main.py
```

**الخطوة 5: المراقبة**
```bash
tail -f logs/bot.log
python src/monitor.py
```

### 7.3) Dockerfile (للنشر السحابي)

```dockerfile
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "src/main.py"]
```

---

## 8) الاختبار والمحاكاة

### 8.1) الاختبار على حساب تجريبي (Demo)

**المدة الإلزامية:** 3 أشهر على الأقل

| المرحلة | المدة | الهدف |
|---|---|---|
| **الشهر 1** | 4 أسابيع | اختبار البنية + إصلاح الأخطاء |
| **الشهر 2** | 4 أسابيع | اختبار Gold Dragon Core + تعديل المعايير |
| **الشهر 3** | 4 أسابيع | اختبار كامل + تقييم الأداء |

### 8.2) مؤشرات الأداء المطلوبة

| المؤشر | الهدف | الحد الأدنى للقبول |
|---|---|---|
| **نسبة النجاح** | 55% | >= 45% |
| **RR المتوسط** | 1:3 | >= 1:2.5 |
| **Profit Factor** | 1.5 | >= 1.2 |
| **Max Drawdown** | 10% | <= 15% |
| **Sharpe Ratio** | 1.0 | >= 0.5 |
| **الصفقات الشهرية** | 20-30 | >= 10 |
| **ACS المتوسط** | 7.5 | >= 6.5 |

### 8.3) Backtesting

```python
from backtesting import Backtest, Strategy

class GoldDragonStrategy(Strategy):
    def init(self):
        self.core = GoldDragonCore()
    def next(self):
        analysis = self.core.analyze(self.data.df)
        if analysis['signal'] and analysis['acs'] >= 7:
            if analysis['signal']['direction'] == 'buy':
                self.buy(sl=analysis['signal']['sl'], tp=analysis['signal']['tp1'])
            else:
                self.sell(sl=analysis['signal']['sl'], tp=analysis['signal']['tp1'])

bt = Backtest(data, GoldDragonStrategy, cash=1000, commission=0.0002)
stats = bt.run()
bt.plot()
```

---

## 9) الصيانة والمراقبة

### 9.1) المراقبة اليومية

| الوقت | المهمة |
|---|---|
| **8:00 صباحا** | فحص الأخبار اليومية -- تعديل الإعدادات |
| **10:00 صباحا** | بداية London Killzone -- مراقبة أولى |
| **15:00 مساء** | بداية NY Killzone -- مراقبة رئيسية |
| **22:00 مساء** | نهاية الجلسة -- مراجعة يومية |
| **منتصف الليل** | إعادة تعيين العدادات اليومية |

### 9.2) الصيانة الأسبوعية

- [ ] مراجعة سجل الأداء (Adaptive Memory)
- [ ] تحديث "الثقة المعدلة" لكل POI
- [ ] مراجعة نسبة النجاح الفعلية vs المتوقعة
- [ ] فحص VPS (CPU, RAM, Disk)
- [ ] تحديث مكتبات Python
- [ ] مراجعة إعدادات الوسيط (Spread, Slippage)

### 9.3) الصيانة الشهرية

- [ ] Self-Audit Protocol كامل
- [ ] مراجعة Correlation Gate (هل الارتباطات تغيرت؟)
- [ ] تحديث Seasonality (هل الأنماط الموسمية تغيرت؟)
- [ ] تقييم Kill Switch (هل تفعّل؟ لماذا؟)
- [ ] قرار: الاستمرار / التعديل / الإيقاف

---

## 10) Checklist قبل النشر الحي (Live Trading)

### ✅ التقني
- [ ] البوت يعمل على Demo لمدة 3 أشهر
- [ ] نسبة النجاح >= 45%
- [ ] Max Drawdown <= 15%
- [ ] Kill Switch يعمل (اختبره يدويا)
- [ ] الإشعارات تصل إلى Telegram
- [ ] VPS يعمل 24/7 بدون انقطاع
- [ ] نسخ احتياطي يومي لقاعدة البيانات
- [ ] إعدادات الوسيط صحيحة (Spread, Commission, Slippage)

### ✅ المالي
- [ ] رأس المال "مستهلكة" -- ليست لدفع فواتير
- [ ] رأس المال <= 500$ (للبدء)
- [ ] خطة للخسارة الكاملة ("ماذا لو أصبح 0$؟")
- [ ] فهم للضرائب في بلد الإقامة
- [ ] سحب الأرباح شهريا (لا تتراكم)

### ✅ النفسي
- [ ] فهم أن البوت قد يخسر
- [ ] عدم المراقبة المفرطة (Check كل ساعة -- ليس كل دقيقة)
- [ ] عدم التدخل يدويا إلا في حالات الطوارئ
- [ ] قبول أن "لا صفقة" أفضل من "صفقة سيئة"

### ✅ القانوني
- [ ] قراءة شروط الوسيط بخصوص البوتات
- [ ] بعض الوسطاء يمنعون البوتات -- تأكد
- [ ] الالتزام بقوانين الضرائب
- [ ] عدم استخدام البوت لغسل الأموال (AML)

---

## 📚 مصادر إضافية

| المورد | الرابط | الوصف |
|---|---|---|
| **MetaTrader5 Python** | https://www.mql5.com/en/docs/integration/python_reference | توثيق MT5 لـ Python |
| **Backtrader** | https://www.backtrader.com/ | إطار Backtesting |
| **ccxt** | https://github.com/ccxt/ccxt | مكتبة تداول لـ 100+ بورصة |
| **Telegram Bot API** | https://core.telegram.org/bots/api | توثيق بوت Telegram |
| **Gold Dragon Pro v4** | [الملف](sandbox:///mnt/agents/output/Gold_Dragon_Algorithm_Pro_v4_Ultimate.md) | بروتوكول التحليل |
| **Gold Dragon Challenge** | [الملف](sandbox:///mnt/agents/output/Gold_Dragon_Challenge_Plan.md) | خطة 100$ -> 1000$ |

---

## 🎯 الخلاصة

بناء بوت تداول آلي ليس "زر سحري" للثراء. هو **أداة** تنفذ بروتوكولا تصممه أنت. الجودة تعتمد على:
1. **جودة البروتوكول** (Gold Dragon Pro v4)
2. **جودة الاختبار** (3 أشهر Demo)
3. **جودة إدارة المخاطرة** (Kill Switch + Risk Manager)
4. **جودة الانضباط** (لا تتدخل يدويا)

> **"البوت ينفذ، الإنسان يراقب، البروتوكول يحمي."**

---

**الإصدار:** Gold Dragon Trading Bot Guide v1.0  
**التوافق:** Gold Dragon Algorithm Pro v4  
**المستوى:** Institutional Grade  
**آخر تحديث:** 2026
