import sys
import threading
import traceback
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup
from kivy.clock import Clock, mainthread
from kivy.core.window import Window
from kivy.utils import platform

from mexc_core import (
    get_top_200_symbols,
    check_trade_conditions_from_main,
    place_mexc_buy_order,
    place_mexc_sell_order_market,
    get_mexc_real_price,
    save_active_position,
    clear_active_position,
    get_active_position,
)


class ScannerThread(threading.Thread):
    def __init__(self, stop_event, app):
        super().__init__(daemon=True)
        self.stop_event = stop_event
        self.app = app

    def run(self):
        try:
            while not self.stop_event.is_set():
                symbols = get_top_200_symbols()
                if not symbols:
                    self.app.log("[SCAN] Could not load Top 200. Retrying in 5s...")
                    self.stop_event.wait(5.0)
                    continue

                self.app.log(f"[SCAN] Starting scan of {len(symbols)} symbols.")

                for index, symbol in enumerate(symbols, 1):
                    if self.stop_event.is_set():
                        break
                    try:
                        valid, price, msg = check_trade_conditions_from_main(symbol)
                        if valid:
                            self.app.log(f"[SIGNAL] {symbol} | ${price:.8f} | {msg}")
                            Clock.schedule_once(
                                lambda dt, s=symbol, p=price: self.app.on_signal(s, p)
                            )
                            self.stop_event.set()
                            return
                        self.app.log(f"[{index}/{len(symbols)}] {symbol} | {msg}")
                    except Exception as exc:
                        self.app.log(f"[ERROR] {symbol}: {exc}")
                    self.stop_event.wait(0.25)

                if not self.stop_event.is_set():
                    self.app.log("[SCAN] Cycle completed. Refreshing.")
        except Exception as exc:
            self.app.log(f"[CRASH PREVENTED] {exc}")
            self.app.log(traceback.format_exc())


class MonitorThread(threading.Thread):
    def __init__(self, stop_event, app, symbol, entry_price, amount, tp, sl):
        super().__init__(daemon=True)
        self.stop_event = stop_event
        self.app = app
        self.symbol = symbol
        self.entry_price = float(entry_price)
        self.amount = float(amount)
        self.tp = float(tp)
        self.sl = float(sl)

    def run(self):
        try:
            while not self.stop_event.is_set():
                try:
                    price = get_mexc_real_price(self.symbol)
                    if price and self.entry_price > 0:
                        pnl_pct = ((price - self.entry_price) / self.entry_price) * 100.0
                        pnl_usd = self.amount * pnl_pct / 100.0
                        Clock.schedule_once(
                            lambda dt, p=pnl_usd, pc=pnl_pct: self.app.update_pnl(p, pc)
                        )
                        if pnl_pct >= self.tp:
                            self.app.log(f"[TP] Target reached: {pnl_pct:.3f}%")
                            Clock.schedule_once(lambda dt: self.app.close_position("TP"))
                            return
                        if pnl_pct <= -self.sl:
                            self.app.log(f"[SL] Stop reached: {pnl_pct:.3f}%")
                            Clock.schedule_once(lambda dt: self.app.close_position("SL"))
                            return
                except Exception as exc:
                    self.app.log(f"[MONITOR ERROR] {exc}")
                self.stop_event.wait(1.5)
        except Exception as exc:
            self.app.log(f"[MONITOR CRASH] {exc}")


class TradingApp(App):
    title = "MEXC Scalper Mobile"

    def build(self):
        if platform in ("android", "ios"):
            # شاشة كاملة على الجوال
            from android.permissions import request_permissions, Permission
            request_permissions([Permission.INTERNET])

        root = BoxLayout(orientation="vertical", padding=10, spacing=8)

        # --- API ---
        api_box = BoxLayout(orientation="vertical", spacing=4, size_hint_y=None, height=120)
        self.api_key = TextInput(hint_text="API Key", multiline=False, size_hint_y=None, height=40)
        self.secret_key = TextInput(hint_text="Secret Key", password=True, multiline=False, size_hint_y=None, height=40)
        api_box.add_widget(Label(text="MEXC API", size_hint_y=None, height=25))
        api_box.add_widget(self.api_key)
        api_box.add_widget(self.secret_key)

        # --- Settings ---
        cfg = GridLayout(cols=2, spacing=4, size_hint_y=None, height=160)
        cfg.add_widget(Label(text="Amount ($)"))
        self.amount = TextInput(text="79", multiline=False)
        cfg.add_widget(self.amount)
        cfg.add_widget(Label(text="TP (%)"))
        self.tp = TextInput(text="1.5", multiline=False)
        cfg.add_widget(self.tp)
        cfg.add_widget(Label(text="SL (%)"))
        self.sl = TextInput(text="2.0", multiline=False)
        cfg.add_widget(self.sl)
        self.paper_row = BoxLayout()
        self.paper = CheckBox(active=True, size_hint_x=None, width=40)
        self.paper_row.add_widget(self.paper)
        self.paper_row.add_widget(Label(text="Paper Trading Mode"))
        cfg.add_widget(self.paper_row)

        # --- Buttons ---
        btns = BoxLayout(spacing=6, size_hint_y=None, height=50)
        self.start_btn = Button(text="Start Scan", background_color=(0.1, 0.6, 0.2, 1))
        self.stop_btn = Button(text="Stop Scan", disabled=True, background_color=(0.7, 0.2, 0.2, 1))
        self.close_btn = Button(text="Close Position", disabled=True, background_color=(0.8, 0.5, 0.1, 1))
        btns.add_widget(self.start_btn)
        btns.add_widget(self.stop_btn)
        btns.add_widget(self.close_btn)

        self.start_btn.bind(on_press=self.start_scan)
        self.stop_btn.bind(on_press=self.stop_scan)
        self.close_btn.bind(on_press=lambda x: self.close_position("MANUAL"))

        # --- Status ---
        self.status_lbl = Label(text="Status: Ready", size_hint_y=None, height=25)
        self.position_lbl = Label(text="Position: None", size_hint_y=None, height=25)
        self.pnl_lbl = Label(text="PnL: --", size_hint_y=None, height=25)

        # --- Log ---
        self.log_view = ScrollView()
        self.log_text = Label(
            text="", size_hint_y=None, halign="left", valign="top",
            font_size="12sp", markup=True
        )
        self.log_text.bind(width=lambda *x: setattr(self.log_text, "text_size", (self.log_text.width, None)))
        self.log_view.add_widget(self.log_text)

        root.add_widget(api_box)
        root.add_widget(cfg)
        root.add_widget(btns)
        root.add_widget(self.status_lbl)
        root.add_widget(self.position_lbl)
        root.add_widget(self.pnl_lbl)
        root.add_widget(Label(text="Activity Log", size_hint_y=None, height=25))
        root.add_widget(self.log_view)

        self.scanner = None
        self.monitor = None
        self.stop_event = threading.Event()
        self.load_position()
        return root

    def log(self, text):
        Clock.schedule_once(lambda dt: self._append_log(text))

    def _append_log(self, text):
        self.log_text.text += text + "\n"
        # حد أقصى للسجل لتفادي استهلاك الذاكرة
        lines = self.log_text.text.split("\n")
        if len(lines) > 300:
            self.log_text.text = "\n".join(lines[-300:])

    @mainthread
    def update_pnl(self, pnl_usd, pnl_pct):
        self.pnl_lbl.text = f"PnL: ${pnl_usd:.4f} ({pnl_pct:.3f}%)"

    def safe_float(self, widget, default):
        try:
            return float(widget.text.strip())
        except Exception:
            return default

    def start_scan(self, *args):
        if self.scanner and self.scanner.is_alive():
            return
        if not self.paper.active and (
            not self.api_key.text.strip() or not self.secret_key.text.strip()
        ):
            self.popup("API Required", "Enter MEXC API Key and Secret Key.")
            return
        self.stop_event = threading.Event()
        self.start_btn.disabled = True
        self.stop_btn.disabled = False
        self.status_lbl.text = "Status: Scanning..."
        self.log("[SYSTEM] Scanner started.")
        self.scanner = ScannerThread(self.stop_event, self)
        self.scanner.start()

    def stop_scan(self, *args):
        self.stop_event.set()
        self.status_lbl.text = "Status: Stopping..."
        self.log("[SYSTEM] Stop requested.")

    def on_signal(self, symbol, price):
        amount = self.safe_float(self.amount, 79.0)
        self.log(f"[BUY] Sending buy order {symbol} for ${amount:.2f}...")
        if self.paper.active:
            ok, msg, fill = True, "Paper order", price
        else:
            ok, msg, fill = place_mexc_buy_order(
                symbol, amount,
                self.api_key.text.strip(), self.secret_key.text.strip()
            )
        if not ok:
            self.log(f"[BUY FAILED] {msg}")
            self.stop_event.clear()
            self.start_btn.disabled = False
            self.status_lbl.text = "Status: Resuming scan..."
            Clock.schedule_once(lambda dt: self.start_scan(), 0.3)
            return

        entry = float(fill or price)
        tp = self.safe_float(self.tp, 1.5)
        sl = self.safe_float(self.sl, 2.0)
        save_active_position(symbol, entry, amount, tp, sl)

        self.position_lbl.text = f"Position: {symbol} @ {entry:.8f}"
        self.close_btn.disabled = False
        self.status_lbl.text = "Status: Position Open"
        self.log(f"[BUY OK] {msg} | Entry: {entry:.8f}")

        self.stop_event = threading.Event()
        self.monitor = MonitorThread(
            self.stop_event, self, symbol, entry, amount, tp, sl
        )
        self.monitor.start()

    def close_position(self, reason="MANUAL", *args):
        pos = get_active_position()
        if not pos:
            self.log("[SELL] No active position.")
            self.close_btn.disabled = True
            return
        self.log(f"[SELL] Closing {pos['symbol']} | reason: {reason}")
        if self.paper.active:
            ok, msg = True, "Paper position closed"
        else:
            ok, msg = place_mexc_sell_order_market(
                pos["symbol"],
                self.api_key.text.strip(), self.secret_key.text.strip()
            )
        if ok:
            clear_active_position()
            self.position_lbl.text = "Position: None"
            self.pnl_lbl.text = "PnL: --"
            self.close_btn.disabled = True
            self.log(f"[SELL OK] {msg}")
            self.status_lbl.text = "Status: Ready"
            self.start_btn.disabled = False
            self.stop_event = threading.Event()
        else:
            self.log(f"[SELL FAILED] {msg}")
            self.status_lbl.text = "Status: Position still open - retry"

    def load_position(self):
        try:
            pos = get_active_position()
            if pos:
                self.position_lbl.text = f"Position: {pos['symbol']} @ {float(pos['entry_price']):.8f}"
                self.close_btn.disabled = False
                self.log("[RECOVERY] Active position recovered.")
        except Exception:
            pass

    def popup(self, title, msg):
        content = BoxLayout(orientation="vertical", padding=10, spacing=10)
        content.add_widget(Label(text=msg))
        btn = Button(text="OK", size_hint_y=None, height=40)
        content.add_widget(btn)
        pop = Popup(title=title, content=content, size_hint=(0.8, 0.4))
        btn.bind(on_press=pop.dismiss)
        pop.open()


if __name__ == "__main__":
    TradingApp().run()
