import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import os
import datetime
import logging
import sys
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

if __name__ == "__main__":
    # Simple check for multiple instances
    lock_file = "bot.lock"
    
    if os.path.exists(lock_file):
        try:
            # Try to test if file is reachable/deletable. 
            # If another process has it open in 'w' mode on Windows, this often errors.
            os.remove(lock_file)
        except Exception:
            print("\n!!! ПОМИЛКА: Схоже, інший примірник бота вже запущено !!!")
            print("Перевір вікна терміналу та зупини всі інші процеси 'python tg_bot.py'.")
            sys.exit(1)

    # We open and KEEP the file open to help Windows prevent deletion by other instances
    f_lock = open(lock_file, "w")
    f_lock.write(str(os.getpid()))
    f_lock.flush()

    try:
        me = bot.get_me()
        logger.info(f"--- STARTING BOT: @{me.username} ---")
        print(f"\n--- Бот ЗАПУЩЕНИЙ: @{me.username} (Python 3.12) ---")
        print("Якщо /scan не працює, перевір файл bot_run.log")
        bot.infinity_polling(timeout=20, long_polling_timeout=20)
    except Exception as e:
        logger.exception("CRITICAL: Bot polling failed")
        print(f"!!! CRITICAL ERROR: {e}")
    finally:
        logger.info("--- BOT STOPPED ---")
        f_lock.close()
        try: os.remove(lock_file)
        except: pass
