# 🤖 Telegram Bot Deployment Guide

Ми щойно створили бота `tg_bot.py`. Щоб він слухав твої команди і виконував їх, його треба "запустити" на будь-якому пристрої чи сервері. Ось дві найкращі інструкції спеціально для тебе:

---

## 📱 Спосіб 1: Termux (Твій телефон як сервер)

Це повністю безкоштовно, треба зробити 1 раз, і бот житиме в кишені!

1. **Завантаж Termux** з F-Droid (або PlayMarket, але краще F-Droid).
2. **Онови пакети та встанови Python:**
   У терміналі Termux введи по черзі наступні команди:
   ```bash
   pkg update && pkg upgrade
   pkg install python git nano
   ```
3. **Скопіюй файли сканера на телефон.** 
   Ти можеш завантажити папку `scan_graf` на свій GitHub, а потім в Termux зробити `git clone <посилання>`, АБО просто перекинути файли `trade.py` та `tg_bot.py` через пам'ять телефону. 
   *(Якщо файли вже в папці Downloads)*:
   ```bash
   termux-setup-storage
   cp -r /storage/emulated/0/Download/scan_graf ~/scan_graf
   cd ~/scan_graf
   ```
4. **Встанови бібліотеки:**
   ```bash
   pip install pyTelegramBotAPI ccxt pandas numpy pandas_ta
   ```
5. **Запусти бота!**
   ```bash
   python tg_bot.py
   ```
> **УВАГА**: Щоб Termux не "засинав", відкрий налаштування акумулятора (Батареї) в Android і надай додатку **Termux** статус **"Не обмежувати"** (No restrictions).

---

## ☁️ Спосіб 2: Render.com (Безкоштовна Хмара)

Цей варіант кращий, бо телефон не розряджатиметься, а бот житиме автономно в дата-центрі.

1. **Залий папку `scan_graf` на свій GitHub.** (Або створи приватний репозиторій)
2. Зайди на [Render.com](https://render.com) і зареєструйся через той же GitHub.
3. Натисни **"New +" -> "Web Service"** (або Background Worker).
4. Вибери свій репозиторій `scan_graf`.
5. У налаштуваннях:
   - **Environment:** `Python 3`
   - **Build Command:** `pip install pyTelegramBotAPI ccxt pandas numpy pandas_ta`
   - **Start Command:** `python tg_bot.py`
6. Знайди вкладку `Environment Variables` (Змінні середовища) і додай:
   - `KEY`: `TELEGRAM_BOT_TOKEN`
   - `VALUE`: `8793241344:AAFFTLTlINE6M21xqR8qHMGAIer8FR7uM3k` *(це твій токен)*
7. Натисни **"Create Web Service"**. Все!

> **Підказка**: У Render.com безкоштовні сервери "засинають" через 15 хв неактивності. Ти будеш писати боту `/scan`, і йому знадобиться секунд 30-40, щоб "прокинутись" і дати відповідь. Це нормально!
