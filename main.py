import sys
import os
import time
import math
import sqlite3
import requests
import numpy as np
import pandas as pd
import ta

from kivymd.app import MDApp
from kivymd.uix.screen import MDScreen
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDRaisedButton, MDRectangleFlatButton
from kivymd.uix.label import MDLabel
from kivymd.uix.card import MDCard
from kivymd.uix.scrollview import MDScrollView
from kivy.clock import Clock
from kivy.core.window import Window

# ==========================================
# 1. إدارة قاعدة البيانات SQLite (Recovery System)
# ==========================================
DB_FILE = "mexc_scalper.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS active_position (
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            buy_price REAL,
            quantity REAL,
            target_price REAL,
            stop_loss_price REAL,
            timestamp TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trade_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            buy_price REAL,
            sell_price REAL,
            quantity REAL,
            pnl_percent REAL,
            reason TEXT,
            close_time TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_active_position(symbol, buy_price, quantity, target_price, stop_loss_price):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM active_position")
    cursor.execute('''
        INSERT INTO active_position (id, symbol, buy_price, quantity, target_price, stop_loss_price, timestamp)
        VALUES (1, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
    ''', (symbol, buy_price, quantity, target_price, stop_loss_price))
    conn.commit()
    conn.close()

def clear_active_position():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM active_position")
    conn.commit()
    conn.close()

def get_active_position():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, buy_price, quantity, target_price, stop_loss_price FROM active_position WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    return row

def log_trade_history(symbol, buy_price, sell_price, quantity, pnl_percent, reason):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO trade_history (symbol, buy_price, sell_price, quantity, pnl_percent, reason, close_time)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
    ''', (symbol, buy_price, sell_price, quantity, pnl_percent, reason))
    conn.commit()
    conn.close()

# ==========================================
# 2. وظائف التعامل مع MEXC API
# ==========================================
MEXC_API_KEY = "YOUR_MEXC_API_KEY"
MEXC_SECRET_KEY = "YOUR_MEXC_SECRET_KEY"

def get_mexc_real_price(symbol):
    try:
        formatted_symbol = symbol.replace("/", "").upper()
        url = f"https://api.mexc.com/api/v3/ticker/price?symbol={formatted_symbol}"
        response = requests.get(url, timeout=4)
        if response.status_code == 200:
            return float(response.json()['price'])
        elif response.status_code == 429:
            time.sleep(1)
    except Exception as e:
        print(f"خطأ في جلب السعر: {e}")
    return None

def get_symbol_precision(symbol):
    try:
        formatted_symbol = symbol.replace("/", "").upper()
        url = "https://api.mexc.com/api/v3/exchangeInfo"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            for s in data.get('symbols', []):
                if s['symbol'] == formatted_symbol:
                    return int(s.get('baseAssetPrecision', 4))
    except Exception:
        pass
    return 4

def get_klines_data(symbol, interval, limit=250):
    try:
        formatted_symbol = symbol.replace("/", "").upper()
        url = f"https://api.mexc.com/api/v3/klines?symbol={formatted_symbol}&interval={interval}&limit={limit}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            df = pd.DataFrame(data, columns=[
                'open_time', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_asset_volume', 'trades', 'tb_base_vol', 'tb_quote_vol', 'ignore'
            ])
            df['close'] = df['close'].astype(float)
            df['open'] = df['open'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['volume'] = df['volume'].astype(float)
            return df
    except Exception as e:
        print(f"خطأ في جلب الشموع لـ {symbol}: {e}")
    return None

def place_mexc_buy_order_market(symbol, usdt_amount):
    price = get_mexc_real_price(symbol)
    if price:
        qty = usdt_amount / price
        return True, price, qty
    return False, 0, 0

def place_mexc_sell_order_market(symbol, qty):
    precision = get_symbol_precision(symbol)
    factor = 10 ** precision
    safe_qty = math.floor(qty * factor) / factor
    
    price = get_mexc_real_price(symbol)
    if price:
        return True, price, safe_qty
    return False, 0, 0

# ==========================================
# 3. تحليل الاستراتيجية (نفس المنطق الخوارزمي)
# ==========================================
def check_trade_conditions_from_main(symbol):
    try:
        df_5m = get_klines_data(symbol, "5m", limit=250)
        df_15m = get_klines_data(symbol, "15m", limit=250)
        df_60m = get_klines_data(symbol, "60m", limit=250)

        if df_5m is None or df_15m is None or df_60m is None:
            return False, 0

        ema200_15m = ta.trend.ema_indicator(df_15m['close'], window=200).iloc[-1]
        ema200_60m = ta.trend.ema_indicator(df_60m['close'], window=200).iloc[-1]

        last_price_15m = df_15m['close'].iloc[-1]
        last_price_60m = df_60m['close'].iloc[-1]

        if not (last_price_15m > ema200_15m and last_price_60m > ema200_60m):
            return False, 0

        df_5m['ema9'] = ta.trend.ema_indicator(df_5m['close'], window=9)
        df_5m['ema21'] = ta.trend.ema_indicator(df_5m['close'], window=21)
        df_5m['ema200'] = ta.trend.ema_indicator(df_5m['close'], window=200)
        df_5m['vwap'] = ta.volume.volume_weighted_average_price(
            high=df_5m['high'], low=df_5m['low'], close=df_5m['close'], volume=df_5m['volume']
        )
        psar = ta.trend.PSARIndicator(high=df_5m['high'], low=df_5m['low'], close=df_5m['close'])
        df_5m['psar'] = psar.psar()

        current_idx = len(df_5m) - 1
        prev_idx = current_idx - 1

        ema9_vals = df_5m['ema9'].values
        ema21_vals = df_5m['ema21'].values
        ema200_vals = df_5m['ema200'].values
        close_vals = df_5m['close'].values
        vwap_vals = df_5m['vwap'].values
        psar_vals = df_5m['psar'].values

        cross_up = (ema9_vals[prev_idx] <= ema21_vals[prev_idx]) and (ema9_vals[current_idx] > ema21_vals[current_idx])
        if not cross_up:
            return False, 0

        price_curr = close_vals[current_idx]
        if price_curr > vwap_vals[current_idx] and psar_vals[current_idx] < price_curr and  (ema21_vals[current_idx] > ema200_vals[current_idx]):
            return True, price_curr

    except Exception as e:
        print(f"خطأ في تحليل الاستراتيجية لـ {symbol}: {e}")

    return False, 0

# ==========================================
# 4. واجهة المستخدم والتطبيق للجوال (KivyMD)
# ==========================================
class MexcScalperMobileApp(MDApp):
    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Blue"

        init_db()

        self.symbols = [
            "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT",
            "SHIB/USDT", "DOT/USDT", "LINK/USDT", "SUI/USDT", "NEAR/USDT", "PEPE/USDT", "LTC/USDT", "APT/USDT",
            "UNI/USDT", "ICP/USDT", "FET/USDT", "RENDER/USDT", "ETC/USDT", "XLM/USDT", "STX/USDT", "INJ/USDT",
            "TAO/USDT", "FIL/USDT", "TRX/USDT", "OKB/USDT", "OP/USDT", "ARB/USDT", "WIF/USDT", "FLOKI/USDT",
            "TIA/USDT", "AAVE/USDT", "SEI/USDT", "GALA/USDT", "RUNE/USDT", "ENS/USDT", "BONK/USDT", "ATOM/USDT"
        ]

        self.is_scanning = False
        self.current_scan_index = 0
        self.is_monitoring = False

        # عناصر الواجهة
        screen = MDScreen()
        layout = MDBoxLayout(orientation='vertical', padding=15, spacing=15)

        self.status_label = MDLabel(
            text="الحالة: جاهز للعمل",
            halign="center",
            font_style="Subtitle1",
            size_hint_y=None,
            height="40dp"
        )
        layout.add_widget(self.status_label)

        # زر التحكم
        btn_layout = MDBoxLayout(orientation='horizontal', spacing=10, size_hint_y=None, height="50dp")
        self.start_btn = MDRaisedButton(text="بدء البحث", on_release=self.start_scanning)
        self.stop_btn = MDRectangleFlatButton(text="إيقاف البحث", on_release=self.stop_scanning)
        btn_layout.add_widget(self.start_btn)
        btn_layout.add_widget(self.stop_btn)
        layout.add_widget(btn_layout)

        # بطاقة المعلومات
        card = MDCard(padding=15, elevation=2)
        scroll = MDScrollView()
        self.info_label = MDLabel(
            text="لا توجد صفقة مفتوحة حالياً.",
            halign="left",
            valign="top",
            theme_text_color="Secondary"
        )
        scroll.add_widget(self.info_label)
        card.add_widget(scroll)
        layout.add_widget(card)

        screen.add_widget(layout)

        # التحقق من استعادة صفقات سابقة عند التشغيل
        Clock.schedule_once(lambda dt: self.check_and_recover_position(), 1)

        return screen

    def start_scanning(self, instance):
        if self.is_scanning or self.is_monitoring:
            return
        self.is_scanning = True
        self.current_scan_index = 0
        self.status_label.text = "الحالة: بدء عملية الفحص..."
        Clock.schedule_interval(self.scan_step, 0.2)

    def stop_scanning(self, instance):
        if self.is_scanning:
            self.is_scanning = False
            Clock.unschedule(self.scan_step)
            self.status_label.text = "الحالة: تم إيقاف الفحص."

    def scan_step(self, dt):
        if not self.is_scanning:
            return False

        if self.current_scan_index >= len(self.symbols):
            self.status_label.text = "الحالة: إنهاء دورة الفحص كاملة بدون إشارة."
            self.is_scanning = False
            return False

        symbol = self.symbols[self.current_scan_index]
        self.status_label.text = f"فحص [{self.current_scan_index + 1}/{len(self.symbols)}]: {symbol}"
        
        is_match, price = check_trade_conditions_from_main(symbol)
        if is_match:
            self.is_scanning = False
            Clock.unschedule(self.scan_step)
            self.execute_trade(symbol, price)
            return False

        self.current_scan_index += 1

    def execute_trade(self, symbol, entry_price):
        usdt_size = 10.0
        success, buy_price, qty = place_mexc_buy_order_market(symbol, usdt_size)
        if success:
            target_price = buy_price * 1.015
            stop_loss = buy_price * 0.99
            save_active_position(symbol, buy_price, qty, target_price, stop_loss)
            self.start_monitoring(symbol, buy_price, qty, target_price, stop_loss)

    def start_monitoring(self, symbol, buy_price, qty, target_price, stop_loss):
        self.is_monitoring = True
        self.status_label.text = f"تم الشراء في {symbol}! جاري المتابعة..."
        Clock.schedule_interval(self.monitor_step, 2.0)

    def monitor_step(self, dt):
        pos = get_active_position()
        if not pos:
            self.is_monitoring = False
            return False

        symbol, buy_price, qty, target_price, stop_loss = pos
        curr_price = get_mexc_real_price(symbol)

        if curr_price:
            pnl = ((curr_price - buy_price) / buy_price) * 100
            info = (
                f"--- صفقة نشطة ---\n"
                f"العملة: {symbol}\n"
                f"سعر الدخول: {buy_price}\n"
                f"السعر الحالي: {curr_price}\n"
                f"الهدف (TP): {target_price}\n"
                f"الاستوب (SL): {stop_loss}\n"
                f"الربح/الخسارة الحالية: {pnl:.2f}%"
            )
            self.info_label.text = info

            if curr_price >= target_price:
                success, sell_price, _ = place_mexc_sell_order_market(symbol, qty)
                if success:
                    self.close_position(symbol, buy_price, sell_price, qty, pnl, "أخذ الأرباح (TP)")
                    return False
            elif curr_price <= stop_loss:
                success, sell_price, _ = place_mexc_sell_order_market(symbol, qty)
                if success:
                    self.close_position(symbol, buy_price, sell_price, qty, pnl, "وقف الخسارة (SL)")
                    return False

    def close_position(self, symbol, buy_price, sell_price, qty, pnl, reason):
        log_trade_history(symbol, buy_price, sell_price, qty, pnl, reason)
        clear_active_position()
        self.is_monitoring = False
        Clock.unschedule(self.monitor_step)
        self.info_label.text = f"تم إغلاق الصفقة على {symbol}.\nالسبب: {reason}\nالربح النهائي: {pnl:.2f}%"
        self.status_label.text = "جاهز للبحث عن صفقة جديدة..."

    def check_and_recover_position(self):
        pos = get_active_position()
        if pos:
            symbol, buy_price, qty, target_price, stop_loss = pos
            self.start_monitoring(symbol, buy_price, qty, target_price, stop_loss)

if __name__ == '__main__':
    MexcScalperMobileApp().run()
