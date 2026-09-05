import hashlib
import hmac
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from urllib.parse import urlencode

import requests
from kivy.app import App
from kivy.clock import Clock
from kivy.lang import Builder
from kivy.properties import StringProperty
from kivy.uix.screenmanager import Screen

BASE_URL = "https://api.mexc.com/api/v3"
SYMBOL_RULES_CACHE = {}
TREND_CACHE = {}
CACHE_TTL = 300
STATE_LOCK = threading.Lock()


def get_db_path():
    """Use Android's writable app-data directory instead of the APK directory."""
    try:
        app = App.get_running_app()
        if app and app.user_data_dir:
            os.makedirs(app.user_data_dir, exist_ok=True)
            return os.path.join(app.user_data_dir, "bot_data.db")
    except Exception:
        pass

    # Desktop fallback.
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "bot_data.db")


def migrate_legacy_db():
    """Copy an old local DB once, if one exists beside the source file."""
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

<MainScreen>:
    name: "main"
    canvas.before:
        Color:
            rgba: .035,.04,.055,1
        Rectangle:
            pos: self.pos
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

            Label:
                text: "Live Log"
                font_size: "15sp"
                bold: True
                size_hint_y: None
                height: dp(30)

            TextInput:
                id: log
                text: app.log_text
                readonly: True
                multiline: True
                size_hint_y: None
                height: dp(260)
                background_color: .045,.055,.07,1
                foreground_color: .75,.8,.86,1
"""


def init_db():
    with sqlite3.connect(get_db_path()) as conn:
        c = conn.cursor()
        c.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
        )
        c.execute("""CREATE TABLE IF NOT EXISTS active_position (
            id INTEGER PRIMARY KEY, symbol TEXT, entry_price REAL, amount REAL,
            tp_percent REAL, sl_percent REAL)""")
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


def save_active_position(symbol, entry, amount, tp, sl):
    with sqlite3.connect(get_db_path()) as conn:
        conn.execute("DELETE FROM active_position")
        conn.execute(
            """INSERT INTO active_position
               (id,symbol,entry_price,amount,tp_percent,sl_percent)
               VALUES(1,?,?,?,?,?)""",
            (symbol, entry, amount, tp, sl),
        )
        conn.commit()


def get_active_position():
    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            """SELECT symbol,entry_price,amount,tp_percent,sl_percent
               FROM active_position WHERE id=1"""
        ).fetchone()
        if not row:
            return None
        return {
            "symbol": row[0],
            "entry_price": row[1],
            "amount": row[2],
            "tp_percent": row[3],
            "sl_percent": row[4],
        }


def clear_active_position():
    with sqlite3.connect(get_db_path()) as conn:
        conn.execute("DELETE FROM active_position")
        conn.commit()


def record_closed_trade(
    symbol, entry, exit_price, amount, pnl_usd, pnl_pct, reason
):
    with sqlite3.connect(get_db_path()) as conn:
        conn.execute(
            """INSERT INTO closed_trades
               (symbol,entry_price,exit_price,amount,pnl_usd,pnl_percent,reason,timestamp)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                symbol,
                entry,
                exit_price,
                amount,
                pnl_usd,
                pnl_pct,
                reason,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()


def safe_get(url, params=None, headers=None):
    backoff = 0.5
    for _ in range(3):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=8)
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
        for s in data["symbols"]:
            SYMBOL_RULES_CACHE[s["symbol"]] = s.get("baseAssetPrecision", 4)


def get_top_200_symbols():
    data = safe_get(BASE_URL + "/ticker/24hr")
    if isinstance(data, list):
        pairs = [x for x in data if x.get("symbol", "").endswith("USDT")]
        pairs.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
        return [x["symbol"] for x in pairs[:200]]
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
    params["recvWindow"] = 5000
    qs = urlencode(params)
    params["signature"] = hmac.new(
        secret.encode(), qs.encode(), hashlib.sha256
    ).hexdigest()
    headers = {"X-MEXC-APIKEY": api, "Content-Type": "application/json"}
    if method == "POST":
        return requests.post(
            BASE_URL + path, headers=headers, params=params, timeout=8
        )
    return requests.get(
        BASE_URL + path, headers=headers, params=params, timeout=8
    )


def buy_market(symbol, amount, api, secret):
    if not api or not secret:
        return False, "Please enter API Key and Secret Key", 0
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
            api,
            secret,
        )
        data = r.json()
        if r.status_code == 200 and "orderId" in data:
            time.sleep(0.3)
            p = get_price(symbol)
            return True, "Buy order executed: " + str(data["orderId"]), p or 0
        return False, data.get("msg", str(data)), 0
    except Exception as e:
        return False, str(e), 0


def free_balance(symbol, api, secret):
    try:
        asset = symbol.replace("USDT", "").replace("/", "").upper()
        r = signed_request("GET", "/account", {}, api, secret)
        if r.status_code == 200:
            for b in r.json().get("balances", []):
                if b["asset"] == asset:
                    return float(b["free"])
    except Exception:
        pass
    return 0


def sell_market(symbol, api, secret):
    try:
        qty = free_balance(symbol, api, secret)
        if qty <= 0:
            return True, "No balance available to sell"
        precision = SYMBOL_RULES_CACHE.get(
            symbol.replace("/", "").upper(), 4
        )
        r = signed_request(
            "POST",
            "/order",
            {
                "symbol": symbol.replace("/", "").upper(),
                "side": "SELL",
                "type": "MARKET",
                "quantity": f"{qty:.{precision}f}",
            },
            api,
            secret,
        )
        data = r.json()
        if r.status_code == 200 and "orderId" in data:
            return True, "Sell order executed"
        return False, data.get("msg", str(data))
    except Exception as e:
        return False, str(e)


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

        if not all(ema_trend(s, i) for i in ("5m", "15m", "60m")):
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
            sum(((h[i] + l[i] + c[i]) / 3) * v[i] for i in range(-21, -1))
            / total
            if total
            else c[-2]
        )
        above = c[-2] > vwap

        avg = sum(v[-101:-2]) / 99 if len(v) >= 101 else 1
        high = v[-2] > avg * 1.8

        if cross and aligned and above and high:
            return True, c[-1], "Confirmed entry strategy conditions met"

        return False, c[-1], "Conditions not met"
    except Exception as e:
        return False, 0, str(e)


class MainScreen(Screen):
    pass


class MEXCMobileApp(App):
    active_symbol = StringProperty("--")
    pnl_text = StringProperty("$0.00 (0.00%)")
    current_price_text = StringProperty("--")
    log_text = StringProperty("")

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
        if pos:
            self.position = pos
            self.active_symbol = pos["symbol"]
            self.log("Restored active position.")
            self.start_ticker()

        return root

    def load_settings(self):
        ids = self.root.get_screen("main").ids
        ids.api.text = get_setting("api_key", "")
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

    def start_scanning(self):
        api, secret, _, _, _ = self.values()
        if not api or not secret:
            self.log("⚠️ Please enter API keys first.")
            return

        with STATE_LOCK:
            if self.running or self.position:
                return
            self.running = True

        self.save_inputs()
        self.log("[START] Scanning for opportunities...")

        def scan():
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
                            lambda dt, s=sym, p=price: self.execute_buy(s, p),
                            0,
                        )
                        with STATE_LOCK:
                            self.running = False
                        return

                    time.sleep(0.15)

                time.sleep(2)

        self.scanner_thread = threading.Thread(target=scan, daemon=True)
        self.scanner_thread.start()

    def stop_scanning(self):
        with STATE_LOCK:
            self.running = False
        self.log("[STOP] Scanning stopped.")

    def execute_buy(self, symbol, approx):
        api, secret, amount, tp, sl = self.values()
        self.log(f"[BUY] {symbol} for ${amount}...")

        ok, msg, entry = buy_market(symbol, amount, api, secret)
        if not ok or entry <= 0:
            self.log("[BUY FAILED] " + msg)
            self.start_scanning()
            return

        with STATE_LOCK:
            self.position = {
                "symbol": symbol,
                "entry_price": entry,
                "amount": amount,
                "tp_percent": tp,
                "sl_percent": sl,
            }

        save_active_position(symbol, entry, amount, tp, sl)
        self.active_symbol = symbol
        self.log(f"[BOUGHT] {symbol} | Entry ${entry:.6f}")
        self.start_ticker()

    def start_ticker(self):
        if self.ticker_thread and self.ticker_thread.is_alive():
            return
        self.ticker_thread = threading.Thread(
            target=self.ticker_loop, daemon=True
        )
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
                    lambda dt, p=p, u=usd, x=pct: self.update_pnl(p, u, x),
                    0,
                )

                if pct >= pos["tp_percent"]:
                    Clock.schedule_once(
                        lambda dt: self.auto_close("TP"), 0
                    )
                    return

                if pct <= -pos["sl_percent"]:
                    Clock.schedule_once(
                        lambda dt: self.auto_close("SL"), 0
                    )
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

        ok, msg = sell_market(pos["symbol"], api, secret)
        exit_price = get_price(pos["symbol"]) or pos["entry_price"]
        pct = (
            (exit_price - pos["entry_price"])
            / pos["entry_price"]
            * 100
        )
        usd = pos["amount"] * pct / 100

        record_closed_trade(
            pos["symbol"],
            pos["entry_price"],
            exit_price,
            pos["amount"],
            usd,
            pct,
            reason,
        )
        clear_active_position()

        self.active_symbol = "--"
        self.current_price_text = "--"
        self.pnl_text = "$0.00 (0.00%)"

        self.log(
            ("[CLOSED] " if ok else "[WARNING] ") + msg
        )

        if reason != "MANUAL":
            self.start_scanning()

    def close_position_manual(self):
        self.close_position("MANUAL")

    def save_inputs(self):
        api, secret, amount, tp, sl = self.values()
        save_setting("api_key", api)
        save_setting("secret_key", secret)
        save_setting("amount", amount)
        save_setting("tp", tp)
        save_setting("sl", sl)


if __name__ == "__main__":
    MEXCMobileApp().run()
