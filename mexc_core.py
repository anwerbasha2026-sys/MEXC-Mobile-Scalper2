import time
import sqlite3
import requests
import hmac
import hashlib
import threading
from datetime import datetime
from urllib.parse import urlencode

DB_NAME = "trading_bot.db"

# --- Rate Limiter Shared Across Threads ---
_api_rate_lock = threading.Lock()
_api_last_request = 0.0
API_MIN_INTERVAL = 0.15  # Minimum interval between API requests to avoid 429/418 bans

_server_time_offset_ms = 0
_server_time_sync_at = 0.0


def sync_mexc_server_time(force=False):
    global _server_time_offset_ms, _server_time_sync_at
    now_mono = time.monotonic()
    if not force and (now_mono - _server_time_sync_at) < 30:
        return _server_time_offset_ms
    try:
        t0 = int(time.time() * 1000)
        r = requests.get("https://api.mexc.com/api/v3/time", timeout=3)
        t1 = int(time.time() * 1000)
        if r.status_code == 200:
            server_ms = int(r.json().get("serverTime"))
            local_mid = (t0 + t1) // 2
            _server_time_offset_ms = server_ms - local_mid
            _server_time_sync_at = now_mono
    except Exception as e:
        print(f"[TIME SYNC] Failed: {e}")
    return _server_time_offset_ms


def mexc_timestamp(force_sync=False):
    sync_mexc_server_time(force=force_sync)
    return int(time.time() * 1000) + _server_time_offset_ms


# --- Database Operations ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
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
    conn.close()


def save_setting(key, value):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()


def get_setting(key, default=""):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else default


def save_active_position(symbol, entry_price, amount, tp_percent, sl_percent):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM active_position")
    cursor.execute("""
        INSERT INTO active_position (id, symbol, entry_price, amount, tp_percent, sl_percent)
        VALUES (1, ?, ?, ?, ?, ?)
    """, (symbol, entry_price, amount, tp_percent, sl_percent))
    conn.commit()
    conn.close()


def clear_active_position():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM active_position")
    conn.commit()
    conn.close()


def get_active_position():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, entry_price, amount, tp_percent, sl_percent FROM active_position WHERE id=1")
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "symbol": row[0],
            "entry_price": row[1],
            "amount": row[2],
            "tp_percent": row[3],
            "sl_percent": row[4]
        }
    return None


def record_closed_trade(symbol, entry_price, exit_price, amount, pnl_usd, pnl_percent, reason):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT INTO closed_trades (symbol, entry_price, exit_price, amount, pnl_usd, pnl_percent, reason, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (symbol, entry_price, exit_price, amount, pnl_usd, pnl_percent, reason, now_str))
    conn.commit()
    conn.close()


def get_all_closed_trades():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, entry_price, exit_price, amount, pnl_usd, pnl_percent, reason, timestamp FROM closed_trades ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return rows


# --- REST API Rate Limiting & Handling ---
def _api_wait():
    global _api_last_request
    with _api_rate_lock:
        now = time.monotonic()
        wait = API_MIN_INTERVAL - (now - _api_last_request)
        if wait > 0:
            time.sleep(wait)
        _api_last_request = time.monotonic()


def _request_json(method, url, **kwargs):
    kwargs.setdefault("timeout", 5)
    last_response = None

    for attempt in range(3):
        _api_wait()
        try:
            response = requests.request(method, url, **kwargs)
            last_response = response

            if response.status_code in (429, 418, 500, 502, 503, 504):
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = min(float(retry_after), 8.0) if retry_after else (1.0 * (2 ** attempt))
                except ValueError:
                    delay = 1.0 * (2 ** attempt)
                time.sleep(delay)
                continue

            return response
        except requests.RequestException:
            if attempt < 2:
                time.sleep(0.5 * (2 ** attempt))
            else:
                raise

    return last_response


# --- MEXC Market & Trading Functions ---
def get_top_200_symbols():
    """Fetch the top 200 USDT pairs sorted by 24h quote volume."""
    try:
        url = "https://api.mexc.com/api/v3/ticker/24hr"
        response = _request_json("GET", url, timeout=6)
        if response is not None and response.status_code == 200:
            data = response.json()
            usdt_pairs = [
                item for item in data
                if item.get("symbol", "").endswith("USDT")
            ]
            usdt_pairs.sort(
                key=lambda x: float(x.get("quoteVolume", 0) or 0),
                reverse=True
            )
            symbols = [item["symbol"] for item in usdt_pairs[:200]]
            if symbols:
                return symbols
        elif response is not None:
            print(f"[API WARN] get_top_200_symbols returned HTTP {response.status_code}")
    except Exception as e:
        print(f"Top-200 error: {e}")

    time.sleep(3)
    return []


def get_mexc_real_price(symbol):
    try:
        formatted_symbol = symbol.replace("/", "").upper()
        url = "https://api.mexc.com/api/v3/ticker/price"
        response = _request_json("GET", url, params={"symbol": formatted_symbol}, timeout=4)
        if response is not None and response.status_code == 200:
            return float(response.json()["price"])
    except Exception as e:
        print(f"Price error: {e}")
    return None


def get_symbol_free_balance(symbol, api_key, secret_key):
    try:
        asset_name = symbol.replace("USDT", "").replace("/", "").upper()
        url_bal = "https://api.mexc.com/api/v3/account"
        ts = mexc_timestamp()
        p_bal = {"timestamp": ts, "recvWindow": 10000}
        q_bal = urlencode(p_bal)
        sig_bal = hmac.new(secret_key.encode('utf-8'), q_bal.encode('utf-8'), hashlib.sha256).hexdigest()
        p_bal["signature"] = sig_bal
        
        res_bal = _request_json("GET", url_bal, headers={"X-MEXC-APIKEY": api_key}, params=p_bal, timeout=5)
        if res_bal and res_bal.status_code == 200:
            balances = res_bal.json().get("balances", [])
            for b in balances:
                if b["asset"] == asset_name:
                    return float(b["free"])
    except Exception as e:
        print(f"Balance error: {e}")
    return 0.0


def place_mexc_buy_order(symbol, amount_usd, api_key, secret_key):
    if not api_key or not secret_key:
        return False, "API Key / Secret Key missing", 0.0
    
    try:
        url = "https://api.mexc.com/api/v3/order"
        timestamp = mexc_timestamp()
        formatted_amount = f"{float(amount_usd):.2f}"
        
        params = {
            "symbol": symbol.replace("/", "").upper(),
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": formatted_amount,
            "recvWindow": 10000,
            "timestamp": timestamp
        }
        
        query_string = urlencode(params)
        signature = hmac.new(secret_key.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
        params["signature"] = signature
        
        headers = {"X-MEXC-APIKEY": api_key, "Content-Type": "application/json"}
        response = _request_json("POST", url, headers=headers, params=params, timeout=5)
        if not response:
            return False, "No response from exchange", 0.0

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
            return True, "Position already closed on exchange (0 balance)"

        url_order = "https://api.mexc.com/api/v3/order"
        qty_str = f"{free_qty:.6f}".rstrip('0').rstrip('.')
        
        p_order = {
            "symbol": symbol.replace("/", "").upper(),
            "side": "SELL",
            "type": "MARKET",
            "quantity": qty_str,
            "recvWindow": 10000,
            "timestamp": mexc_timestamp()
        }
        q_order = urlencode(p_order)
        sig_order = hmac.new(secret_key.encode('utf-8'), q_order.encode('utf-8'), hashlib.sha256).hexdigest()
        p_order["signature"] = sig_order
        
        res_sell = _request_json("POST", url_order, headers={"X-MEXC-APIKEY": api_key}, params=p_order, timeout=5)
        if not res_sell:
            return False, "No response from exchange on sell"

        res_data = res_sell.json()
        if res_sell.status_code == 200 and "orderId" in res_data:
            return True, "Sell order executed successfully"
        else:
            return False, f"Exchange rejected: {res_data.get('msg', str(res_data))}"
            
    except Exception as e:
        return False, f"Connection error: {str(e)}"


# --- Indicators and Technical Conditions ---
def _get_klines(symbol, interval, limit=500):
    url = "https://api.mexc.com/api/v3/klines"
    response = _request_json(
        "GET",
        url,
        params={
            "symbol": symbol.replace("/", "").upper(),
            "interval": interval,
            "limit": limit,
        },
        timeout=5,
    )
    if response is None or response.status_code != 200:
        return None
    return response.json()


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
    try:
        klines = _get_klines(formatted_symbol, interval, 500)
        if not klines or len(klines) < 201:
            return False
        # نأخذ إغلاقات الشموع المغلقة فقط (تتجاهل الشمعة -1 المفتوحة حالياً)
        closes = [float(k[4]) for k in klines[:-1]]
        ema200 = calculate_ema_series(closes, 200)
        # مقارنة إغلاق آخر شمعة مغلقة (closes[-1]) بقيمة EMA200 المقابلة لها
        return bool(ema200 and closes[-1] > ema200[-1])
    except Exception:
        return False


def _ema_value_at_candle(ema_series, period, candle_index):
    ema_index = candle_index - (period - 1)
    if 0 <= ema_index < len(ema_series):
        return ema_series[ema_index]
    return None


def check_trade_conditions_from_main(symbol):
    try:
        formatted_symbol = symbol.replace("/", "").upper()

        # التأكد من اتجاه EMA200 على الأطر الزمنية الشموع المغلقة
        if not check_ema200_trend(formatted_symbol, "5m"):
            return False, 0.0, "5m trend not bullish"

        if not check_ema200_trend(formatted_symbol, "15m"):
            return False, 0.0, "15m trend not bullish"

        if not check_ema200_trend(formatted_symbol, "60m"):
            return False, 0.0, "60m trend not bullish"

        klines = _get_klines(formatted_symbol, "5m", 500)
        if not klines or len(klines) < 201:
            return False, 0.0, "Insufficient kline data"

        # استبعاد الشمعة الحالية klines[-1] للعمل على الشموع المغلقة بالكامل فقط
        closed_klines = klines[:-1]

        closes = [float(k[4]) for k in closed_klines]
        volumes = [float(k[5]) for k in closed_klines]
        highs = [float(k[2]) for k in closed_klines]
        lows = [float(k[3]) for k in closed_klines]

        if len(closes) < 200 or len(volumes) < 100:
            return False, 0.0, "Insufficient data"

        # سعر إغلاق الشمعة المغلقة الأخيرة
        last_closed_price = closes[-1]

        ema9_series = calculate_ema_series(closes, 9)
        ema21_series = calculate_ema_series(closes, 21)
        ema200_series = calculate_ema_series(closes, 200)

        # التحقق من وجود تقاطع إيجابي بين EMA9 و EMA21 خلال آخر 3 شموع مغلقة
        has_recent_crossover = False
        for offset in range(1, 4):
            idx = len(closes) - offset
            prev_idx = idx - 1

            ema9_prev = _ema_value_at_candle(ema9_series, 9, prev_idx)
            ema21_prev = _ema_value_at_candle(ema21_series, 21, prev_idx)
            ema9_now = _ema_value_at_candle(ema9_series, 9, idx)
            ema21_now = _ema_value_at_candle(ema21_series, 21, idx)

            if None in (ema9_prev, ema21_prev, ema9_now, ema21_now):
                continue

            if ema9_prev <= ema21_prev and ema9_now > ema21_now:
                has_recent_crossover = True
                break

        ema9_now = _ema_value_at_candle(ema9_series, 9, len(closes) - 1)
        ema21_now = _ema_value_at_candle(ema21_series, 21, len(closes) - 1)
        ema200_now = _ema_value_at_candle(ema200_series, 200, len(closes) - 1)

        if None in (ema9_now, ema21_now, ema200_now):
            return False, last_closed_price, "EMA data unavailable"

        # حساب VWAP لآخر 20 شمعة مغلقة
        total_vol = sum(volumes[-20:])
        tp_vol = sum(
            ((highs[i] + lows[i] + closes[i]) / 3) * volumes[i]
            for i in range(-20, 0)
        )
        vwap = tp_vol / total_vol if total_vol > 0 else last_closed_price

        # حجم الشمعة المغلقة الأخيرة مقارنة بمتوسط الشموع السابقة
        avg_vol = sum(volumes[-100:-1]) / 99
        is_volume_high = volumes[-1] > (avg_vol * 1.8)

        if (
            has_recent_crossover
            and ema9_now > ema21_now
            and ema21_now > ema200_now
            and last_closed_price > vwap
            and is_volume_high
        ):
            return True, last_closed_price, "Signal conditions confirmed on closed candle"

        return False, last_closed_price, "Conditions not complete"

    except Exception as e:
        return False, 0.0, f"Error: {e}"


# Initialize Database on module import
init_db()
