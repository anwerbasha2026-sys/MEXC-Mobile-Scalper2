
import os
import time
import sqlite3
import hmac
import hashlib
import threading
from datetime import datetime
from urllib.parse import urlencode

import requests
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.properties import StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(APP_DIR, "bot_data.db")
API_BASE = "https://api.mexc.com/api/v3"


def db_connect():
    return sqlite3.connect(DB_NAME)


def init_db():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS active_position (
        id INTEGER PRIMARY KEY, symbol TEXT, entry_price REAL,
        amount REAL, tp_percent REAL, sl_percent REAL
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS closed_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT,
        entry_price REAL, exit_price REAL, amount REAL,
        pnl_usd REAL, pnl_percent REAL, reason TEXT, timestamp TEXT
    )""")
    conn.commit()
    conn.close()


def save_setting(key, value):
    conn = db_connect()
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                 (key, str(value)))
    conn.commit()
    conn.close()


def get_setting(key, default=""):
    conn = db_connect()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default


def save_active_position(symbol, entry_price, amount, tp, sl):
    conn = db_connect()
    conn.execute("DELETE FROM active_position")
    conn.execute("""INSERT INTO active_position
        (id,symbol,entry_price,amount,tp_percent,sl_percent)
        VALUES(1,?,?,?,?,?)""", (symbol, entry_price, amount, tp, sl))
    conn.commit()
    conn.close()


def clear_active_position():
    conn = db_connect()
    conn.execute("DELETE FROM active_position")
    conn.commit()
    conn.close()


def get_active_position():
    conn = db_connect()
    row = conn.execute("""SELECT symbol,entry_price,amount,tp_percent,sl_percent
                          FROM active_position WHERE id=1""").fetchone()
    conn.close()
    if not row:
        return None
    return {
        "symbol": row[0], "entry_price": row[1], "amount": row[2],
        "tp_percent": row[3], "sl_percent": row[4]
    }


def record_closed_trade(symbol, entry, exit_price, amount, pnl_usd, pnl_pct, reason):
    conn = db_connect()
    conn.execute("""INSERT INTO closed_trades
        (symbol,entry_price,exit_price,amount,pnl_usd,pnl_percent,reason,timestamp)
        VALUES(?,?,?,?,?,?,?,?)""",
        (symbol, entry, exit_price, amount, pnl_usd, pnl_pct, reason,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()


def get_all_closed_trades():
    conn = db_connect()
    rows = conn.execute("""SELECT symbol,entry_price,exit_price,amount,
        pnl_usd,pnl_percent,reason,timestamp FROM closed_trades ORDER BY id DESC""").fetchall()
    conn.close()
    return rows


def api_get(path, params=None, headers=None, timeout=8):
    try:
        return requests.get(API_BASE + path, params=params, headers=headers, timeout=timeout)
    except Exception:
        return None


def signed_request(method, path, params, api_key, secret_key):
    params = dict(params)
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 60000
    query = urlencode(params)
    signature = hmac.new(secret_key.encode(), query.encode(), hashlib.sha256).hexdigest()
    params["signature"] = signature
    headers = {"X-MEXC-APIKEY": api_key}
    url = API_BASE + path
    if method == "GET":
        return requests.get(url, headers=headers, params=params, timeout=8)
    return requests.post(url, headers=headers, params=params, timeout=8)


def get_top_200_symbols():
    try:
        r = api_get("/ticker/24hr", timeout=10)
        if r and r.status_code == 200:
            data = r.json()
            pairs = [x for x in data if x.get("symbol", "").endswith("USDT")]
            pairs.sort(key=lambda x: float(x.get("quoteVolume", 0) or 0), reverse=True)
            return [x["symbol"] for x in pairs[:200]]
    except Exception:
        pass
    return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]


def get_real_price(symbol):
    try:
        r = api_get("/ticker/price", {"symbol": symbol.replace("/", "").upper()}, timeout=5)
        if r and r.status_code == 200:
            return float(r.json()["price"])
    except Exception:
        pass
    return None


def get_free_balance(symbol, api_key, secret_key):
    try:
        r = signed_request("GET", "/account", {}, api_key, secret_key)
        if r and r.status_code == 200:
            asset = symbol.replace("USDT", "").replace("/", "").upper()
            for b in r.json().get("balances", []):
                if b["asset"] == asset:
                    return float(b["free"])
    except Exception:
        pass
    return 0.0


def buy_market(symbol, amount_usd, api_key, secret_key):
    if not api_key or not secret_key:
        return False, "API credentials are missing.", 0.0
    try:
        params = {
            "symbol": symbol.replace("/", "").upper(),
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": f"{float(amount_usd):.2f}"
        }
        r = signed_request("POST", "/order", params, api_key, secret_key)
        data = r.json() if r is not None else {}
        if r is not None and r.status_code == 200 and "orderId" in data:
            time.sleep(0.5)
            price = get_real_price(symbol)
            return True, f"Order ID: {data['orderId']}", price or 0.0
        return False, data.get("msg", str(data)), 0.0
    except Exception as e:
        return False, str(e), 0.0


def sell_market(symbol, api_key, secret_key):
    try:
        qty = get_free_balance(symbol, api_key, secret_key)
        if qty <= 0:
            return True, "Position already closed or balance is zero."
        params = {
            "symbol": symbol.replace("/", "").upper(),
            "side": "SELL",
            "type": "MARKET",
            "quantity": f"{qty:.4f}"
        }
        r = signed_request("POST", "/order", params, api_key, secret_key)
        data = r.json() if r is not None else {}
        if r is not None and r.status_code == 200 and "orderId" in data:
            return True, "Market sell order executed."
        return False, f"Exchange rejected order: {data.get('msg', str(data))}"
    except Exception as e:
        return False, str(e)


def ema_series(data, period):
    if len(data) < period:
        return []
    multiplier = 2 / (period + 1)
    ema = [sum(data[:period]) / period]
    for price in data[period:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema


def check_ema200(symbol, interval):
    try:
        r = api_get("/klines", {"symbol": symbol, "interval": interval, "limit": 500}, timeout=6)
        if not r or r.status_code != 200:
            return False
        closes = [float(k[4]) for k in r.json()]
        if len(closes) < 200:
            return False
        e = ema_series(closes, 200)
        return bool(e) and closes[-1] > e[-1]
    except Exception:
        return False


def check_trade_conditions(symbol):
    try:
        symbol = symbol.replace("/", "").upper()

        if not (check_ema200(symbol, "5m") and
                check_ema200(symbol, "15m") and
                check_ema200(symbol, "60m")):
            return False, 0.0, "Trend is not bullish"

        r = api_get("/klines", {
            "symbol": symbol, "interval": "5m", "limit": 500
        }, timeout=7)
        if not r or r.status_code != 200:
            return False, 0.0, "Unable to fetch market data"

        klines = r.json()
        closes = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]

        if len(closes) < 200 or len(volumes) < 100:
            return False, 0.0, "Not enough candles"

        price = closes[-1]
        e9 = ema_series(closes, 9)
        e21 = ema_series(closes, 21)
        e200 = ema_series(closes, 200)
        if not e9 or not e21 or not e200:
            return False, price, "EMA data unavailable"

        min_len = min(len(e9), len(e21))
        e9v, e21v = e9[-min_len:], e21[-min_len:]
        recent_cross = False
        for offset in range(1, 4):
            idx = -offset
            prev = idx - 1
            if e9v[prev] <= e21v[prev] and e9v[idx] > e21v[idx]:
                recent_cross = True
                break

        total_vol = sum(volumes[-20:])
        vwap_num = sum(((highs[i] + lows[i] + closes[i]) / 3) * volumes[i]
                        for i in range(-20, 0))
        vwap = vwap_num / total_vol if total_vol else price
        avg_vol = sum(volumes[-100:-1]) / 99
        high_volume = volumes[-1] > avg_vol * 1.8

        valid = (recent_cross and e9v[-1] > e21v[-1] and
                 e21v[-1] > e200[-1] and price > vwap and high_volume)

        return (True, price, "Sniping conditions confirmed") if valid else \
               (False, price, "Conditions not complete")
    except Exception as e:
        return False, 0.0, f"Error: {e}"


class MEXCApp(App):
    status = StringProperty("Ready")

    def build(self):
        Window.clearcolor = (0.07, 0.07, 0.07, 1)
        init_db()
        self.scanning = False
        self.monitoring = False
        self.active_position = get_active_position()

        root = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))

        title = Label(text="MEXC REAL EXECUTION SCALPER",
                      size_hint_y=None, height=dp(42),
                      font_size="20sp", bold=True)
        root.add_widget(title)

        scroll = ScrollView()
        content = BoxLayout(orientation="vertical", spacing=dp(7), size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))

        self.api_key = self.add_input(content, "API Key", get_setting("api_key", ""))
        self.secret_key = self.add_input(content, "Secret Key", get_setting("secret_key", ""), password=True)
        self.amount = self.add_input(content, "Trade Amount (USDT)", get_setting("amount", "109"))
        self.tp = self.add_input(content, "Take Profit (%)", get_setting("tp", "1.5"))
        self.sl = self.add_input(content, "Stop Loss (%)", get_setting("sl", "2.0"))

        buttons = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(5))
        self.start_btn = Button(text="START SCAN")
        self.stop_btn = Button(text="STOP SCAN", disabled=True)
        self.close_btn = Button(text="CLOSE POSITION", disabled=True)
        self.start_btn.bind(on_release=lambda *_: self.start_scan())
        self.stop_btn.bind(on_release=lambda *_: self.stop_scan())
        self.close_btn.bind(on_release=lambda *_: self.manual_close())
        buttons.add_widget(self.start_btn)
        buttons.add_widget(self.stop_btn)
        buttons.add_widget(self.close_btn)
        content.add_widget(buttons)

        self.symbol_lbl = Label(text="Position: --", size_hint_y=None, height=dp(35))
        self.pnl_lbl = Label(text="PnL: $0.00 (0.00%)", size_hint_y=None, height=dp(35), bold=True)
        content.add_widget(self.symbol_lbl)
        content.add_widget(self.pnl_lbl)

        self.log = Label(text="Log\n", size_hint_y=None, halign="left", valign="top")
        self.log.bind(texture_size=lambda w, s: setattr(w, "height", max(dp(120), s[1])))
        content.add_widget(self.log)

        self.history = Label(text="Closed Trades\n", size_hint_y=None, halign="left", valign="top")
        self.history.bind(texture_size=lambda w, s: setattr(w, "height", max(dp(120), s[1])))
        content.add_widget(self.history)

        scroll.add_widget(content)
        root.add_widget(scroll)

        Clock.schedule_once(lambda *_: self.recover_position(), 0.5)
        return root

    def add_input(self, parent, label, value, password=False):
        box = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(5))
        box.add_widget(Label(text=label, size_hint_x=0.38))
        inp = TextInput(text=value, multiline=False, password=password)
        box.add_widget(inp)
        parent.add_widget(box)
        return inp

    def append_log(self, text):
        self.log.text += text + "\n"
        self.log.texture_update()

    def popup(self, title, message):
        Popup(title=title, content=Label(text=message),
              size_hint=(0.85, 0.35)).open()

    def start_scan(self):
        api, secret = self.api_key.text.strip(), self.secret_key.text.strip()
        if not api or not secret:
            self.popup("Warning", "Enter API Key and Secret Key first.")
            return
        for k, v in [("api_key", api), ("secret_key", secret),
                     ("amount", self.amount.text), ("tp", self.tp.text), ("sl", self.sl.text)]:
            save_setting(k, v)

        if self.scanning:
            return
        self.scanning = True
        self.start_btn.disabled = True
        self.stop_btn.disabled = False
        self.append_log("[START] Scanning top 200 USDT pairs...")
        threading.Thread(target=self.scan_worker, daemon=True).start()

    def stop_scan(self):
        self.scanning = False
        self.start_btn.disabled = False
        self.stop_btn.disabled = True
        self.append_log("[STOP] Scanner stopped.")

    def scan_worker(self):
        symbols = get_top_200_symbols()
        for i, symbol in enumerate(symbols, 1):
            if not self.scanning:
                return
            valid, price, msg = check_trade_conditions(symbol)
            Clock.schedule_once(lambda dt, i=i, s=symbol, p=price, m=msg:
                                self.append_log(f"[{i}/{len(symbols)}] {s} {m}"), 0)
            if valid:
                self.scanning = False
                Clock.schedule_once(lambda dt, s=symbol, p=price: self.execute_buy(s, p), 0)
                return
            time.sleep(0.2)
        Clock.schedule_once(lambda dt: self.scan_finished(), 0)

    def scan_finished(self):
        self.start_btn.disabled = False
        self.stop_btn.disabled = True
        self.append_log("[DONE] No qualifying opportunity found.")

    def execute_buy(self, symbol, approx_price):
        self.start_btn.disabled = True
        self.stop_btn.disabled = True
        try:
            amount = float(self.amount.text)
            tp = float(self.tp.text)
            sl = float(self.sl.text)
        except ValueError:
            amount, tp, sl = 109.0, 1.5, 2.0

        self.append_log(f"[BUY] Sending market buy for {symbol}, {amount:.2f} USDT...")
        ok, msg, entry = buy_market(symbol, amount, self.api_key.text.strip(), self.secret_key.text.strip())
        if not ok or entry <= 0:
            self.append_log(f"[BUY FAILED] {msg}")
            self.start_scan()
            return

        self.active_position = {"symbol": symbol, "entry_price": entry, "amount": amount,
                                "tp_percent": tp, "sl_percent": sl}
        save_active_position(symbol, entry, amount, tp, sl)
        self.symbol_lbl.text = f"Position: {symbol} @ {entry:.8f}"
        self.close_btn.disabled = False
        self.append_log(f"[BUY OK] {symbol} entry {entry:.8f}")
        self.start_monitor()

    def start_monitor(self):
        if self.monitoring:
            return
        self.monitoring = True
        threading.Thread(target=self.monitor_worker, daemon=True).start()

    def monitor_worker(self):
        while self.monitoring and self.active_position:
            pos = self.active_position
            price = get_real_price(pos["symbol"])
            if price and pos["entry_price"] > 0:
                pct = ((price - pos["entry_price"]) / pos["entry_price"]) * 100
                usd = pos["amount"] * pct / 100
                Clock.schedule_once(lambda dt, p=price, u=usd, x=pct: self.update_pnl(p, u, x), 0)
                if pct >= pos["tp_percent"]:
                    Clock.schedule_once(lambda dt: self.close_position("TP"), 0)
                    return
                if pct <= -pos["sl_percent"]:
                    Clock.schedule_once(lambda dt: self.close_position("SL"), 0)
                    return
            time.sleep(1.2)

    def update_pnl(self, price, usd, pct):
        self.pnl_lbl.text = f"PnL: {'+' if usd >= 0 else ''}${usd:.2f} ({'+' if pct >= 0 else ''}{pct:.2f}%)"

    def close_position(self, reason):
        if not self.active_position:
            return
        self.monitoring = False
        pos = self.active_position
        self.append_log(f"[SELL] Closing {pos['symbol']} ({reason})...")
        ok, msg = sell_market(pos["symbol"], self.api_key.text.strip(), self.secret_key.text.strip())
        exit_price = get_real_price(pos["symbol"]) or pos["entry_price"]
        pct = ((exit_price - pos["entry_price"]) / pos["entry_price"]) * 100
        usd = pos["amount"] * pct / 100
        record_closed_trade(pos["symbol"], pos["entry_price"], exit_price, pos["amount"], usd, pct, reason)
        clear_active_position()
        self.active_position = None
        self.close_btn.disabled = True
        self.symbol_lbl.text = "Position: --"
        self.pnl_lbl.text = "PnL: $0.00 (0.00%)"
        self.append_log(f"[CLOSED] {pos['symbol']} {usd:+.2f} USDT ({pct:+.2f}%) | {msg}")
        self.load_history()
        self.start_scan()

    def manual_close(self):
        if self.active_position:
            self.close_position("MANUAL")

    def recover_position(self):
        self.load_history()
        if self.active_position:
            self.symbol_lbl.text = f"Position: {self.active_position['symbol']} @ {self.active_position['entry_price']:.8f}"
            self.close_btn.disabled = False
            self.append_log(f"[RECOVERED] Monitoring {self.active_position['symbol']}")
            self.start_monitor()

    def load_history(self):
        rows = get_all_closed_trades()[:20]
        text = "Closed Trades\n"
        for r in rows:
            text += f"{r[0]} | {r[4]:+.2f} USDT | {r[5]:+.2f}% | {r[6]} | {r[7]}\n"
        self.history.text = text
        self.history.texture_update()


if __name__ == "__main__":
    MEXCApp().run()
