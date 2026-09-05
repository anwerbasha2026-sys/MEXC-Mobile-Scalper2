import sys
from pathlib import Path
import time
import sqlite3
import requests
import hmac
import hashlib
import threading
from datetime import datetime
from urllib.parse import urlencode
from concurrent.futures import ThreadPoolExecutor, as_completed

import kivy
kivy.require('2.1.0')

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.graphics import Color, Rectangle
from kivy.core.text import LabelBase
from kivy.resources import resource_add_path

# ==========================================
# Arabic font support
# ==========================================
BASE_DIR = Path(__file__).resolve().parent
FONT_DIR = BASE_DIR / "fonts"
ARABIC_FONT = FONT_DIR / "NotoSansArabic.ttf"

if FONT_DIR.exists():
    resource_add_path(str(FONT_DIR))

if ARABIC_FONT.exists():
    LabelBase.register(
        name="NotoArabic",
        fn_regular=str(ARABIC_FONT),
        fn_bold=str(ARABIC_FONT),
    )
else:
    # The app still starts if the font is accidentally missing.
    print(f"Arabic font not found: {ARABIC_FONT}")


class ArabicLabel(Label):
    def __init__(self, **kwargs):
        kwargs.setdefault("font_name", "NotoArabic" if ARABIC_FONT.exists() else "Roboto")
        kwargs.setdefault("font_size", "15sp")
        super().__init__(**kwargs)


class ArabicButton(Button):
    def __init__(self, **kwargs):
        kwargs.setdefault("font_name", "NotoArabic" if ARABIC_FONT.exists() else "Roboto")
        super().__init__(**kwargs)


class ArabicTextInput(TextInput):
    def __init__(self, **kwargs):
        kwargs.setdefault("font_name", "NotoArabic" if ARABIC_FONT.exists() else "Roboto")
        super().__init__(**kwargs)

# ==========================================
# 1. إدارة قاعدة البيانات (SQLite)
# ==========================================
DB_NAME = "bot_data.db"

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_position (
                id INTEGER PRIMARY KEY,
                symbol TEXT,
                entry_price REAL,
                amount REAL,
                tp_percent REAL,
                sl_percent REAL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS closed_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                entry_price REAL,
                exit_price REAL,
                amount REAL,
                pnl_usd REAL,
                pnl_percent REAL,
                reason TEXT,
                timestamp TEXT
            )
        """)
        conn.commit()

def save_setting(key, value):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
            conn.commit()
    except Exception as e:
        print(f"DB Error (save_setting): {e}")

def get_setting(key, default=""):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
            row = cursor.fetchone()
            return row[0] if row else default
    except Exception:
        return default

def save_active_position(symbol, entry_price, amount, tp_percent, sl_percent):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM active_position")
            cursor.execute("""
                INSERT INTO active_position (id, symbol, entry_price, amount, tp_percent, sl_percent)
                VALUES (1, ?, ?, ?, ?, ?)
            """, (symbol, entry_price, amount, tp_percent, sl_percent))
            conn.commit()
    except Exception as e:
        print(f"DB Error (save_active_position): {e}")

def clear_active_position():
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM active_position")
            conn.commit()
    except Exception as e:
        print(f"DB Error (clear_active_position): {e}")

def get_active_position():
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT symbol, entry_price, amount, tp_percent, sl_percent FROM active_position WHERE id=1")
            row = cursor.fetchone()
            if row:
                return {
                    "symbol": row[0],
                    "entry_price": row[1],
                    "amount": row[2],
                    "tp_percent": row[3],
                    "sl_percent": row[4]
                }
    except Exception:
        pass
    return None

def record_closed_trade(symbol, entry_price, exit_price, amount, pnl_usd, pnl_percent, reason):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("""
                INSERT INTO closed_trades (symbol, entry_price, exit_price, amount, pnl_usd, pnl_percent, reason, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (symbol, entry_price, exit_price, amount, pnl_usd, pnl_percent, reason, now_str))
            conn.commit()
    except Exception as e:
        print(f"DB Error (record_closed_trade): {e}")

def get_all_closed_trades():
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT symbol, entry_price, exit_price, amount, pnl_usd, pnl_percent, reason, timestamp FROM closed_trades ORDER BY id DESC")
            return cursor.fetchall()
    except Exception:
        return []

# ==========================================
# 2. حماية الطلبات وتخزين الاتجاه (Rate Limit & Cache)
# ==========================================
SYMBOL_RULES_CACHE = {}
TREND_CACHE = {}
CACHE_TTL = 300
FEE_RATE = 0.001  # عمولة التداول المتوقعة (0.1% للـ Taker)

def safe_mexc_request(url, params=None, headers=None, max_retries=3):
    backoff = 1.0
    for _ in range(max_retries):
        try:
            res = requests.get(url, params=params, headers=headers, timeout=5)
            if res.status_code == 429:
                retry_after = int(res.headers.get("Retry-After", backoff))
                time.sleep(retry_after)
                backoff *= 2
                continue
            if res.status_code == 200:
                return res.json()
        except Exception:
            time.sleep(backoff)
            backoff *= 2
    return None

def update_exchange_info():
    global SYMBOL_RULES_CACHE
    data = safe_mexc_request("https://api.mexc.com/api/v3/exchangeInfo")
    if data and "symbols" in data:
        for s in data["symbols"]:
            SYMBOL_RULES_CACHE[s["symbol"]] = s.get("baseAssetPrecision", 4)

def get_base_precision(symbol):
    clean_symbol = symbol.replace("/", "").upper()
    return SYMBOL_RULES_CACHE.get(clean_symbol, 4)

def get_top_200_symbols():
    data = safe_mexc_request("https://api.mexc.com/api/v3/ticker/24hr")
    if data and isinstance(data, list):
        usdt_pairs = [item for item in data if item.get('symbol', '').endswith('USDT')]
        usdt_pairs.sort(key=lambda x: float(x.get('quoteVolume', 0)), reverse=True)
        return [item['symbol'] for item in usdt_pairs[:200]]
    return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]

def get_mexc_real_price(symbol):
    formatted_symbol = symbol.replace("/", "").upper()
    data = safe_mexc_request(f"https://api.mexc.com/api/v3/ticker/price?symbol={formatted_symbol}")
    if data and 'price' in data:
        return float(data['price'])
    return None

def get_symbol_free_balance(symbol, api_key, secret_key):
    try:
        asset_name = symbol.replace("USDT", "").replace("/", "").upper()
        url_bal = "https://api.mexc.com/api/v3/account"
        ts = int(time.time() * 1000)
        p_bal = {"timestamp": ts, "recvWindow": 60000}
        q_bal = urlencode(p_bal)
        sig_bal = hmac.new(secret_key.encode('utf-8'), q_bal.encode('utf-8'), hashlib.sha256).hexdigest()
        p_bal["signature"] = sig_bal
        
        res_bal = requests.get(url_bal, headers={"X-MEXC-APIKEY": api_key}, params=p_bal, timeout=5)
        if res_bal.status_code == 200:
            balances = res_bal.json().get("balances", [])
            for b in balances:
                if b["asset"] == asset_name:
                    return float(b["free"])
    except Exception as e:
        print(f"خطأ جلب الرصيد: {e}")
    return 0.0

def place_mexc_buy_order(symbol, amount_usd, api_key, secret_key):
    if not api_key or not secret_key:
        return False, "لم يتم إدخال API Key/Secret", 0.0
    try:
        url = "https://api.mexc.com/api/v3/order"
        timestamp = int(time.time() * 1000)
        formatted_amount = f"{float(amount_usd):.2f}"
        
        params = {
            "symbol": symbol.replace("/", "").upper(),
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": formatted_amount,
            "recvWindow": 60000,
            "timestamp": timestamp
        }
        
        query_string = urlencode(params)
        signature = hmac.new(secret_key.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
        params["signature"] = signature
        
        headers = {"X-MEXC-APIKEY": api_key, "Content-Type": "application/json"}
        response = requests.post(url, headers=headers, params=params, timeout=5)
        res_data = response.json()
        
        if response.status_code == 200 and "orderId" in res_data:
            time.sleep(0.5)
            real_price = get_mexc_real_price(symbol)
            return True, f"Order ID: {res_data['orderId']}", real_price
        else:
            return False, res_data.get("msg", str(res_data)), 0.0
    except Exception as e:
        return False, str(e), 0.0

def place_mexc_sell_order_market(symbol, api_key, secret_key):
    try:
        free_qty = get_symbol_free_balance(symbol, api_key, secret_key)
        if free_qty <= 0:
            return True, "الصفقة مغلقة بالفعل على المنصة (الرصيد 0)"

        url_order = "https://api.mexc.com/api/v3/order"
        precision = get_base_precision(symbol)
        qty_str = f"{free_qty:.{precision}f}"
        
        p_order = {
            "symbol": symbol.replace("/", "").upper(),
            "side": "SELL",
            "type": "MARKET",
            "quantity": qty_str,
            "recvWindow": 60000,
            "timestamp": int(time.time() * 1000)
        }
        q_order = urlencode(p_order)
        sig_order = hmac.new(secret_key.encode('utf-8'), q_order.encode('utf-8'), hashlib.sha256).hexdigest()
        p_order["signature"] = sig_order
        
        res_sell = requests.post(url_order, headers={"X-MEXC-APIKEY": api_key}, params=p_order, timeout=5)
        res_data = res_sell.json()
        
        if res_sell.status_code == 200 and "orderId" in res_data:
            return True, "تم تنفيذ أمر البيع المباشر على المنصة"
        else:
            return False, f"رفض المنصة: {res_data.get('msg', str(res_data))}"
    except Exception as e:
        return False, f"خطأ الاتصال: {str(e)}"

# ==========================================
# 3. التحليل الفني المؤكد
# ==========================================
def calculate_ema_series(data, period):
    if len(data) < period:
        return []
    ema = []
    multiplier = 2 / (period + 1)
    sma = sum(data[:period]) / period
    ema.append(sma)
    for price in data[period:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema

def check_ema200_trend(formatted_symbol, interval):
    now = time.time()
    cache_key = (formatted_symbol, interval)
    if cache_key in TREND_CACHE:
        result, timestamp = TREND_CACHE[cache_key]
        if now - timestamp < CACHE_TTL:
            return result

    data = safe_mexc_request(f"https://api.mexc.com/api/v3/klines?symbol={formatted_symbol}&interval={interval}&limit=300")
    if not data or not isinstance(data, list):
        return False
    
    closes = [float(k[4]) for k in data]
    if len(closes) < 200:
        return False
    
    ema200_series = calculate_ema_series(closes, 200)
    res = closes[-2] > ema200_series[-2] if ema200_series else False
    
    TREND_CACHE[cache_key] = (res, now)
    return res

def check_trade_conditions_from_main(symbol):
    try:
        formatted_symbol = symbol.replace("/", "").upper()
        if not (check_ema200_trend(formatted_symbol, "5m") and 
                check_ema200_trend(formatted_symbol, "15m") and 
                check_ema200_trend(formatted_symbol, "60m")):
            return False, 0.0, "الاتجاه العام غير صاعد"

        klines = safe_mexc_request(f"https://api.mexc.com/api/v3/klines?symbol={formatted_symbol}&interval=5m&limit=300")
        if not klines or len(klines) < 200:
            return False, 0.0, "بيانات الشموع غير كافية"

        closes = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]

        current_price = closes[-1]
        ema9_series = calculate_ema_series(closes, 9)
        ema21_series = calculate_ema_series(closes, 21)
        ema200_series = calculate_ema_series(closes, 200)

        has_recent_crossover = (ema9_series[-3] <= ema21_series[-3]) and (ema9_series[-2] > ema21_series[-2])
        is_ema_aligned = (ema9_series[-2] > ema21_series[-2]) and (ema21_series[-2] > ema200_series[-2])

        total_vol = sum(volumes[-21:-1])
        tp_vol = sum(((highs[i] + lows[i] + closes[i]) / 3) * volumes[i] for i in range(-21, -1))
        vwap = tp_vol / total_vol if total_vol > 0 else closes[-2]
        is_above_vwap = closes[-2] > vwap

        avg_vol = sum(volumes[-101:-2]) / 99
        is_volume_high = volumes[-2] > (avg_vol * 2)

        if has_recent_crossover and is_ema_aligned and is_above_vwap and is_volume_high:
            return True, current_price, "تحقق شروط الاقتناص المؤكدة"
        else:
            return False, current_price, "شروط غير مكتملة"

    except Exception as e:
        return False, 0.0, f"خطأ: {str(e)}"

# ==========================================
# 4. واجهة المستخدم والتنفيذ عبر Kivy
# ==========================================
class MEXCScalpApp(App):
    def build(self):
        self.title = "MEXC Professional Execution Scalper"
        init_db()

        self.is_scanning = False
        self.is_monitoring = False
        self.active_position = None

        # Main Layout
        root = ScrollView(size_hint=(1, 1))
        main_layout = BoxLayout(orientation='vertical', size_hint_y=None, padding=10, spacing=10)
        main_layout.bind(minimum_height=main_layout.setter('height'))

        # API Credentials Section
        api_box = BoxLayout(orientation='vertical', size_hint_y=None, height=140, spacing=5)
        api_box.add_widget(ArabicLabel(text="🔑 حساب MEXC API", font_size='18sp', bold=True, color=(0, 0.7, 1, 1), size_hint_y=None, height=30))
        
        self.api_key_input = ArabicTextInput(text=get_setting("api_key", ""), hint_text="API Key", multiline=False, size_hint_y=None, height=40)
        self.api_key_input.bind(text=lambda instance, val: save_setting("api_key", val.strip()))
        api_box.add_widget(self.api_key_input)

        self.secret_key_input = ArabicTextInput(text=get_setting("secret_key", ""), hint_text="Secret Key", password=True, multiline=False, size_hint_y=None, height=40)
        self.secret_key_input.bind(text=lambda instance, val: save_setting("secret_key", val.strip()))
        api_box.add_widget(self.secret_key_input)
        
        main_layout.add_widget(api_box)

        # Risk Management Controls
        risk_box = GridLayout(cols=2, size_hint_y=None, height=130, spacing=5)
        
        risk_box.add_widget(ArabicLabel(text="مبلغ الصفقة ($):"))
        self.amount_input = ArabicTextInput(text=get_setting("amount", "109"), multiline=False)
        self.amount_input.bind(text=lambda instance, val: save_setting("amount", val))
        risk_box.add_widget(self.amount_input)

        risk_box.add_widget(ArabicLabel(text="الهدف (TP %):"))
        self.tp_input = ArabicTextInput(text=get_setting("tp", "1.5"), multiline=False)
        self.tp_input.bind(text=lambda instance, val: save_setting("tp", val))
        risk_box.add_widget(self.tp_input)

        risk_box.add_widget(ArabicLabel(text="الوقف (SL %):"))
        self.sl_input = ArabicTextInput(text=get_setting("sl", "2.0"), multiline=False)
        self.sl_input.bind(text=lambda instance, val: save_setting("sl", val))
        risk_box.add_widget(self.sl_input)

        main_layout.add_widget(risk_box)

        # Action Buttons
        btn_box = BoxLayout(orientation='vertical', size_hint_y=None, height=150, spacing=8)
        
        self.btn_start = ArabicButton(text="بدء البحث التلقائي", background_color=(0.1, 0.5, 0.8, 1), bold=True)
        self.btn_start.bind(on_press=self.start_scanning)
        btn_box.add_widget(self.btn_start)

        self.btn_stop_scan = ArabicButton(text="إيقاف البحث", background_color=(0.9, 0.5, 0, 1), disabled=True, bold=True)
        self.btn_stop_scan.bind(on_press=self.stop_scanning)
        btn_box.add_widget(self.btn_stop_scan)

        self.btn_close = ArabicButton(text="إغلاق الصفقة فوراً", background_color=(0.8, 0.2, 0.2, 1), disabled=True, bold=True)
        self.btn_close.bind(on_press=self.close_position_manual)
        btn_box.add_widget(self.btn_close)

        main_layout.add_widget(btn_box)

        # Live PnL Display Card
        pnl_box = BoxLayout(orientation='vertical', size_hint_y=None, height=80, spacing=5)
        self.lbl_active_symbol = ArabicLabel(text="العملة: --", font_size='16sp', bold=True)
        self.lbl_pnl_info = ArabicLabel(text="$0.00 (0.00%)", font_size='20sp', bold=True, color=(1, 1, 1, 1))
        pnl_box.add_widget(self.lbl_active_symbol)
        pnl_box.add_widget(self.lbl_pnl_info)
        main_layout.add_widget(pnl_box)

        # Log Section
        main_layout.add_widget(ArabicLabel(text="📝 السجل المباشر", font_size='16sp', bold=True, size_hint_y=None, height=30))
        self.log_label = ArabicLabel(text="جاهز للبدء...\n", size_hint_y=None, halign='left', valign='top')
        self.log_label.bind(size=self.log_label.setter('text_size'))
        self.log_label.bind(minimum_height=self.log_label.setter('height'))
        
        log_scroll = ScrollView(size_hint_y=None, height=180)
        log_scroll.add_widget(self.log_label)
        main_layout.add_widget(log_scroll)

        # History Table Section
        main_layout.add_widget(ArabicLabel(text="📜 سجل الصفقات المنتهية", font_size='16sp', bold=True, size_hint_y=None, height=30))
        self.history_layout = GridLayout(cols=4, size_hint_y=None, spacing=2)
        self.history_layout.bind(minimum_height=self.history_layout.setter('height'))
        
        history_scroll = ScrollView(size_hint_y=None, height=200)
        history_scroll.add_widget(self.history_layout)
        main_layout.add_widget(history_scroll)

        root.add_widget(main_layout)

        # Load Saved States
        self.load_closed_trades_to_ui()
        self.check_and_recover_position()

        return root

    def append_log(self, message):
        def _update(dt):
            self.log_label.text += f"{message}\n"
        Clock.schedule_once(_update)

    # ==========================================
    # Operations & Threads
    # ==========================================
    def start_scanning(self, instance=None):
        api = self.api_key_input.text.strip()
        secret = self.secret_key_input.text.strip()
        if not api or not secret:
            self.append_log("[تنبيه] أدخل مفاتيح API أولاً.")
            return

        self.btn_start.disabled = True
        self.btn_stop_scan.disabled = False
        self.is_scanning = True
        self.append_log("[بدء] البحث الآمن عن فرصة جديدة...")

        threading.Thread(target=self._run_scanner, daemon=True).start()

    def stop_scanning(self, instance=None):
        self.is_scanning = False
        self.btn_start.disabled = False
        self.btn_stop_scan.disabled = True
        self.append_log("[إيقاف] تم إيقاف عملية البحث.")

    def _run_scanner(self):
        update_exchange_info()
        symbols = get_top_200_symbols()
        self.append_log(f"بدء الفحص المنظم لـ {len(symbols)} عملة...")

        while self.is_scanning:
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {}
                for sym in symbols:
                    if not self.is_scanning:
                        break
                    futures[executor.submit(check_trade_conditions_from_main, sym)] = sym
                    time.sleep(0.04)

                for future in as_completed(futures):
                    if not self.is_scanning:
                        break
                    symbol = futures[future]
                    try:
                        is_valid, price, msg = future.result()
                        if is_valid and self.is_scanning:
                            self.append_log(f"[🎯 اقتناص!] {symbol} | السعر: ${price:.4f}")
                            self.is_scanning = False
                            Clock.schedule_once(lambda dt: self.execute_snipe(symbol, price))
                            return
                    except Exception:
                        pass
            time.sleep(1)

    def execute_snipe(self, symbol, approx_price):
        self.btn_stop_scan.disabled = True
        try:
            amount = float(self.amount_input.text)
            tp = float(self.tp_input.text)
            sl = float(self.sl_input.text)
        except ValueError:
            amount, tp, sl = 109.0, 1.5, 2.0

        api_k = self.api_key_input.text.strip()
        sec_k = self.secret_key_input.text.strip()

        self.append_log(f"[شراء] إرسال أمر شراء {symbol} بمبلغ ${amount}...")
        
        def _buy_task():
            success, order_msg, entry_price = place_mexc_buy_order(symbol, amount, api_k, sec_k)
            if not success or entry_price <= 0:
                self.append_log(f"[🛑 فشل الشراء] {order_msg}")
                Clock.schedule_once(lambda dt: self.start_scanning())
                return

            self.active_position = {
                "symbol": symbol, "entry_price": entry_price, "amount": amount,
                "tp_percent": tp, "sl_percent": sl
            }
            save_active_position(symbol, entry_price, amount, tp, sl)

            self.append_log(f"[🎯 تم الشراء] {symbol} | سعر الدخول: ${entry_price:.4f}")
            Clock.schedule_once(lambda dt: self._setup_active_ui(symbol))
            self.start_ticker_thread()

        threading.Thread(target=_buy_task, daemon=True).start()

    def _setup_active_ui(self, symbol):
        self.lbl_active_symbol.text = f"العملة: {symbol}"
        self.btn_close.disabled = False

    def start_ticker_thread(self):
        self.is_monitoring = True
        threading.Thread(target=self._run_ticker, daemon=True).start()

    def _run_ticker(self):
        pos = self.active_position
        if not pos:
            return

        while self.is_monitoring:
            current_price = get_mexc_real_price(pos['symbol'])
            if current_price and pos['entry_price'] > 0:
                gross_pnl_percent = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
                pnl_percent = gross_pnl_percent - (FEE_RATE * 2 * 100)
                pnl_usd = pos['amount'] * (pnl_percent / 100)

                Clock.schedule_once(lambda dt, usd=pnl_usd, pct=pnl_percent: self.update_live_pnl(usd, pct))

                if pnl_percent >= pos['tp_percent']:
                    self.is_monitoring = False
                    Clock.schedule_once(lambda dt: self.handle_auto_close("TP"))
                    break

                if pnl_percent <= -pos['sl_percent']:
                    self.is_monitoring = False
                    Clock.schedule_once(lambda dt: self.handle_auto_close("SL"))
                    break

            time.sleep(1.0)

    def update_live_pnl(self, pnl_usd, pnl_percent):
        color = (0.2, 0.8, 0.2, 1) if pnl_usd >= 0 else (0.9, 0.2, 0.2, 1)
        prefix = "+" if pnl_usd >= 0 else ""
        self.lbl_pnl_info.text = f"{prefix}${pnl_usd:.2f} ({prefix}{pnl_percent:.2f}%)"
        self.lbl_pnl_info.color = color

    def handle_auto_close(self, reason_type):
        pos = self.active_position
        if not pos:
            return

        symbol = pos['symbol']
        api_k = self.api_key_input.text.strip()
        sec_k = self.secret_key_input.text.strip()

        self.append_log(f"[تنفيذ الإغلاق] جاري إرسال أمر بيع {symbol} ({reason_type}) المباشر...")

        def _close_task():
            success, sell_msg = place_mexc_sell_order_market(symbol, api_k, sec_k)
            exit_price = get_mexc_real_price(symbol) or pos['entry_price']
            gross_pnl_percent = ((exit_price - pos['entry_price']) / pos['entry_price']) * 100
            pnl_percent = gross_pnl_percent - (FEE_RATE * 2 * 100)
            pnl_usd = pos['amount'] * (pnl_percent / 100)

            record_closed_trade(symbol, pos['entry_price'], exit_price, pos['amount'], pnl_usd, pnl_percent, reason_type)
            clear_active_position()
            self.active_position = None

            self.append_log(f"[✅ تم البيع/تأكيد الإغلاق] {symbol} | النتيجة الصافية: ${pnl_usd:.2f} ({pnl_percent:.2f}%)")
            Clock.schedule_once(lambda dt: self._reset_position_ui())
            Clock.schedule_once(lambda dt: self.load_closed_trades_to_ui())
            Clock.schedule_once(lambda dt: self.start_scanning())

        threading.Thread(target=_close_task, daemon=True).start()

    def close_position_manual(self, instance=None):
        self.is_monitoring = False
        pos = self.active_position
        if not pos:
            return

        symbol = pos['symbol']
        api_k = self.api_key_input.text.strip()
        sec_k = self.secret_key_input.text.strip()

        def _manual_close_task():
            success, sell_msg = place_mexc_sell_order_market(symbol, api_k, sec_k)
            exit_price = get_mexc_real_price(symbol) or pos['entry_price']
            gross_pnl_percent = ((exit_price - pos['entry_price']) / pos['entry_price']) * 100
            pnl_percent = gross_pnl_percent - (FEE_RATE * 2 * 100)
            pnl_usd = pos['amount'] * (pnl_percent / 100)

            record_closed_trade(symbol, pos['entry_price'], exit_price, pos['amount'], pnl_usd, pnl_percent, "MANUAL")
            clear_active_position()
            self.active_position = None

            self.append_log(f"[إغلاق يدوي] تم تنظيف الصفقة واستعادة التداول.")
            Clock.schedule_once(lambda dt: self._reset_position_ui())
            Clock.schedule_once(lambda dt: self.load_closed_trades_to_ui())

        threading.Thread(target=_manual_close_task, daemon=True).start()

    def _reset_position_ui(self):
        self.btn_close.disabled = True
        self.lbl_active_symbol.text = "العملة: --"
        self.lbl_pnl_info.text = "$0.00 (0.00%)"
        self.lbl_pnl_info.color = (1, 1, 1, 1)

    def check_and_recover_position(self):
        saved_pos = get_active_position()
        if saved_pos:
            self.append_log(f"[استعادة] متابعة الصفقة المفتوحة: {saved_pos['symbol']}")
            self.active_position = saved_pos
            self._setup_active_ui(saved_pos['symbol'])
            self.btn_start.disabled = True
            self.start_ticker_thread()

    def load_closed_trades_to_ui(self):
        self.history_layout.clear_widgets()
        
        # Headers
        headers = ["الزوج", "الربح ($)", "النسبة (%)", "السبب"]
        for h in headers:
            self.history_layout.add_widget(ArabicLabel(text=h, bold=True, size_hint_y=None, height=25, color=(0, 0.7, 1, 1)))

        trades = get_all_closed_trades()
        for trade in trades:
            symbol, entry, exit_p, amount, pnl_usd, pnl_pct, reason, ts = trade
            color = (0.2, 0.8, 0.2, 1) if pnl_usd >= 0 else (0.9, 0.2, 0.2, 1)
            
            self.history_layout.add_widget(ArabicLabel(text=str(symbol), size_hint_y=None, height=25))
            self.history_layout.add_widget(ArabicLabel(text=f"${pnl_usd:.2f}", color=color, size_hint_y=None, height=25))
            self.history_layout.add_widget(ArabicLabel(text=f"{pnl_pct:.2f}%", color=color, size_hint_y=None, height=25))
            self.history_layout.add_widget(ArabicLabel(text=str(reason), size_hint_y=None, height=25))

if __name__ == '__main__':
    MEXCScalpApp().run()
