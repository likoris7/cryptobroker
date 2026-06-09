import time
import datetime
import math
import schedule
import threading
import os
import sys

from dotenv import load_dotenv
load_dotenv() 

from http.server import BaseHTTPRequestHandler, HTTPServer
from data_fetcher import CryptoDataFetcher
from strategy import StrategyEngine
from liquidation_api import LiquidationHeatmap
from trade_tracker import TradeTracker
from telegram_notifier import TelegramNotifier
from bingx_trader import BingXTrader
from news_analyzer import NewsAnalyzer
import setup_memory as _sm

# --- CONFIGURATION ---
TRADING_MODE = "DEMO"         # "DEMO" або "REAL"
IS_TRADING_ACTIVE = False
MAX_DAILY_TRADES = 40
SCAN_LIMIT = 200
TRADE_COOLDOWN_SECONDS = 3600 # 1 година
MAX_CONCURRENT_TRADES = 5
# --- TAKE PROFIT & RISK CONFIGURATION ---
TP1_RR = 1.5
TP2_RR = 3.0
PARTIAL_CLOSE_PCT = 0.50
# ---------------------

# --- ANTI-DUPLICATE GUARD ---
_recently_executed: dict = {}
# ----------------------------

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type','text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is alive and running!")
        
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-type','text/plain')
        self.end_headers()
    
    # FIX #1: Suppress default request logging to prevent log spam
    def log_message(self, format, *args):
        pass

def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    print(f"Dummy HTTP Server listening on port {port}...")
    server.serve_forever()

def run_bot_iteration(fetcher, strategy, liq_map, tracker, notifier, bingx_trader, news, symbol):
    """
    Scans a single symbol and returns signal data if found.
    """
    try:
        df_15m = fetcher.fetch_ohlcv(symbol, '15m', limit=100)
        
        if df_15m is None or len(df_15m) < 20:
            return None
            
        # Add indicators and Generate Signal
        df_15m = strategy.add_indicators(df_15m)
        df_15m = strategy.generate_signals(df_15m)
        
        # Evaluate signals on the last CLOSED candle (iloc[-2]) to prevent repainting
        latest_15m = df_15m.iloc[-2]
        
        # --- UPDATE ACTIVE TRADES (Trailing SL / Take Profits) ---
        resolved, be_updates, trailing_sl_updates = tracker.update_candles(symbol, latest_15m)
        for res in resolved:
            is_demo = res.get('is_demo', True)
            prefix = "[DEMO] " if is_demo else ""
            notifier.send_trade_closure_alert(
                res['symbol'], res['direction'], res['result'], res['pnl'], res['entry'], res['exit_price'],
                leverage=res.get('leverage'), margin_mode=res.get('margin_mode', 'ISOLATED')
            )
            if not is_demo:
                bingx_trader.cancel_all_orders(res['symbol'])
            sid = res.get('setup_id', '')
            if sid:
                _sm.record_result(sid, win=(res['result'] == 'WIN'))
        for be in be_updates:
            is_demo = be.get('is_demo', True)
            prefix = "[DEMO] " if is_demo else ""
            notifier.send_message(f"🛡 <b>{prefix}BREAKEVEN SECURED for {be['symbol']}</b>\nMoving SL to entry: {be['entry']}.")
        for trail in trailing_sl_updates:
            is_demo = trail.get('is_demo', True)
            if not is_demo:
                bingx_trader.update_sl(trail['symbol'], trail['new_sl'])

        # --- ATR STAGNATION EXIT ---
        atr     = latest_15m.get('atr', float('nan'))
        atr_avg = latest_15m.get('atr_avg', float('nan'))
        if not math.isnan(atr) and not math.isnan(atr_avg) and atr_avg > 0:
            for t in list(tracker.active_trades):
                if t['symbol'] != symbol:
                    continue
                try:
                    entry_time = datetime.datetime.fromisoformat(t.get('entry_time', ''))
                    age_hours  = (datetime.datetime.now() - entry_time).total_seconds() / 3600
                except Exception:
                    age_hours = 0
                if age_hours >= 4 and atr < atr_avg * 0.5:
                    print(f"[ATR EXIT] {symbol} stagnant (ATR={atr:.6f} < avg={atr_avg:.6f}) after {age_hours:.1f}h")
                    is_demo = t.get('is_demo', True)
                    prefix = "[DEMO] " if is_demo else ""
                    try:
                        ticker    = bingx_trader.client.fetch_ticker(symbol)
                        exit_px   = ticker['last']
                    except Exception:
                        exit_px = t['entry']
                    
                    if not is_demo:
                        bingx_trader.close_position(symbol)
                        bingx_trader.cancel_all_orders(symbol)
                        
                    closed = tracker.force_close_trade(symbol, exit_px)
                    if closed:
                        pnl_pct = (closed['pnl'] / t['entry']) * t.get('leverage', 1) * 100
                        notifier.send_message(
                            f"⏳ <b>{prefix}ATR EXIT — {symbol}</b>\n"
                            f"Ринок застиг ({age_hours:.1f}h відкрита)\n"
                            f"ATR={atr:.5f} vs avg={atr_avg:.5f}\n"
                            f"PnL: <code>{pnl_pct:+.1f}%</code>"
                        )
        # ---------------------------------------------------------

        sig = latest_15m['signal']
        if sig == 0: return None

        # FIX #2: BTC Correlation Filter — ВІДНОВЛЕНО (блокує торги проти тренду BTC)
        if symbol != 'BTC/USDT':
            try:
                btc_df_1h = fetcher.fetch_ohlcv('BTC/USDT', '1h', limit=50)
                if btc_df_1h is not None and len(btc_df_1h) > 0:
                    btc_df_1h['ema_50'] = btc_df_1h['close'].ewm(span=50, adjust=False).mean()
                    btc_last = btc_df_1h.iloc[-1]
                    btc_trend = 1 if btc_last['close'] > btc_last['ema_50'] else -1
                    if (sig == 1 and btc_trend == -1) or (sig == -1 and btc_trend == 1):
                        print(f"[BTC FILTER] ❌ {symbol} signal blocked — against BTC 1H trend.")
                        return None  # ВИПРАВЛЕНО: блокуємо замість просто логування
            except Exception as e:
                print(f"[BTC FILTER] Warning: could not fetch BTC trend: {e}")

        # Return bundle for prioritization
        return {
            'symbol': symbol,
            'sig': sig,
            'prob': latest_15m.get('probability', 80),
            'entry': latest_15m['close'],
            'sl': latest_15m['sl'],
            'tp1': latest_15m['tp1'],
            'tp2': latest_15m['tp2'],
            'setup_id': latest_15m.get('setup_id', ''),
            'df_row': latest_15m
        }
    except Exception as e:
        # print(f"Error scanning {symbol}: {e}")
        return None

def execute_signal(sig_data, liq_map, tracker, notifier, bingx_trader):
    """Perform the actual trade execution with Liquidation mapping and Telegram alerts."""
    symbol = sig_data['symbol']
    sig = sig_data['sig']
    entry_price = sig_data['entry']
    sl = sig_data['sl']
    tp1 = sig_data['tp1']
    tp2 = sig_data['tp2']
    prob = sig_data['prob']
    
    # 1a. Prevent duplicate active trades
    if any(t['symbol'] == symbol for t in tracker.active_trades):
        print(f"⚠️ Skipping {symbol} execution: Trade already active in tracker.")
        return False

    # 1b. Cooldown guard
    now = time.time()
    last_exec = _recently_executed.get(symbol, 0)
    if now - last_exec < TRADE_COOLDOWN_SECONDS:
        remaining = int(TRADE_COOLDOWN_SECONDS - (now - last_exec))
        print(f"⏳ Skipping {symbol}: cooldown active ({remaining}s remaining).")
        return False

    # FIX #3: Перевірка валідності SL перед виконанням угоди
    if sl is None or (isinstance(sl, float) and math.isnan(sl)):
        print(f"[SKIP] {symbol}: invalid SL value ({sl}), skipping trade.")
        return False

    # 2. Risk & Take Profit calculation
    risk = abs(entry_price - sl)

    # FIX #4: Захист від нульового ризику (щоб не ділити на 0 і не відкривати угоди без SL)
    if risk < entry_price * 0.0005:
        print(f"[SKIP] {symbol}: risk too small ({risk:.6f}), skipping trade.")
        return False
    
    sl_pct = risk / entry_price
    lev = bingx_trader.calculate_safe_leverage(symbol, sl_pct)
    
    if sig == 1:
        tp1 = entry_price + (TP1_RR * risk)
        tp2 = entry_price + (TP2_RR * risk)
    else:
        tp1 = entry_price - (TP1_RR * risk)
        tp2 = entry_price - (TP2_RR * risk)
        
    sl  = bingx_trader.round_price(symbol, sl)
    tp1 = bingx_trader.round_price(symbol, tp1)
    tp2 = bingx_trader.round_price(symbol, tp2)
        
    reward = abs(tp2 - entry_price)
    rr_ratio = reward / risk if risk > 0 else 0

    direction_str = "LONG" if sig == 1 else "SHORT"
    is_demo = (TRADING_MODE == "DEMO")
    prefix = "[DEMO] " if is_demo else ""
    
    print(f"Signal detected for {symbol}: {prefix}{direction_str} (Prob: {prob}% | R/R: {rr_ratio:.2f})")
    
    margin_type = "ISOLATED"
    if is_demo:
        success = True
        sltp_ok = True
        trade_margin = 100.0
    else:
        success = bingx_trader.place_order(symbol, direction_str, entry_price, sl, tp1, tp2, margin_type=margin_type)
        sltp_ok = getattr(bingx_trader, 'last_sltp_ok', False)
        try:
            bal = bingx_trader.get_futures_balance()
            trade_margin = bingx_trader.calculate_margin(bal)
        except:
            trade_margin = 10.0

    if success:
        setup_id = sig_data.get('setup_id', '')
        display_direction = f"{prefix}{direction_str}"
        
        notifier.send_signal_alert(symbol, display_direction, entry_price, tp1, tp2, sl, prob, rr_ratio, leverage=lev, margin_mode=margin_type)
        tracker.add_trade(symbol, sig, entry_price, sl, tp1, tp2, leverage=lev, setup_id=setup_id, is_demo=is_demo, margin=trade_margin, margin_mode=margin_type)
        _recently_executed[symbol] = time.time()
        print(f"[TRADE] ✅ {display_direction} {symbol} | setup={setup_id or 'n/a'}")

        if sltp_ok:
            notifier.send_message(
                f"🛡 <b>{prefix}SL/TP встановлені для {symbol}</b>\n"
                f"Стоп-лосс: <code>{sl}</code>\n"
                f"TP1 (50%): <code>{tp1}</code>\n"
                f"TP2: <code>{tp2}</code>"
            )
        else:
            sltp_err = getattr(bingx_trader, 'last_sltp_error', '') or 'невідома причина'
            notifier.send_message(
                f"⚠️ <b>{prefix}SL/TP не вдалось встановити для {symbol}</b>\n"
                f"Причина: <code>{sltp_err}</code>\n"
                f"Бот повторить спробу через 5 хв автоматично.\n"
                f"Перевір ордери вручну на BingX!"
            )
        return True
    else:
        last_err = getattr(bingx_trader, 'last_error', 'Unknown Error')
        print(f"[FAIL] {direction_str} {symbol} execution failed: {last_err}")
        return False

def sync_active_protections(tracker, bingx_trader, notifier):
    active = tracker.active_trades
    if not active: return
    print(f"--- Syncing protections & liquidations for {len(active)} active trades ---")
    
    for t in active:
        is_demo = t.get('is_demo', True)
        if is_demo:
            continue
            
        try:
            symbol = t['symbol']
            pos = bingx_trader.get_position(symbol)
            if not pos:
                print(f"[INFO] {symbol} has no active position on exchange. (Possible manual close?)")
                continue
            
            liq_price = float(pos.get('liquidationPrice', 0) or 0)
            if liq_price > 0:
                t['liquidation_price'] = liq_price
            
            ticker = bingx_trader.client.fetch_ticker(symbol)
            curr_price = ticker['last']
            
            liq_dist_pct = abs(curr_price - liq_price) / curr_price if liq_price > 0 else 1.0
            sl_dist_pct = abs(curr_price - t['sl']) / curr_price
            
            print(f"[SCAN] {symbol} | Price: {curr_price} | SL: {t['sl']} | Liq: {liq_price} (Dist: {liq_dist_pct:.2%})")
            
            if liq_dist_pct < 0.02 and liq_price > 0:
                notifier.send_message(f"🚨 <b>LIQUIDATION DANGER for {symbol}</b>\nPrice: {curr_price}\nLiquidation: <b>{liq_price}</b>\nDistance: <b>{liq_dist_pct:.2%}</b>")
            
            is_danger = (t['direction'] == 1 and liq_price > t['sl']) or (t['direction'] == -1 and liq_price < t['sl'] and liq_price > 0)

            if is_danger:
                dist_to_liq = abs(curr_price - liq_price)
                dist_to_sl  = abs(curr_price - t['sl'])
                print(f"[LIQ DANGER] {symbol}: Liq ({liq_price}) before SL ({t['sl']}). Adjusting leverage...")
                notifier.send_message(
                    f"⚠️ <b>ЛІКВІДАЦІЯ БЛИЖЧЕ НІЖ СТОП для {symbol}</b>\n"
                    f"Ціна: <code>{curr_price}</code>\n"
                    f"Стоп-лосс: <code>{t['sl']}</code> (відстань: {dist_to_sl:.4f})\n"
                    f"Ліквідація: <code>{liq_price}</code> (відстань: {dist_to_liq:.4f})\n"
                    f"🔽 Зменшую плече та переоткриваю угоду..."
                )

                success = bingx_trader.reenter_with_reduced_leverage(
                    symbol, t['direction'], t['sl'], t['tp1'], t['tp2']
                )

                if success:
                    try:
                        new_ticker = bingx_trader.client.fetch_ticker(symbol)
                        t['entry'] = float(new_ticker.get('last', curr_price))
                        tracker.save_active_trades()
                    except Exception:
                        pass
                    notifier.send_message(
                        f"✅ <b>Угода переоткрита: {symbol}</b>\n"
                        f"Новий вхід: <code>{t['entry']}</code>\n"
                        f"SL: <code>{t['sl']}</code> | TP: <code>{t['tp2']}</code>\n"
                        f"♻️ Плече знижено — ліквідація тепер безпечно далі SL"
                    )
                else:
                    print(f"[EMERGENCY] Re-entry failed for {symbol}. Panic closing...")
                    notifier.send_message(
                        f"💥 <b>АВАРІЙНЕ ЗАКРИТТЯ {symbol}</b>\n"
                        f"Не вдалося переоткрити угоду. Закриваю для збереження капіталу."
                    )
                    bingx_trader.close_position(symbol)
                    tracker.force_close_trade(symbol, curr_price)
                continue
            
            bingx_trader.sync_sl_tp(symbol, t['sl'], t['tp1'], t['tp2']) 

        except Exception as e:
            print(f"Sync failed for {t['symbol']}: {e}")

def send_daily_report_job(tracker, notifier):
    stats = tracker.get_daily_stats_and_reset()
    notifier.send_daily_report(stats['wins'], stats['losses'], stats['pnl'])

def main_loop(notifier, tracker, fetcher, strategy, liq_map, bingx_trader, news):
    print("AI Crypto Trading Thread STARTED.")
    schedule.every().day.at("21:00").do(send_daily_report_job, tracker, notifier)
    schedule.every(5).minutes.do(sync_active_protections, tracker, bingx_trader, notifier)
    
    while True:
        try:
            if not IS_TRADING_ACTIVE:
                time.sleep(5)
                continue
            
            daily_count = tracker.get_trades_count_today()
            if daily_count >= MAX_DAILY_TRADES:
                print(f"Daily quota reached ({daily_count}/{MAX_DAILY_TRADES}). Sleeping...")
                time.sleep(600)
                continue
                
            symbols = fetcher.get_usdt_futures_symbols()
            
            signals_found = []
            for sym in symbols:
                if not IS_TRADING_ACTIVE: break
                res = run_bot_iteration(fetcher, strategy, liq_map, tracker, notifier, bingx_trader, news, sym)
                if res:
                    signals_found.append(res)
                time.sleep(0.01)
            
            if signals_found:
                signals_found.sort(key=lambda x: x['prob'], reverse=True)
                active_count = len(tracker.active_trades)
                slots_available = max(0, MAX_CONCURRENT_TRADES - active_count)
                for sig in signals_found[:slots_available]:
                    daily_count = tracker.get_trades_count_today()
                    if daily_count >= MAX_DAILY_TRADES:
                        break
                    execute_signal(sig, liq_map, tracker, notifier, bingx_trader)

            schedule.run_pending()
            time.sleep(15)
        except Exception as e:
            print(f"Error in trading loop: {e}")
            time.sleep(10)

if __name__ == "__main__":
    from telebot import types
    
    notifier = TelegramNotifier()
    tracker = TradeTracker()
    fetcher = CryptoDataFetcher('bingx')
    strategy = StrategyEngine()
    liq_map = LiquidationHeatmap()
    bingx_trader = BingXTrader()
    news = NewsAnalyzer()
    
    bot = notifier.bot
    try:
        print("Cleaning up old Telegram sessions...")
        bot.delete_webhook(drop_pending_updates=True)
        time.sleep(1)
        try:
            bot.get_updates(offset=-1, timeout=0)
        except Exception:
            pass
        time.sleep(5)
        me = bot.get_me()
        print(f"Connected to Telegram as: @{me.username}")
    except Exception as e:
        print(f"Webhook cleanup note: {e}")
    
    from telebot.types import ReplyKeyboardMarkup, KeyboardButton
    def get_main_keyboard():
        markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add(KeyboardButton("🟢 Старт"), KeyboardButton("🛑 Стоп"))
        markup.add(KeyboardButton("🟢 Демо-трейдинг"), KeyboardButton("🔴 Реал-трейдинг"))
        markup.add(KeyboardButton("📅 Звіт за місяць"), KeyboardButton("📉 Ліквідації"))
        markup.add(KeyboardButton("📊 Аналіз"), KeyboardButton("🏆 Звітність"))
        markup.add(KeyboardButton("🧪 Тест"), KeyboardButton("🛠 Відлагодження"))
        markup.add(KeyboardButton("🔍 Перевірка"), KeyboardButton("🧹 Чистка"))
        markup.add(KeyboardButton("💾 Бекап"), KeyboardButton("📥 Download"))
        return markup

    @bot.message_handler(commands=['start'])
    @bot.message_handler(func=lambda message: message.text == "🟢 Старт")
    def handle_start(message):
        print(f"📥 Received START from {message.from_user.username}")
        global IS_TRADING_ACTIVE
        global TRADING_MODE
        notifier.chat_id = message.chat.id
        IS_TRADING_ACTIVE = True
        mode_text = globals().get('TRADING_MODE', 'DEMO')
        text = f"🤖 <b>Crypto_Pepper AI</b>\n\n🟢 Двигун <b>АКТИВОВАНО</b>!\nЯ в реальному часі сканую ф'ючерси BingX...\n\nПоточний режим: <b>{mode_text}</b>"
        bot.reply_to(message, text, parse_mode="HTML", reply_markup=get_main_keyboard())

    @bot.message_handler(func=lambda message: message.text == "🛑 Стоп")
    def handle_stop(message):
        global IS_TRADING_ACTIVE
        IS_TRADING_ACTIVE = False
        bot.reply_to(message, "🛑 <b>Торгівлю зупинено.</b>", parse_mode="HTML", reply_markup=get_main_keyboard())

    @bot.message_handler(func=lambda message: message.text in ["🟢 Демо-трейдинг", "🔴 Реал-трейдинг"])
    def handle_mode_switch(message):
        global TRADING_MODE
        if message.text == "🟢 Демо-трейдинг":
            TRADING_MODE = "DEMO"
            bot.reply_to(message, "✅ <b>Режим змінено на ДЕМО-ТРЕЙДИНГ.</b>\nРеальні ордери на біржу НЕ відправляються.", parse_mode="HTML", reply_markup=get_main_keyboard())
        elif message.text == "🔴 Реал-трейдинг":
            TRADING_MODE = "REAL"
            bot.reply_to(message, "⚠️ <b>Режим змінено на РЕАЛ-ТРЕЙДИНГ.</b>\nБот торгуватиме твоїми реальними грошима на BingX!", parse_mode="HTML", reply_markup=get_main_keyboard())

    BACKUP_FILES = {
        'trades.db': os.path.join(os.path.dirname(__file__), 'trades.db'),
        'leverage_memory.json': os.path.join(os.path.dirname(__file__), 'leverage_memory.json'),
        'setup_memory.json': os.path.join(os.path.dirname(__file__), 'setup_memory.json'),
    }

    @bot.message_handler(func=lambda m: m.text in ['💾 Бекап', '📥 Download'])
    def handle_backup(message):
        notifier.chat_id = message.chat.id
        bot.send_message(message.chat.id, '💾 <b>Надсилаю резервні копії...</b>', parse_mode='HTML')
        sent = 0
        for name, path in BACKUP_FILES.items():
            if os.path.exists(path):
                with open(path, 'rb') as f:
                    bot.send_document(message.chat.id, f, caption=f'📄 {name}')
                sent += 1
        if sent == 0:
            bot.send_message(message.chat.id, '⚠️ Файли ще не створені (немає трейдів).')
        else:
            bot.send_message(message.chat.id, f'✅ Надіслано {sent} файл(ів).')

    @bot.message_handler(content_types=['document'])
    def handle_restore(message):
        doc = message.document
        fname = doc.file_name
        if fname not in BACKUP_FILES:
            bot.reply_to(message, f'⚠️ Невідомий файл: {fname}. Надсилай лише: {", ".join(BACKUP_FILES.keys())}')
            return
        try:
            file_info = bot.get_file(doc.file_id)
            downloaded = bot.download_file(file_info.file_path)
            dest = BACKUP_FILES[fname]
            with open(dest, 'wb') as f:
                f.write(downloaded)
            bot.reply_to(message, f'✅ <b>{fname}</b> відновлено! Перезапусти бота щоб застосувати.', parse_mode='HTML')
            print(f'[RESTORE] {fname} restored from Telegram.')
        except Exception as e:
            bot.reply_to(message, f'❌ Помилка відновлення {fname}: {e}')

    # 0. Sync protections for existing trades immediately
    sync_active_protections(tracker, bingx_trader, notifier)
    
    # 1. Start HTTP dummy server thread
    threading.Thread(target=run_http_server, daemon=True).start()
    
    # 2. Start trading loop thread
    threading.Thread(target=main_loop, args=(notifier, tracker, fetcher, strategy, liq_map, bingx_trader, news,), daemon=True).start()
    
    # 3. Start Telegram Polling
    print("Bot is fully operational. Starting Telegram polling...")
    from telebot.apihelper import ApiTelegramException
    max_retries = 10
    for attempt in range(max_retries):
        try:
            bot.infinity_polling(
                timeout=20,
                long_polling_timeout=10,
                skip_pending=True,
                allowed_updates=['message', 'callback_query']
            )
            break
        except ApiTelegramException as e:
            if "409" in str(e) or "Conflict" in str(e):
                wait = 15 * (attempt + 1)
                print(f"[409] Another instance is running. Waiting {wait}s before retry ({attempt+1}/{max_retries})...")
                time.sleep(wait)
                try:
                    bot.get_updates(offset=-1, timeout=0)
                except Exception:
                    pass
            else:
                raise
        except Exception as e:
            print(f"[POLL ERROR] {e}. Restarting in 10s...")
            time.sleep(10)
