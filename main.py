"""
JamesTrade — Главный запуск проекта

Запускает:
  • trading_core.py — торговое ядро (в режиме loop)
  • tg_app.py — Telegram bot
"""

import sys
import subprocess
import threading
import time
import os
import importlib

# === 1. Кодировка ===
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
except:
    pass

print("[Main] ✅ UTF-8 активирован")

# === 2. Проверяем библиотеки ===
REQUIRED_LIBS = [
    "requests", "pandas", "numpy", "python-dotenv", "telebot", "ta"
]

def install_missing():
    missing = []
    for lib in REQUIRED_LIBS:
        try:
            importlib.import_module(lib)
        except ImportError:
            missing.append(lib)

    if missing:
        print(f"[Main] Устанавливаю зависимости: {missing}")
        subprocess.call([sys.executable, "-m", "pip", "install", *missing])
    else:
        print("[Main] ✅ Все зависимости на месте")

install_missing()

# === 3. Загружаем .env ===
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("[Main] ✅ .env загружен")
except:
    print("[Main] ⚠️ .env не найден (не критично)")

# Интервал для цикла торговли
TRADING_LOOP_SEC = int(os.getenv("TRADING_LOOP_SEC", "60"))

# === 4. Запуск торгового ядра ===
def run_trading_core():
    while True:
        try:
            print("\n[Main] 🔥 Запуск trading_core.py ...")
            subprocess.run([sys.executable, "trading_core.py", "loop", str(TRADING_LOOP_SEC)], check=True)
        except:
            print("[Main] ⚠️ trading_core упал. Перезапуск через 5 сек...")
            time.sleep(5)

# === 5. Запуск Telegram бота ===
def run_tg_app():
    while True:
        try:
            print("\n[Main] 💬 Запуск tg_app.py ...")
            subprocess.run([sys.executable, "tg_app.py"], check=True)
        except:
            print("[Main] ⚠️ tg_app упал. Перезапуск через 5 сек...")
            time.sleep(5)

# === 6. Запуск потоков ===
t1 = threading.Thread(target=run_trading_core, daemon=True)
t2 = threading.Thread(target=run_tg_app, daemon=True)

t1.start()
t2.start()

print("\n[Main] 🚀 Проект запущен — торговля + Telegram работают.")

# === 7. Чтобы программа не завершалась ===
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n[Main] 📴 Остановка.")
    sys.exit(0)
''