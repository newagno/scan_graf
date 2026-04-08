import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import os
import sys
import logging
import os
import time
import threading
import http.server
import socketserver
import signal
import uuid
from datetime import datetime
from telebot.apihelper import ApiTelegramException
import html

# Configure logging to console and a file for debugging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot_run.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Heavy imports moved inside functions to allow fast startup

# Використовуємо токен явно для уникнення конфліктів середовища
TOKEN = "8793241344:AAFFTLTlINE6M21xqR8qHMGAIer8FR7uM3k"
bot = telebot.TeleBot(TOKEN)

# Словник для збереження налаштувань користувачів
USER_PREFS = {}

EXCHANGES_MAP = {
    '1': 'Binance',
    '2': 'BinanceUSD-M',
    '3': 'OKX',
    '4': 'Bybit',
    '5': 'Bitget',
    '6': 'WhiteBit'
}

def get_prefs(chat_id):
    if chat_id not in USER_PREFS:
        USER_PREFS[chat_id] = {'ex': '2', 'sym': 'BTC/USDT:USDT', 'tfs': '15m,1h,4h'}
    return USER_PREFS[chat_id]

def build_menu_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔄 Змінити біржу", callback_data="change_ex"),
        InlineKeyboardButton("🪙 Змінити актив (пару)", callback_data="change_sym"),
        InlineKeyboardButton("⏱ Змінити таймфрейми", callback_data="change_tfs"),
        InlineKeyboardButton("🚀 СТАРТ СКАНУВАННЯ", callback_data="run_scan")
    )
    return markup

def send_menu(chat_id, message_id=None):
    prefs = get_prefs(chat_id)
    ex_name = EXCHANGES_MAP.get(prefs['ex'], 'Невідома')
    
    text = (
        "📊 <b>Меню Сканера</b> 📊\n\n"
        f"🏢 <b>Біржа:</b> <code>{ex_name}</code>\n"
        f"🪙 <b>Пара:</b> <code>{prefs['sym']}</code>\n"
        f"⏱ <b>Таймфрейми:</b> <code>{prefs['tfs']}</code>\n\n"
        "Натисни кнопку нижче для зміни, або запускай!"
    )
    
    try:
        if message_id:
            bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, 
                                  parse_mode="HTML", reply_markup=build_menu_markup())
        else:
            bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=build_menu_markup())
        logger.info(f"Menu sent to {chat_id}")
    except Exception as e:
        logger.error(f"Error in send_menu: {e}")
        bot.send_message(chat_id, f"Error sending menu. Try again /scan")

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    logger.info(f"Start command from {message.chat.id}")
    help_text = (
        "🤖 <b>Crypto Scanner Bot</b> 🤖\n\n"
        "Використовуй /scan для відкриття меню."
    )
    bot.reply_to(message, help_text, parse_mode="HTML")

@bot.message_handler(commands=['scan'])
def handle_scan(message):
    logger.info(f"Received /scan command from {message.chat.id}")
    send_menu(message.chat.id)

@bot.message_handler(commands=['todo'])
def handle_todo(message):
    try:
        text = message.text.split("/todo", 1)[1].strip()
        if not text:
            bot.reply_to(message, "Usage: /todo [your idea]")
            return
        
        with open("TODO.txt", "a", encoding="utf-8") as f:
            t = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{t}] {text}\n")
        bot.reply_to(message, "✅ Recorded in TODO.txt")
        logger.info(f"Recorded TODO: {text}")
    except Exception as e:
        logger.error(f"TODO error: {e}")

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    chat_id = call.message.chat.id
    prefs = get_prefs(chat_id)
    logger.info(f"Callback data: {call.data} from {chat_id}")
    
    if call.data == "change_ex":
        markup = InlineKeyboardMarkup(row_width=2)
        btns = [InlineKeyboardButton(name, callback_data=f"set_ex_{k}") for k, name in EXCHANGES_MAP.items()]
        markup.add(*btns)
        bot.edit_message_text("Обери біржу:", chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup)
        
    elif call.data.startswith("set_ex_"):
        ex_id = call.data.replace("set_ex_", "")
        prefs['ex'] = ex_id
        send_menu(chat_id, call.message.message_id)
        
    elif call.data == "change_sym":
        msg = bot.edit_message_text("Введи пару (наприклад BTC):", chat_id=chat_id, message_id=call.message.message_id)
        bot.register_next_step_handler(msg, process_sym_step)
        
    elif call.data == "change_tfs":
        msg = bot.edit_message_text("Введи ТФ (наприклад 1h,4h):", chat_id=chat_id, message_id=call.message.message_id)
        bot.register_next_step_handler(msg, process_tfs_step)
        
    elif call.data == "run_scan":
        bot.edit_message_text("⏳ Сканую...", chat_id=chat_id, message_id=call.message.message_id)
        try:
            from trade import CryptoScanner
            scanner = CryptoScanner(prefs['ex'], prefs['sym'], prefs['tfs'])
            report = scanner.run()
            bot.delete_message(chat_id, call.message.message_id)
            
            # Send report in MONO block, escaped for HTML
            safe_report = html.escape(report)
            max_len = 4000
            for i in range(0, len(safe_report), max_len):
                bot.send_message(chat_id, f"<code>{safe_report[i:i+max_len]}</code>", parse_mode="HTML")
            
            send_menu(chat_id)
        except Exception as e:
            logger.exception("Scan error")
            bot.send_message(chat_id, f"Error: {e}")
            send_menu(chat_id)

def process_sym_step(message):
    chat_id = message.chat.id
    sym = message.text.strip().upper()
    if '/' not in sym: sym = f"{sym}/USDT:USDT"
    get_prefs(chat_id)['sym'] = sym
    try: bot.delete_message(chat_id, message.message_id)
    except: pass
    send_menu(chat_id)

def process_tfs_step(message):
    chat_id = message.chat.id
    tfs = message.text.strip().replace(" ", "")
    get_prefs(chat_id)['tfs'] = tfs
    try: bot.delete_message(chat_id, message.message_id)
    except: pass
    send_menu(chat_id)

# --- INSTANCE TRACKING ---
INSTANCE_ID = str(uuid.uuid4())[:8]

# --- SHUTDOWN HANDLING (RENDER-READY) ---
def handle_exit(sig, frame):
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] [{INSTANCE_ID}] Received SHUTDOWN signal ({sig}). Exiting...")
    try:
        bot.stop_polling()
    except:
        pass
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_exit)
signal.signal(signal.SIGINT, handle_exit)

# --- HEALTH CHECK (RENDER-READY) ---
# Render Web Service requires binding to a port.
# If you use a 'Background Worker', this part isn't strictly needed but won't hurt.
def start_health_check():
    try:
        port = int(os.environ.get("PORT", 8080))
        # Simple handler to just return 200 OK for Render's health check
        class HealthHandler(http.server.SimpleHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
            def log_message(self, format, *args):
                return # Silence logging for health checks

        with socketserver.TCPServer(("", port), HealthHandler) as httpd:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Health check server started: PORT {port}")
            httpd.serve_forever()
    except Exception as e:
        print(f"Health Check Server Error: {e}")

# --- РЕЖИМ ВІДНОВЛЕННЯ (RENDER-READY) ---
def run_bot():
    while True:
        try:
            me = bot.get_me()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [{INSTANCE_ID}] Бот @{me.username} запускає polling...")
            # Using bot.polling(non_stop=False) so we control the retry loop here
            bot.polling(non_stop=False, timeout=30, long_polling_timeout=15)
        except ApiTelegramException as e:
            if e.error_code == 409:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [{INSTANCE_ID}] Помилка 409 (Conflict).")
                print("Чекаю 60 секунд, поки старий інстанс відключиться...")
                time.sleep(60)
            else:
                print(f"API Error: {e}")
                time.sleep(10)
        except Exception as e:
            print(f"Unexpected Error: {e}")
            time.sleep(15)

if __name__ == "__main__":
    try:
        # Start the health check server in a background thread
        threading.Thread(target=start_health_check, daemon=True).start()
        
        print(f"\n--- Бот ЗАПУЩЕНИЙ (ID: {INSTANCE_ID}) (Python 3.12) ---")
        print("Натисни Ctrl+C для зупинки.")
        run_bot()
    except KeyboardInterrupt:
        handle_exit(signal.SIGINT, None)
