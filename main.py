# -*- coding: utf-8 -*-
"""
MEXC Mobile Scalper — Full Fixed Version
- VWAP + EMA cross strategy on 5m (trend filter: 5m/15m/1h)
- Safe quantity-based selling (executedQty, LOT_SIZE rounding)
- Fees accounted in TP/SL
- Trade History screen with Win/Loss stats
- Clear record + Clear history buttons
"""

import hashlib
import hmac
import os
import shutil
import sqlite3
import threading
import time
 datetime import datetime
from urllib.parse import urlencode

import requests
from kivy.app import App
from kivy.clock import Clock
from kivy.lang import Builder
from kivy.properties import StringProperty, ColorProperty
from kivy.uix.screenmanager import Screen, ScreenManager

BASE_URL = "https://api.mexc.com/api/v3"
SYMBOL_RULES_CACHE = {}   # symbol -> {"precision","step","minQty"}
TREND_CACHE = {}
CACHE_TTL = 300
STATE_LOCK = threading.Lock()
SCAN_LOCK = threading.Lock()
FEE_RATE = 0.0007         # ~0.05% per side + safety margin


def get_db_path():
    try:
        app = App.get_running_app()
        if app and app.user_data_dir:
            os.makedirs(app.user_data_dir, exist_ok=True)
            return os.path.join(app.user_dir, "bot_data.db")
    except Exception:
        pass
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "bot_data.db")


def migrate_legacy_db():
    target = get_db_path()
    legacy = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.db")
    try:
        if os.path.abspath(legacy) != os.path.abspath(target):
            if os.path.exists(legacy) and not os.path.exists(target):
                shutil.copy2(legacy, target)
    except Exception:
        pass


KV = r"""
#:import dp kivy.metrics.dp

ScreenManager:
    MainScreen:
    HistoryScreen:

<MainScreen>:
    name: "main"
    canvas.before:
        Color:
            rgba: .035,.04,.055,1
        Rectangle:
            pos self.pos
            size: self.size

    ScrollView:
        do_scroll_x: False
        bar_width: dp(3)
        BoxLayout:
            orientation: "vertical"
            spacing: dp(12)
            padding: dp(12)
            size_hint_y: None
            height: self.minimum_height

            BoxLayout:
                size_hint_y: None
                height: dp(55)
                padding: dp(4)
                Label:
                    text: "MEXC"
                    font_size: "25sp"
                    bold: True
                    color: .15,.8,1,1
                    halign: "left"
                    text_size: self.size
                Label:
                    text: "MOBILE SCALPER"
                    font_size: "12sp"
                    color: .65,.7,.75,1
                    halign: "right"
                    valign: "center"
                    text_size: self.size

            BoxLayout:
                size_hint_y: None
                height: dp(88)
                padding: dp(12)
                spacing: dp(8)
                canvas.before:
                    Color:
                        rgba: .07,.085,.11,1
                    RoundedRectangle:
                        pos: self.pos
                        size: self.size
                        radius: [dp(14)]
                BoxLayout:
                    orientation: "vertical"
                    Label:
                        text: "Pair"
                        font_size: "11sp"
                        color: .55,.6,.68,1
                    Label:
                        text: app.active_symbol
                        font_size: "22sp"
                        bold: True
                BoxLayout:
                    orientation: "vertical"
                    Label:
                        text: "Current Price"
                        font_size: "11sp"
                        color: .55,.6,.68,1
                    Label:
                        text: app.current_price_text
                        font_size: "18sp"
                        bold: True

            BoxLayout:
                size_hint_y: None
                height: dp(105)
                padding: dp(12)
                orientation: "vertical"
                canvas.before:
                    Color:
                        rgba: .07,.085,.11,1
                    RoundedRectangle:
                        pos: self.pos
                        size: self.size
                        radius: [dp(14)]
                Label:
                    text: "Live PnL"
                    font_size: "12sp"
                    color: .55,.6,.68,1
                    size_hint_y: None
                    height: dp(25)
                Label:
                    text: app.pnl_text
                    font_size: "27sp"
                    bold: True

            Label:
                text: "Connection Settings"
                font_size: "15sp"
                bold: True
                size_hint_y: None
                height: dp(30)

            TextInput:
                id: api
                hint_text: "API Key"
                multiline: False
                size_hint_y: None
                height: dp(46)
                padding: [dp(12),dp(12)]
                background_color: .075,.09,.12,1
                foreground_color: 1,1,1,1

            TextInput:
                id: secret
                hint_text: "Secret Key"
                password: True
                multiline: False
                size_hint_y: None
                height: dp(46)
                padding: [dp(12),dp(12)]
                background_color: .075,.09,.12,1
                foreground_color: 1,1,1,1

            Label:
                text: "Risk Management"
                font_size: "15sp"
                bold: True
                size_hint_y: None
                height: dp(30)

            BoxLayout:
                size_hint_y: None
                height: dp(48)
                spacing: dp(7)
                TextInput:
                    id: amount
                    text: "109"
                    hint_text: "Amount $"
                    input_filter: "float"
                    multiline: False
                    background_color: .075,.09,.12,1
                    foreground_color: 1,1,1,1
                TextInput:
                    id: tp
                    text: "1.5"
                    hint_text: "TP %"
                    input_filter: "float"
                    multiline: False
                    background_color: .075,.09,.12,1
                    foreground_color: 1,1,1,1
                TextInput:
                    id: sl
                    text: "2.0"
                    hint_text: "SL %"
                    input_filter: "float"
                    multiline: False
                    background_color: .075,.09,.12,1
                    foreground_color: 1,1,1,1

            BoxLayout:
                size_hint_y: None
                height: dp(55)
                spacing: dp(8)
                Button:
                    text: "▶ Start Scan"
                    bold: True
                    background_color: .08,.48,.75,1
                    on_release: app.start_scanning()
                Button:
                    text: "■ Stop"
                    bold: True
                    background_color: .8,.42,.08,1
                    on_release: app.stop_scanning()

            Button:
                text: "Close Position Instantly"
                size_hint_y: None
                height: dp(55)
                bold: True
                background_color: .78,.12,.14,1
                on_release: app.close_position_manual()

            Button:
                text: "🗑 Clear Trade Record"
                size_hint_y: None
                height: dp(48)
                bold: True
                background_color: .25,.25,.3,1
                on_release: app.clear_trade_record()

            Button:
                text: "📊 Trade History & Stats"
                size_hint_y: None
                height: dp(48)
                bold: True
                background_color: .1,.35,.5,1
                on_release: app.open_history()

            Label:
                text: "Live Log"
                font_size: "15sp"
                bold: True
                size_hint_y: None
                height: dp(30)

            TextInput                id: log
                text: app.log_text
                readonly: True
                multiline: True
                size_hint_y: None
                height: dp(260)
                background_color: .045,.055,.07,1
                foreground_color: .75,.8,.86,1


<HistoryScreen>:
    name: "history"
    canvas.before:
        Color:
            rgba: .035,.04,.055,1
        Rectangle:
            pos: self.pos
            size: self.size

    BoxLayout:
        orientation: "vertical"
        padding: dp(12)
        spacing: dp(10)

        BoxLayout:
            size_hint_y: None
            height: dp(50)
            spacing: dp(8)
            Button:
                text: "◀ Back"
                size_hint_x: None
                width: dp(90)
                bold: True
                background_color: .25,.25,.3,1
                on_release: app.back_to_main()
            Label:
                text: "Trade History"
                font_size: "20sp"
                bold: True
                color: .15,.8,1,1

        BoxLayout:
            size_hint_y: None
            height: dp(110)
            padding: dp(10)
            spacing: dp(8)
            canvas.before:
                Color:
                    rgba: .07,.085,.11,1
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [dp(14)]
            BoxLayout:
                orientation: "vertical"
                Label:
                    text: "Total Trades"
                    font_size: "10sp"
                    color: .55,.6,.68,1
                Label:
                    text: app.stat_total
                    font_size: "18sp"
                    bold: True
            BoxLayout:
                orientation: "vertical"
                Label:
                    text: "Win Rate"
                    font_size: "10sp"
                    color: .55,.6,.68,1
                Label:
                    text: app.stat_winrate
                    font_size: "18sp"
                    bold: True
                    color: app.winrate_color
            BoxLayout:
                orientation: "vertical"
                Label:
                    text: "Net PnL"
                    font_size: "10sp"
                    color: .55,.6,.68,1
                Label:
                    text: app.stat_pnl
                    font_size: "18sp"
                    bold: True
                    color: app.stat_pnl_color

        BoxLayout:
            size_hint_y: None
            height: dp(40)
            spacing: dp(6)
            Label:
                text: "Wins: " + app.stat_wins
                bold: True
                color: .3,.85,.45,1
            Label:
                text: "Losses: " + app.stat_losses
                bold: True
                color: .9,.35,.35,1

        ScrollView:
            do_scroll_x: False
            bar_width: dp(3)
            Label:
                text: app.history_text
                font_size: "12sp"
                color: .75,.8,.86,1
                halign: "left"
                valign: "top"
                text_size: self.width, None
                size_hint_y: None
                height: self.texture_size[1]
                padding: [dp(6), dp(6)]

        Button:
            text: "🗑 Clear History"
            size_hint_y: None
            height: dp(44)
            bold: True
            background_color: .5,.15,.15,1
            on_release: app.clear_history_from_screen()
"""


# ---------------------------------------------------------------- DB

def init_db():
    with sqlite3.connect(get_db_path()) as conn:
        c = conn.cursor()
        c.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
        )
        c.execute("""CREATE TABLE IF NOT EXISTS active_position (
            id INTEGER PRIMARY KEY, symbol TEXT, entry_price REAL, amount REAL,
            qty REAL, tp_percent REAL, sl_percent REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS closed_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, entry_price REAL,
            exit_price REAL, amount REAL, pnl_usd REAL, pnl_percent REAL,
            reason TEXT, timestamp TEXT)""")
        conn.commit()


def save_setting(key, value):
    with sqlite3.connect(get_db_path()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
            (key, str(value)),
        )
        conn.commit()


def get_setting(key, default=""):
    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return row[0] if row else default


def save_active_position(symbol, entry, amount, qty, tp, sl):
    with sqlite3.connect(get_db_path()) as conn:
        conn.execute("DELETE FROM active_position")
        conn.execute(
            """INSERT INTO active_position
               (id,symbol,entry_price,amount,qty,tp_percent,sl_percent)
               VALUES(1,?,?,?,?,?,?)""",
            (symbol, entry, amount, qty, tp, sl),
        )
        conn.commit()


def get_active_position():
    with sqlite3.connect(get_db_path()) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(active_position)")]
        if "qty" not in cols:
            conn.execute("ALTER TABLE active_position ADD COLUMN qty REAL DEFAULT 0")
            conn.commit()
        row = conn.execute(
            """SELECT symbol,entry_price,amount,qty,tp_percent,sl_percent
               FROM active_position WHERE id=1"""
        ).fetchone()
        if not row:
            return None
        return {
            "symbol": row[0],
            "entry_price": row[1],
            "amount": row[2],
            "qty": row[3] or 0,
            "tp_percent": row[4],
            "sl_percent": row[5],
        }


def clear_active_position():
    with sqlite3.connect(get_db_path()) as conn:
        conn.execute("DELETE FROM active_position")
        conn.commit()


def clear_closed_trades():
    with sqlite3.connect(get_db_path()) as conn:
        conn.execute("DELETE FROM closed_trades")
        conn.commit()


def record_closed_trade(symbol, entry, exit_price, amount, pnl_usd, pnl_pct, reason):
    with sqlite3.connect(get_db_path()) as conn:
        conn.execute(
            """INSERT INTO closed_trades
               (symbol,entry_price,exit_price,amount,pnl_usd,pnl_percent,reason,timestamp)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                symbol, entry, exit_price, amount,
                pnl_usd, pnl_pct, reason,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()


# ---------------------------------------------------------------- API

def safe_get(url, params=None, headers=None):
    backoff = 0.5
    for _ in range(3):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=10)
            if r.status_code == 429:
                time.sleep(backoff)
                backoff *= 2
                continue
            if r.status_code == 200:
                return r.json()
        except Exception:
            time.sleep(backoff)
            backoff *= 2
    return None


def update_exchange_info():
    global SYMBOL_RULES_CACHE
    data = safe_get(BASE_URL + "/exchangeInfo")
    if data and "symbols" in data:
        rules = {}
        for s in data["symbols"]:
            step, min_qty = 0.0001, 0.0
            for f in s.get("filters", []):
                if f.get("filterType") == "LOT_SIZE":
                    step = float(f.get("stepSize", 0.0001))
                    min_qty = float(f.get("minQty", 0.0))
            rules[s["symbol"]] = {
                "precision": s.get("baseAssetPrecision", 4),
                "step": step,
                "minQty": min_qty,
            }
        SYMBOL_RULES_CACHE = rules


def _round_qty(qty, symbol):
    rule = SYMBOL_RULES_CACHE.get(symbol, {"step": 0.0001, "minQty": 0.0})
    step = rule["step"] or 0.0001
    qty = max(0.0, qty)
    stepped = int(qty / step) * step
    step_str = str(step)
    decimals = 0
    if "." in step_str:
        trimmed = step_str.split(".")[-1].rstrip("0")
        decimals = len(trimmed)
 stepped = round(stepped, decimals)
    if stepped < (rule["minQty"] or 0):
        return 0.0
    return stepped


def get_top_200_symbols():
    data = safe_get(BASE_URL + "/ticker/24hr")
    if isinstance(data, list):
        pairs = [x for x in data if x.get("symbol", "").endswith("USDT")]
        pairs.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
        syms = [x["symbol"] for x in pairs[:200]]
        if syms:
            return syms
    return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]


def get_price(symbol):
    data = safe_get(
        BASE_URL + "/ticker/price",
        {"symbol": symbol.replace("/", "").upper()},
    )
    try:
        return float(data["price"])
    except Exception:
        return None


def signed_request(method, path, params, api, secret):
    params = dict(params)
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 10000
    qs = urlencode(params)
    params["signature"] = hmac.new(
        secret.encode(), qs.encode(), hashlib.sha256
    ).hexdigest()
    headers = {"X-MEXC-APIKEY": api, "Content-Type": "application/json"}
    if method == "POST":
        return requests.post(BASE_URL + path, headers=headers, params=params, timeout=10)
    return requests.get(BASE_URL + path, headers=headers, params=params, timeout=10)


def buy_market(symbol, amount, api, secret):
    """Buy market order. Returns (ok, msg, real_entry_price, executed_qty)."""
    if not api or not secret:
        return False, "Please enter API Key and Secret Key", 0, 0
    try:
        r = signed_request(
            "POST",
            "/order",
            {
                "symbol": symbol.replace("/", "").upper(),
                "side": "BUY",
                "type": "MARKET",
                "quoteOrderQty": f"{amount:.2f}",
            },
            api, secret,
        )
        data = r.json()
        if r.status_code == 200 and "orderId" in data:
            entry, qty = 0.0, 0.0
            try:
                cq = float(data.get("cummulativeQuoteQty", 0))
                qty = float(data.get("executedQty", 0))
                if qty > 0:
                    entry = cq / qty
            except Exception:
                pass
            if entry <= 0:
                time.sleep(0.3)
                entry = get_price(symbol) or 0
            if qty <= 0 and entry > 0:
                qty = amount / entry
            return True, "Buy executed: " + str(data["orderId"]), entry, qty
        return False, data.get("msg", str(data)), 0, 0
    except Exception as e:
        return False, str(e), 0, 0


def sell_qty_market(symbol, qty, api, secret):
    """Sell ONLY the stored position quantity (LOT_SIZE rounded)."""
    try:
        symbol = symbol.replace("/", "").upper()
        qty = _round_qty(qty, symbol)
        if qty <= 0:
            return False, "Quantity too small after LOT_SIZE rounding"
        r = signed_request(
            "POST",
            "/order",
            {
                "symbol": symbol,
                "side": "SELL",
                "type": "MARKET",
                "quantity": f"{qty:g}",
            },
            api, secret,
        )
        data = r.json()
        if r.status_code == 200 and "orderId" in data:
            return True, "Sell executed"
        return False, data.get("msg", str(data))
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------- Indicators

def ema(data, period):
    if len(data) < period:
        return []
    out = [sum(data[:period]) / period]
    m = 2 / (period + 1)
    for p in data[period:]:
        out.append((p - out[-1]) * m + out[-1])
    return out


def ema_trend(symbol, interval):
    key = (symbol, interval)
    now = time.time()
    if key in TREND_CACHE and now - TREND_CACHE[key][1] < CACHE_TTL:
        return TREND_CACHE[key][0]

    data = safe_get(
        BASE_URL + "/klines",
        {"symbol": symbol, "interval": interval, "limit": 220},
    )
    if not isinstance(data, list) or len(data) < 200:
        return False

    closes = [float(k[4]) for k in data]
    e = ema(closes, 200)
    result = bool(e) and closes[-2] > e[-2]
    TREND_CACHE[key] = (result, now)
    return result


def check_conditions(symbol):
    try:
        s = symbol.replace("/", "").upper()

        # Fixed: "1h" instead of invalid "60m"
        if not all(ema_trend(s, i) for i in ("5m", "15m", "1h")):
            return False, 0, "Overall trend is not bullish"

        data = safe_get(
            BASE_URL + "/klines",
            {"symbol": s, "interval": "5m", "limit": 220},
        )
        if not isinstance(data, list) or len(data) < 200:
            return False, 0, "Insufficient candlestick data"

        c = [float(k[4]) for k in data]
        v = [float(k[5]) for k in data]
        h = [float(k[2]) for k in data]
        l = [float(k[3]) for k in data]

        e9, e21, e200 = ema(c, 9), ema(c, 21), ema(c, 200)
        if not e9 or not e21 or not e200 or len(e9) < 3:
            return False, 0, "Indicator calculation error"

        cross = e9[-3] <= e21[-3] and e9[-2] > e21[-2]
        aligned = e9[-2] > e21[-2] > e200[-2]

        total = sum(v[-21:-1])
        vwap = (
            sum(((h[i] + l[i] + c[i]) / 3) * v[i] for i in range(-21, -1)) / total
            if total else c[-2]
        )
        above = c[-2] > vwap

        avg = sum(v[-101:-2]) / 99 if len(v) >= 101 else 1
        high = v[-2] > avg * 1.8

        if cross and aligned and above and high:
            return True, c[-1], "Confirmed entry strategy conditions met"

        return False, c[-1], "Conditions not met"
    except Exception as e:
        return False, 0, str(e)


# ---------------------------------------------------------------- Screens

class MainScreen(Screen):
    pass


class HistoryScreen(Screen):
    pass


# ---------------------------------------------------------------- App

class MEXCMobileApp(App):
    active_symbol = StringProperty("--")
    pnl_text = StringProperty("$0.00 (0.00%)")
    current_price_text = StringProperty("--")
    log_text = StringProperty("")

    history_text = StringProperty("No closed trades yet.")
    stat_total = StringProperty("0")
    stat_wins = StringProperty("0")
    stat_losses = StringProperty("0")
    stat_winrate = StringProperty("0%")
    stat_pnl = StringProperty("$0.00")
    winrate_color = ColorProperty([.75, .75, .75, 1])
    stat_pnl_color = ColorProperty([.75, .75, .75, 1])

    running = False
    position = None
    scanner_thread = None
    ticker_thread = None

    def build(self):
        migrate_legacy_db()
        init_db()
        root = Builder.load_string(KV)
        Clock.schedule_once(lambda dt: self.load_settings(), 0.2)

        pos = get_active_position()
        if pos and pos["qty"] > 0:
            self.position = pos
            self.active_symbol = pos["symbol"]
            self.log("Restored active position.")
            self.start_ticker()
        elif pos:
            clear_active_position()

        return root

    def load_settings(self):
        ids = self.root.get_screen("main").ids
        ids.api.text = get_setting("api_key "")
        ids.secret.text = get_setting("secret_key", "")
        ids.amount.text = get_setting("amount", "109")
        ids.tp.text = get_setting("tp", "1.5")
        ids.sl.text = get_setting("sl", "2.0")

    def log(self, msg):
        def add(dt):
            self.log_text += ("\n" if self.log_text else "") + msg
        Clock.schedule_once(add, 0)

    def values(self):
        ids = self.root.get_screen("main").ids
        api = ids.api.text.strip()
        secret = ids.secret.text.strip()
        try:
            amount = float(ids.amount.text)
            tp = float(ids.tp.text)
            sl = float(ids.sl.text)
        except Exception:
            amount, tp, sl = 109, 1.5, 2
        return api, secret, amount, tp, sl

    # ------------------------------------------------ Scanner

    def start_scanning(self):
        api, secret, _, _, _ = self.values()
        if not api or not secret:
            self.log("⚠️ Please enter API keys first.")
            return

        with STATE_LOCK:
            if self.running or self.position:
                return
            self.running = True

        if not SCAN_LOCK.acquire(blocking=False):
            with STATE_LOCK:
                self.running = False
            self.log("Scanner already running.")
            return

        self.save_inputs()
        self.log("[START] Scanning for opportunities...")

        def scan():
            try:
                update_exchange_info()
                symbols = get_top_200_symbols()
                self.log(f"Scanning {len(symbols)} coins...")

                while True:
                    with STATE_LOCK:
                        if not self.running:
                            break

                    for sym in symbols:
                        with STATE_LOCK:
                            if not self.running:
                                break

                        ok, price, msg = check_conditions(sym)

                        if ok:
                            self.log(f"🎯 {sym} | ${price:.6f}")
                            Clock.schedule_once(
                                lambda dt, s=sym, p=price: self.execute_buy(s, p), 0
                            )
                            with STATE_LOCK:
                                self.running = False
                            return

                        time.sleep(0.15)

                    time.sleep(2)
            finally:
                SCAN_LOCK.release()

        self.scanner_thread = threading.Thread(target=scan, daemon=True)
        self.scanner_thread.start()

    def stop_scanning(self):
        with STATE_LOCK:
            self.running = False
        self.log("[STOP] Scanning stopped.")

    # ------------------------------------------------ Trade

    def execute_buy(self, symbol, approx):
        api, secret, amount, tp, sl = self.values()
        self.log(f"[BUY] {symbol} for ${amount}...")

        ok, msg, entry, qty = buy_market(symbol, amount, api, secret)
        if not ok or entry <= 0 or qty <= 0:
            self.log("[BUY FAILED] " + msg)
            Clock.schedule_once(lambda dt: self.start_scanning(), 1)
            return

        with STATE_LOCK:
            self.position = {
                "symbol": symbol,
                "entry_price": entry,
                "amount": amount,
                "qty": qty,
                "tp_percent": tp,
                "sl_percent": sl,
            }

        save_active_position(symbol, entry, amount, qty, tp, sl)
        self.active_symbol = symbol
        self.log(f"[BOUGHT] {symbol} | Entry ${entry:.6f} | Qty {qty}")
        self.start_ticker()

    def start_ticker(self):
        if self.ticker_thread and self.ticker_thread.is_alive():
            return
        self.ticker_thread = threading.Thread(target=self.ticker_loop, daemon=True)
        self.ticker_thread.start()

    def ticker_loop(self):
        while True:
            with STATE_LOCK:
                pos = self.position

            if not pos:
                break

            p = get_price(pos["symbol"])
            if p and pos["entry_price"] > 0:
                pct = (p - pos["entry_price"]) / pos["entry_price"] * 100
                usd = pos["amount"] * pct / 100

                Clock.schedule_once(
                    lambda dt, p=p, u=usd, x=pct: self.update_pnl(p, u, x), 0
                )

                # Fees deducted from targets
                if pct >= pos["tp_percent"] - FEE_RATE * 100:
                    Clock.schedule_once(lambda dt: self.auto_close("TP"), 0)
                    return

                if pct <= -(pos["sl_percent"] + FEE_RATE * 100):
                    Clock.schedule_once(lambda dt: self.auto_close("SL"), 0)
                    return

            time.sleep(1)

    def update_pnl(self, p, usd, pct):
        self.current_price_text = f"${p:.6f}"
        self.pnl_text = (
            f"{'+' if usd >= 0 else ''}${usd:.2f} "
            f"({'+' if pct >= 0 else ''}{pct:.2f}%)"
        )

    def auto_close(self, reason):
        self.close_position(reason)

    def close_position(self, reason="MANUAL"):
        with STATE_LOCK:
            pos = self.position
            if not pos:
                return
            self.position = None

        api, secret, _, _, _ = self.values()
        self.log(f"[CLOSING] {pos['symbol']} ({reason})...")

        ok, msg = sell_qty_market(pos["symbol"], pos.get("qty", 0), api, secret)

        if not ok:
            self.log("[SELL FAILED] " + msg)
            self.log("Position kept active. Try again.")
            with STATE_LOCK:
                self.position = pos
            return

        exit_price = get_price(pos["symbol"]) or pos["entry_price"]
        pct = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100
        usd = pos["amount"] * pct / 100

        record_closed_trade(
            pos["symbol"], pos["entry_price"], exit_price,
            pos["amount"], usd, pct, reason,
        )
        clear_active_position()
        self.refresh_history()

        self.active_symbol = "--"
        self.current_price_text = "--"
        self.pnl_text = "$0.00 (0.00%)"

        self.log("[CLOSED] " + msg)

        if reason != "MANUAL":
            Clock.schedule_once(lambda dt: self.start_scanning(), 2)

    def close_position_manual(self):
        self.close_position("MANUAL")

    # ------------------------------------------------ Clear record 🗑

    def clear_trade_record(self):
        """Clears the ACTIVE position record only (does NOT sell assets!)."""
        had_pos = False
        with STATE_LOCK:
            if self.position:
                had_pos = True
                self.position = None

        clear_active_position()
        self.active_symbol = "--"
        self.current_price_text = "--"
        self.pnl_text = "$0.00 (0.00%)"

        if had_pos:
            self.log("🗑 Active position record cleared (assets NOT sold!).")
        else:
            self.log("🗑 No active position to clear.")

    def clear_history_from_screen(self):
        clear_closed_trades()
        self.refresh_history()
        self.log("🗑 Closed trade history cleared.")

    # ------------------------------------------------ History 📊

    def refresh_history(self):
        with sqlite3.connect(get_db_path()) as conn:
            rows = conn.execute(
                """SELECT symbol, entry_price, exit_price, amount,
                          pnl_usd, pnl_percent, reason, timestamp
                   FROM closed_trades ORDER BY id DESC"""
            ).fetchall()

        if not rows:
            self.history_text = "No closed trades yet."
            self.stat_total = "0"
            self.stat_wins = "0"
            self.stat_losses = "0"
            self.stat_winrate = "0%"
            self.stat_pnl = "$0.00"
            self.winrate_color = [.75, .75, .75, 1]
            self.stat_pnl_color = [.75, .75, .75, 1]
            return

        lines = []
        wins = losses = 0
        total_pnl = 0.0

        for (sym, entry, exit_, amount, usd, pct, reason, ts) in rows:
            total_pnl += usd
            if usd >= 0:
                wins += 1
                icon = "🟢"
            else:
                losses += 1
                icon = "🔴"
            lines.append(
                f"{icon} {sym} [{reason}]\n"
                f"   {ts} | Entry {entry:.6f} -> Exit {exit_:.6f}\n"
                f"   PnL: {'+' if usd >= 0 else ''}${usd:.f} "
                f"({'+' if pct >= 0 else ''}{pct:.2f}%) | ${amount:.0f}\n"
            )

        total = wins + losses
        winrate = wins / total * 100 if total else 0

        self.history_text = "\n".join(lines)
        self.stat_total = str(total)
        self.stat_wins = str(wins)
        self.stat_losses = str(losses)
        self.stat_winrate = f"{winrate:.1f}%"
        self.stat_pnl = f"{'+' if total_pnl >= 0 else ''}${total_pnl:.2f}"

        self.winrate_color = (
            [.3, .85, .45, 1] if winrate >= 50 else [.9, .35, .35, 1]
        )
        self.stat_pnl_color = (
            [.3, .85, .45, 1] if total_pnl >= 0 else [.9, .35, .35, 1]
        )

    def open_history(self):
        self.refresh_history()
        self.root.current = "history"

    def back_to_main(self):
        self.root.current = "main"

    def save_inputs(self):
        api, secret, amount, tp, sl = self.values()
        save_setting("api_key", api)
        save_setting("secret_key", secret)
        save_setting("amount", amount)
        save_setting("tp", tp)
        save_setting("sl", sl)


if __name__ == "__main__":
    MEXCMobileApp().run()
