# tg_app.py
# -*- coding: utf-8 -*-
import sys
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import os
import asyncio 
import logging
import json
import time
import re
from datetime import datetime, timedelta
from typing import Tuple, Optional, Dict, List

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
TRADES_FILE = os.getenv("TRADES_FILE", "./trades.json")
SYMBOLS_ENV = [s.strip().upper() for s in os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT").split(",") if s.strip()]

# aiogram
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# local DB helper (expected methods used in this file)
import db_json as db  # create_default_user, get_user, set_api_keys, update_setting, load_users, set_subscription, get_trades_for_user

# optional Bybit client module (may be None if not present)
try:
    import client as client_module
except Exception:
    client_module = None

# Crypto disabled by design (we store plain keys)
KEY_FILE = ".fernet.key"
HAVE_CRYPTO = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# requests session with retry/backoff
session = requests.Session()
retries = Retry(total=3, backoff_factor=0.4, status_forcelist=(500, 502, 503, 504))
adapter = HTTPAdapter(max_retries=retries)
session.mount("https://", adapter)
session.mount("http://", adapter)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Payment / CryptoBot settings
PAYMENT_AMOUNT = float(os.getenv("PAYMENT_AMOUNT_USDT", "7"))
PAYMENT_ASSET = os.getenv("PAYMENT_ASSET", "USDT")
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN", "")  # optional
CRYPTO_CREATE_INVOICE_URL = "https://pay.crypt.bot/api/createInvoice"
CRYPTO_GET_INVOICES_URL = "https://pay.crypt.bot/api/getInvoices"
CRYPTO_HEADERS = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN} if CRYPTOBOT_TOKEN else {}

ERROR_NOTIFY_INTERVAL = 300
_LAST_ERROR_NOTIFY: Dict[str, float] = {}

def _should_notify(key: str) -> bool:
    now = time.time()
    last = _LAST_ERROR_NOTIFY.get(key, 0)
    if now - last > ERROR_NOTIFY_INTERVAL:
        _LAST_ERROR_NOTIFY[key] = now
        return True
    return False

async def _async_send_admin(text: str):
    try:
        if ADMIN_ID:
            await bot.send_message(ADMIN_ID, text)
    except Exception:
        logger.exception("Failed to send admin notification (async)")

def notify_admin_rate_limited_sync(text: str, key: str = "default_notify"):
    if not ADMIN_ID:
        return
    if not _should_notify(key):
        return
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_async_send_admin(text))
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            session.post(url, json={"chat_id": ADMIN_ID, "text": text}, timeout=5)
    except Exception:
        logger.exception("notify_admin_rate_limited_sync failed")

# Localization (RU / EN / ES)
LOCALE = {
    "ru": {
        "choose_lang": "Выберите язык / Choose language:",
        "welcome": "👋 Привет! Это JamesTrade.\nВыберите пункт меню ниже:",
        "menu_subscription": "📊 Подписка",
        "menu_settings": "⚙️ Настройки",
        "menu_trades": "💹 Мои сделки",
        "menu_bot_on": "🤖 Бот: ВКЛ",
        "menu_bot_off": "🤖 Бот: ВЫКЛ",
        "menu_support": "🆘 Поддержка",
        "menu_info": "ℹ️ ИНФО",
        "enter_api_key": "Введите API Key (в следующем сообщении):",
        "enter_api_secret": "Теперь введите API Secret (в следующем сообщении):",
        "keys_saved_ok": "✅ API ключи сохранены и успешно проверены.",
        "keys_saved_warn": "❗️ Ключи сохранены, но проверка не прошла: {info}\nПроверьте права ключей (read/balance/trade) и флаг TESTNET.",
        "keys_saved_no_client": "Ключи сохранены, но сервер не настроен для проверки ключей.",
        "no_keys": "❌ У вас не добавлены API ключи. Добавьте их в ⚙️ Настройки -> API ключи.",
        "invalid_keys": "❌ Неверные или недостаточные права API ключей: {info}\nПроверьте ключи и права (баланс/торговля).",
        "save_failed": "❌ Не удалось сохранить ключи. Попробуйте позже.",
        "subscribe_required": "🔒 У вас нет активной подписки. Купите подписку через меню или /buy.",
        "trading_on": "▶️ Торговля включена.",
        "trading_off": "⏸️ Торговля отключена.",
        "buy_success": "👉 Ссылка на оплату: {url}\nПосле оплаты подписка активируется автоматически.",
        "buy_fail": "❌ Не удалось создать счёт. Попробуйте позже.",
        "invoice_paid": "✅ Оплата получена! Ваша подписка активирована на {days} дней.",
        "settings_menu_title": "⚙️ Меню настроек — выберите раздел:",
        "settings_testnet_status": "🌐 TESTNET переключён {status}.",
        "settings_back": "⬅ Назад",
        "settings_lang": "🌐 Язык / Language",
        "trade_mode_title": "Режим торговли — текущий: {current}\nВыберите режим:",
        "trade_mode_set_ok": "✅ Режим торговли установлен: {mode}",
        "pairs_title": "Выберите торговые пары (нажмите, чтобы переключить) или введите свои:",
        "pairs_saved": "✅ Выбранные пары сохранены: {pairs}",
        "pairs_input_prompt": "Введите пары через запятую или пробелы (например: BTCUSDT, ETHUSDT или BTC/USDT):",
        "pairs_saved_partial": "✅ Сохранены: {valid}. Необработаны/недействительны: {invalid}",
        "pairs_invalid_none": "❌ Никакие введённые пары не были распознаны как действительные: {invalid}",
        "pairs_manual_saved": "✅ Ваши пары сохранены: {pairs}",
        "pairs_manual_button": "✏️ Ввести свои",
        "pairs_done_button": "✅ Готово",
        "pairs_title_short": "Выбор пар",
        "risk_title": "Текущие risk-настройки:\n{fields}\n\nИзменить: SET KEY VALUE",
        "indicators_menu_title": "⚙️ Настройки индикаторов — выберите раздел:",
        "indicators_global_title": "🌐 Глобальные настройки индикаторов:\nНажмите кнопку, чтобы переключить индикатор.",
        "indicators_advanced_text": "🔧 Расширенные настройки (текущие):\n\n{settings}\n\nЧтобы изменить значение используйте команду:\nSET KEY VALUE\n\nПример: SET RSI_PERIOD 14",
        "support_prompt": "Опишите проблему — ваше сообщение будет отправлено админу. Для отмены введите /cancel",
        "support_sent": "✅ Сообщение отправлено в поддержку. Ожидайте ответа.",
        "support_failed": "Ошибка при отправке в поддержку. Попробуйте позже.",
        "no_trades": "💤 Сделок пока нет.",
        "trades_end": "— Конец —",
        "trade_notification": "⚡️ Сделка: {symbol} {side}\nЦена: {price}\nОбъём: {qty}\nPnL: {pnl}\n{ts}",
        "admin_only": "❌ Только админ.",
        "invalid_user_id": "❌ Некорректный идентификатор пользователя.",
        "enter_reply_prompt": "Введите ответ пользователю {user_id}. Для отмены: /cancel",
        "reply_sent": "✅ Ответ отправлен пользователю.",
        "action_cancelled": "Действие отменено.",
        "set_usage": "Ошибка. Формат: SET KEY VALUE",
        "error_data": "Ошибка данных.",
        "welcome_short": "🤖 Команды: /buy — оплатить подписку; SET KEY VALUE — изменить настройку.",
        # New strings for pair management and info
        "manage_pairs_title": "Ваши текущие пары:\n{pairs}\n\nВыберите действие:",
        "add_pair_button": "➕ Добавить пару",
        "remove_pair_button": "➖ Удалить пару",
        "no_pairs_yet": "У вас ещё нет сохранённых пар.",
        "pair_removed": "✅ Пара {pair} удалена.",
        "pair_added": "✅ Пара(ы) добавлены: {pairs}",
        "pair_not_found": "❌ Пара {pair} не найдена в вашем списке.",
       "info_text": """
🤖 КАК РАБОТАЕТ БОТ
Этот бот подключается напрямую к Bybit с помощью ваших API-ключей. После добавления ключей и включения бота он анализирует рынок и выполняет сделки на вашем аккаунте Bybit, используя средства Единого торгового аккаунта. Бот не может выводить средства — вывод остаётся под контролем пользователя.

💎 ЗАЧЕМ НУЖНА ПОДПИСКА?
Подписка даёт доступ к боту и поддерживает дальнейшую разработку, обслуживание и клиентскую поддержку. Подписка предоставляет доступ к ПО и сервисам — это не гарантия прибыли.

⚡️ ВАЖНОЕ ОГРАНИЧЕНИЕ
Бот торгует только через ваш спотовый аккаунт Bybit и в пределах прав API, которые вы выдали. У бота нет прав на вывод средств и он не может переводить средства за пределы вашего аккаунта. Вы полностью отвечаете за безопасность ваших API-ключей и за торговую активность в своём аккаунте.

📊 ИСПОЛЬЗУЕМЫЕ ИНДИКАТОРЫ
OPEN INTEREST — показывает интерес покупателей/продавцов (информационный, не настраивается).
RSI — помогает определять перекупленность/перепроданность.
MACD — сигнализирует о смене тренда.
EMA — используется для отслеживания направления цены.
(Настройки индикаторов могут быть доступны для изменения там, где это указано — проверь панель бота.)

⚠️ РИСКИ И ЭФФЕКТИВНОСТЬ
Результат зависит от рыночных условий, настроек индикаторов и ваших параметров. Бот не гарантирует прибыль; результаты могут сильно варьироваться, и прошлые результаты не гарантируют будущих. Применяйте риск-менеджмент, торгуйте ответственно и только на те средства, которые можете позволить себе потерять.

📌 ПОДДЕРЖИВАЕМАЯ БИРЖА
На данный момент бот поддерживает только Bybit.

📚 ОСНОВНЫЕ КОМАНДЫ
/buy — создать счёт на подписку
SET KEY VALUE — изменить числовую или логическую настройку (пример: SET RSI_PERIOD 14)
SETKEY <api_key> <api_secret> — быстро установить API-ключи

🔐 БЕЗОПАСНОСТЬ И ОТВЕТСТВЕННОСТЬ
Храните API-ключи в секрете. По соображениям безопасности выдавайте только те права, которые действительно нужны боту (торговля, без прав на вывод).
Вы несёте полную ответственность за безопасность ключей, настройки аккаунта и принимаемые торговые решения. Бот — инструмент автоматизации и индикаторов, а не персональный финансовый советник.

📄 ЮРИДИЧЕСКИЕ И НАЛОГОВЫЕ МОМЕНТЫ
Использование бота может иметь юридические и налоговые последствия в вашей юрисдикции. Подписка или использование бота не создаёт инвестиционных отношений или фидуциарной ответственности. Для вопросов налогообложения и соответствия законам обратитесь к специалисту.
"""

    },
    "en": {
        "choose_lang": "Choose language / Выберите язык:",
        "welcome": "👋 Hi! This is JamesTrade.\nChoose an item from the menu:",
        "menu_subscription": "📊 Subscription",
        "menu_settings": "⚙️ Settings",
        "menu_trades": "💹 My trades",
        "menu_bot_on": "🤖 Bot: ON",
        "menu_bot_off": "🤖 Bot: OFF",
        "menu_support": "🆘 Support",
        "menu_info": "ℹ️ INFO",
        "enter_api_key": "Enter API Key (in the next message):",
        "enter_api_secret": "Now enter API Secret (in the next message):",
        "keys_saved_ok": "✅ API keys saved and validated successfully.",
        "keys_saved_warn": "❗️ Keys saved but validation failed: {info}\nCheck key permissions (read/balance/trade) and TESTNET flag.",
        "keys_saved_no_client": "Keys saved but server cannot validate keys (client.py missing).",
        "no_keys": "❌ You haven't added API keys. Add them in ⚙️ Settings -> API keys.",
        "invalid_keys": "❌ Invalid or insufficient API key permissions: {info}\nCheck keys and permissions (balance/trade).",
        "save_failed": "❌ Failed to save API keys. Try again later.",
        "subscribe_required": "🔒 You don't have an active subscription. Buy it in menu or /buy.",
        "trading_on": "▶️ Trading enabled.",
        "trading_off": "⏸️ Trading disabled.",
        "buy_success": "👉 Payment link: {url}\nAfter payment your subscription will be activated automatically.",
        "buy_fail": "❌ Failed to create invoice. Try later.",
        "invoice_paid": "✅ Payment received! Your subscription is activated for {days} days.",
        "settings_menu_title": "⚙️ Settings menu — choose section:",
        "settings_testnet_status": "🌐 TESTNET toggled {status}.",
        "settings_back": "⬅ Back",
        "settings_lang": "🌐 Language",
        "trade_mode_title": "Trade mode — current: {current}\nChoose mode:",
        "trade_mode_set_ok": "✅ Trade mode set: {mode}",
        "pairs_title": "Choose trading pairs (tap to toggle) or enter your own:",
        "pairs_input_prompt": "Enter pairs separated by comma or spaces (e.g. BTCUSDT, ETHUSDT):",
        "pairs_saved": "✅ Pairs saved: {pairs}",
        "pairs_saved_partial": "✅ Saved: {valid}. Invalid/unrecognized: {invalid}",
        "pairs_invalid_none": "❌ None of the entered pairs were recognized: {invalid}",
        "pairs_manual_saved": "✅ Your pairs saved: {pairs}",
        "pairs_manual_button": "✏️ Enter custom",
        "pairs_done_button": "✅ Done",
        "risk_title": "Current risk settings:\n{fields}\n\nChange with: SET KEY VALUE",
        "indicators_menu_title": "⚙️ Indicator settings — choose:",
        "indicators_global_title": "🌐 Global indicator toggles:\nPress button to toggle an indicator.",
        "indicators_advanced_text": "🔧 Advanced settings (current):\n\n{settings}\n\nTo change use:\nSET KEY VALUE\n\nExample: SET RSI_PERIOD 14",
        "support_prompt": "Describe the issue — your message will be sent to admin. To cancel, use /cancel",
        "support_sent": "✅ Message sent to support. Wait for reply.",
        "support_failed": "Failed to forward to support. Try later.",
        "no_trades": "💤 No trades yet.",
        "trades_end": "— End —",
        "trade_notification": "⚡️ Trade: {symbol} {side}\nPrice: {price}\nQty: {qty}\nPnL: {pnl}\n{ts}",
        "admin_only": "❌ Admin only.",
        "invalid_user_id": "❌ Invalid user id.",
        "enter_reply_prompt": "Enter reply to user {user_id}. To cancel: /cancel",
        "reply_sent": "✅ Reply sent to the user.",
        "action_cancelled": "Action cancelled.",
        "set_usage": "Error. Format: SET KEY VALUE",
        "error_data": "Bad data.",
        "welcome_short": "🤖 Commands: /buy — pay subscription; SET KEY VALUE — change setting.",
        # New strings for pair management and info
        "manage_pairs_title": "Your current pairs:\n{pairs}\n\nChoose action:",
        "add_pair_button": "➕ Add pair",
        "remove_pair_button": "➖ Remove pair",
        "no_pairs_yet": "You have no saved pairs yet.",
        "pair_removed": "✅ Pair {pair} removed.",
        "pair_added": "✅ Pair(s) added: {pairs}",
        "pair_not_found": "❌ Pair {pair} not found in your list.",
        "info_text": """
🤖 HOW THE BOT WORKS
This bot connects directly to Bybit using your API keys. When you add keys and enable the bot, it analyzes the market and executes trades on your Bybit account using funds in your Unified Trading Account. The bot cannot withdraw funds — withdrawals remain under the user’s control.

💎 WHY SUBSCRIBE?
A subscription gives access to the bot and funds ongoing development, maintenance and customer support. Subscribing purchases access to software and services — not a promise of returns.

⚡️ KEY LIMITATION
The bot only trades using your Bybit spot account and the API permissions you grant. It never has withdrawal permissions and cannot move funds outside your account. You are fully responsible for securing your API keys and for all trading activity performed under your account.

📊 INDICATORS USED
OPEN INTEREST — shows buyer/seller interest (informational, not configurable).
RSI — identifies overbought/oversold conditions.
MACD — signals trend changes.
EMA is used for price trend tracking.
(Indicator settings may be configurable where noted; check the bot panel for which fields you can change.)

⚠️ RISK & PERFORMANCE
Performance depends on market conditions, indicator settings and user configuration. The bot does not guarantee profits; results may vary and past performance is not indicative of future results. Use risk management, trade responsibly and only with funds you can afford to lose.

📌 SUPPORTED EXCHANGE
Currently the bot supports Bybit only.

📚 BASIC COMMANDS
/buy — create a subscription invoice
SET KEY VALUE — change a numeric or boolean setting (example: SET RSI_PERIOD 14)
SETKEY <api_key> <api_secret> — quickly set API keys

🔐 SECURITY & RESPONSIBILITY
Keep your API keys private. For safety, grant only the permissions the bot requires (trading, no withdrawals).
You remain fully responsible for API key security, account settings, and trading decisions.
The bot provides automation and indicators — it is a tool, not personalized financial advice.

📄 LEGAL / TAX
Using the bot may have legal and tax implications in your jurisdiction. Subscribing to or using the bot does not create an investment relationship or fiduciary duty. Consult a tax or legal advisor for guidance on reporting and compliance.
"""
    },
    "es": {
        "choose_lang": "Elige idioma / Choose language:",
        "welcome": "👋 ¡Hola! Esto es JamesTrade.",
        "menu_subscription": "📊 Suscripción",
        "menu_settings": "⚙️ Ajustes",
        "menu_trades": "💹 Mis operaciones",
        "menu_bot_on": "🤖 Bot: ON",
        "menu_bot_off": "🤖 Bot: OFF",
        "menu_support": "🆘 Soporte",
        "menu_info": "ℹ️ INFO",
        "enter_api_key": "Introduce API Key (en el siguiente mensaje):",
        "enter_api_secret": "Ahora introduce API Secret (en el siguiente mensaje):",
        "keys_saved_ok": "✅ Claves API guardadas y validadas con éxito.",
        "keys_saved_warn": "❗️ Claves guardadas, pero la validación falló: {info}\nVerifica permisos (read/balance/trade) y TESTNET.",
        "keys_saved_no_client": "Claves guardadas, pero el servidor no puede validar (client.py ausente).",
        "no_keys": "❌ No has añadido claves API. Añádelas en ⚙️ Ajustes -> API keys.",
        "invalid_keys": "❌ Claves inválidas o permisos insuficientes: {info}\nVerifica las claves y permisos (balance/trade).",
        "save_failed": "❌ No se pudieron guardar las claves. Intenta más tarde.",
        "subscribe_required": "🔒 No tienes una suscripción activa. Cómprala en el menú o /buy.",
        "trading_on": "▶️ Trading activado.",
        "trading_off": "⏸️ Trading desactivado.",
        "buy_success": "👉 Enlace de pago: {url}\nTras el pago, la suscripción se activará automáticamente.",
        "buy_fail": "❌ No se pudo crear la factura. Intenta más tarde.",
        "invoice_paid": "✅ ¡Pago recibido! Tu suscripción está activada por {days} días.",
        "settings_menu_title": "⚙️ Menú de ajustes — elige sección:",
        "settings_testnet_status": "🌐 TESTNET cambiado a {status}.",
        "settings_back": "⬅ Volver",
        "settings_lang": "🌐 Idioma",
        "trade_mode_title": "Modo de trading — actual: {current}\nElige modo:",
        "trade_mode_set_ok": "✅ Modo de trading establecido: {mode}",
        "pairs_title": "Elige pares de trading (toca para alternar) o introduce los tuyos:",
        "pairs_input_prompt": "Introduce pares separados por comas o espacios (p. ej.: BTCUSDT, ETHUSDT):",
        "pairs_saved": "✅ Pares guardados: {pairs}",
        "pairs_saved_partial": "✅ Guardados: {valid}. No reconocidos/invalidos: {invalid}",
        "pairs_invalid_none": "❌ Ninguno de los pares introducidos fue reconocido como válido: {invalid}",
        "pairs_manual_saved": "✅ Tus pares guardados: {pairs}",
        "pairs_manual_button": "✏️ Introducir propios",
        "pairs_done_button": "✅ Hecho",
        "risk_title": "Ajustes de riesgo actuales:\n{fields}\n\nCambiar: SET KEY VALUE",
        "indicators_menu_title": "⚙️ Ajustes de indicadores — elige:",
        "indicators_global_title": "🌐 Indicadores globales:\nPulsa para alternar un indicador.",
        "indicators_advanced_text": "🔧 Ajustes avanzados (actuales):\n\n{settings}\n\nPara cambiar usa:\nSET KEY VALUE\n\nEjemplo: SET RSI_PERIOD 14",
        "support_prompt": "Describe el problema — tu mensaje se enviará al administrador. Para cancelar usa /cancel",
        "support_sent": "✅ Mensaje enviado al soporte. Espera respuesta.",
        "support_failed": "Error al enviar al soporte. Intenta más tarde.",
        "no_trades": "💤 Aún no hay operaciones.",
        "trades_end": "— Fin —",
        "trade_notification": "⚡️ Operación: {symbol} {side}\nPrecio: {price}\nCantidad: {qty}\nPnL: {pnl}\n{ts}",
        "admin_only": "❌ Solo administrador.",
        "invalid_user_id": "❌ Id de usuario inválido.",
        "enter_reply_prompt": "Introduce la respuesta al usuario {user_id}. Para cancelar: /cancel",
        "reply_sent": "✅ Respuesta enviada al usuario.",
        "action_cancelled": "Acción cancelada.",
        "set_usage": "Error. Formato: SET KEY VALUE",
        "error_data": "Datos erróneos.",
        "welcome_short": "🤖 Comandos: /buy — pagar suscripción; SET KEY VALUE — cambiar ajuste.",
        # New strings
        "manage_pairs_title": "Tus pares actuales:\n{pairs}\n\nElige acción:",
        "add_pair_button": "➕ Añadir par",
        "remove_pair_button": "➖ Eliminar par",
        "no_pairs_yet": "Todavía no tienes pares guardados.",
        "pair_removed": "✅ Par {pair} eliminado.",
        "pair_added": "✅ Par(es) añadidos: {pairs}",
        "pair_not_found": "❌ Par {pair} no encontrado en tu lista.",
       "info_text": """
🤖 CÓMO FUNCIONA EL BOT
Este bot se conecta directamente a Bybit usando tus claves API. Cuando añades las claves y activas el bot, éste analiza el mercado y ejecuta operaciones en tu cuenta de Bybit usando los fondos de tu Unified Trading Account. El bot no puede retirar fondos: las retiradas quedan bajo el control del usuario.

💎 ¿POR QUÉ SUSCRIBIRSE?
La suscripción da acceso al bot y financia el desarrollo continuo, el mantenimiento y el soporte. Suscribirse otorga acceso al software y a los servicios — no es una promesa de beneficios.

⚡️ LIMITACIÓN PRINCIPAL
El bot opera únicamente con tu cuenta spot de Bybit y con los permisos API que concedas. Nunca tiene permisos de retirada y no puede mover fondos fuera de tu cuenta. Eres totalmente responsable de la seguridad de tus claves API y de toda la actividad de trading realizada en tu cuenta.

📊 INDICADORES UTILIZADOS
OPEN INTEREST — muestra el interés de compradores/vendedores (informativo, no configurable).
RSI — identifica condiciones de sobrecompra/sobreventa.
MACD — indica cambios de tendencia.
EMA — se utiliza para seguir la dirección del precio.
(Las configuraciones de los indicadores pueden ser editables donde se indique; revisa el panel del bot para ver qué campos son modificables.)

⚠️ RIESGOS Y RENDIMIENTO
El rendimiento depende de las condiciones de mercado, las configuraciones de indicadores y la configuración del usuario. El bot no garantiza beneficios; los resultados pueden variar y el rendimiento pasado no asegura resultados futuros. Usa gestión de riesgos, opera responsablemente y sólo con fondos que puedas permitirte perder.

📌 EXCHANGE SOPORTADO
Actualmente el bot soporta únicamente Bybit.

📚 COMANDOS BÁSICOS
/buy — crear una factura de suscripción
SET KEY VALUE — cambiar una configuración numérica o booleana (ejemplo: SET RSI_PERIOD 14)
SETKEY <api_key> <api_secret> — establecer rápidamente las claves API

🔐 SEGURIDAD Y RESPONSABILIDAD
Mantén tus claves API privadas. Por seguridad, concede sólo los permisos que el bot necesite (trading, sin permisos de retirada).
Eres responsable de la seguridad de tus claves, la configuración de tu cuenta y las decisiones de trading. El bot proporciona automatización e indicadores — es una herramienta, no asesoramiento financiero personalizado.

📄 ASPECTOS LEGALES / FISCALES
El uso del bot puede tener implicaciones legales y fiscales en tu jurisdicción. Suscribirse o usar el bot no crea una relación de inversión ni una obligación fiduciaria. Consulta a un asesor legal o fiscal sobre cumplimiento y obligaciones de reporte.
"""

    },
}

# FSM
class Form(StatesGroup):
    api_key = State()
    api_secret = State()
    support_user = State()
    admin_reply = State()
    pairs_input = State()

# Encryption helpers (no-op to store plain keys)
def encrypt(data: str) -> str:
    return data

def decrypt(data: str) -> str:
    return data

# Localization helper (improved with fallbacks and humanized key fallback)
def t(uid: Optional[int], key: str, **kwargs) -> str:
    """Return localized string with multi-level fallback."""
    lang = "ru"
    try:
        if uid is not None:
            u = db.get_user(uid) or {}
            settings = u.get("settings") or {}
            lang = settings.get("lang") or settings.get("language") or "ru"
            if lang not in LOCALE:
                lang = "ru"
    except Exception:
        lang = "ru"

    s = None
    try:
        s = LOCALE.get(lang, {}).get(key)
    except Exception:
        s = None
    if s is None:
        s = LOCALE.get("ru", {}).get(key)
    if s is None:
        s = LOCALE.get("en", {}).get(key)
    if s is None:
        human = key.replace("_", " ").strip().capitalize()
        s = human

    if kwargs:
        try:
            return s.format(**kwargs)
        except Exception:
            return s
    return s

def normalize_symbol(sym: str) -> str:
    if not sym:
        return ""
    s = sym.strip().upper()
    s = re.sub(r'[^A-Z0-9]', '', s)
    return s

def validate_symbols(uid: int, symbols: List[str]) -> Tuple[List[str], List[str]]:
    valid = []
    invalid = []
    u = db.get_user(uid) or {}
    settings = u.get("settings") or {}
    testnet = bool(settings.get("TESTNET", False) or settings.get("testnet", False))

    client = None
    if client_module is not None:
        try:
            client = client_module.BybitClient(api_key=None, api_secret=None, testnet=testnet)
        except Exception:
            client = None

    base_public = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"

    for s in symbols:
        ns = normalize_symbol(s)
        if not ns:
            continue
        ok = False
        try:
            if client is not None and hasattr(client, "get_symbol_info"):
                try:
                    info = client.get_symbol_info(ns)
                    if info and isinstance(info, dict) and info:
                        ok = True
                except Exception:
                    pass
            if not ok and client is not None and hasattr(client, "fetch_ohlcv_df"):
                try:
                    df = client.fetch_ohlcv_df(ns, interval="5", limit=1)
                    if hasattr(df, "empty"):
                        if not df.empty:
                            ok = True
                    else:
                        if df:
                            ok = True
                except Exception:
                    pass
            if not ok:
                try:
                    params = {"category": getattr(client, "category", "linear"), "symbol": ns}
                    url = base_public + "/v5/market/instruments-info"
                    r = session.get(url, params=params, timeout=6)
                    j = r.json() if r is not None else {}
                    items = None
                    if isinstance(j, dict):
                        res = j.get("result") or j
                        if isinstance(res, dict):
                            items = res.get("list") or []
                        elif isinstance(res, list):
                            items = res
                    if items:
                        for it in items:
                            if isinstance(it, dict) and (it.get("symbol") == ns or it.get("name") == ns):
                                ok = True
                                break
                except Exception:
                    pass
        except Exception:
            pass

        if ok:
            valid.append(ns)
        else:
            invalid.append(ns)
    def uniq(seq):
        seen = set()
        out = []
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out
    return uniq(valid), uniq(invalid)

def validate_user_keys(uid: int) -> Tuple[bool, str]:
    try:
        u = db.get_user(uid)
        if not u:
            return False, "User not found"
        api_key_enc = u.get("api_key") or ""
        api_secret_enc = u.get("api_secret") or ""
        if not api_key_enc or not api_secret_enc:
            return False, "missing_keys"
        api_key = decrypt(api_key_enc)
        api_secret = decrypt(api_secret_enc)
        settings = u.get("settings") or {}
        testnet = bool(settings.get("TESTNET", False) or settings.get("testnet", False))

        if client_module is None:
            return False, "no_client"

        try:
            client = client_module.BybitClient(api_key=api_key, api_secret=api_secret, testnet=testnet)
        except Exception as e:
            logger.exception("Failed to create BybitClient for validation")
            return False, f"client_init_error: {e}"

        try:
            if hasattr(client, "get_balance_usdt"):
                bal = client.get_balance_usdt()
                if bal is None:
                    return False, "auth_or_rights"
                return True, "ok_balance"
            if hasattr(client, "get_account_info"):
                info = client.get_account_info()
                if info is None:
                    return False, "auth_or_rights"
                return True, "ok_account"
        except Exception as e:
            msg = str(e).lower()
            logger.exception("Key validation exception for user %s: %s", uid, e)
            if "401" in msg or "unauthorized" in msg or "invalid" in msg:
                return False, "auth_or_rights"
            return False, f"exception: {e}"
        return False, "no_validation_method"
    except Exception as e:
        logger.exception("validate_user_keys generic error")
        return False, f"internal_error: {e}"

def has_active_sub(user_id: int) -> bool:
    u = db.get_user(user_id)
    if not u:
        return False
    sub_until = u.get("sub_until")
    if not sub_until:
        return False
    try:
        if isinstance(sub_until, str) and sub_until.lower() == "forever":
            return True
        dt = datetime.fromisoformat(str(sub_until))
        return dt > datetime.utcnow()
    except Exception:
        return False

def is_trading_active(user_id: int) -> bool:
    u = db.get_user(user_id)
    if not u:
        return False
    settings = u.get("settings", {}) or {}
    return bool(settings.get("active"))

def main_reply_kb(user_id: Optional[int] = None, resize: bool = True) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text=t(user_id, "menu_subscription"))
    builder.button(text=t(user_id, "menu_settings"))
    builder.button(text=t(user_id, "menu_trades"))
    bot_label = t(user_id, "menu_bot_off")
    if user_id is not None and is_trading_active(user_id):
        bot_label = t(user_id, "menu_bot_on")
    builder.button(text=bot_label)
    builder.button(text=t(user_id, "menu_support"))
    builder.button(text=t(user_id, "menu_info"))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=resize)

def admin_reply_kb_for_user(user_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Ответить", callback_data=f"admin_reply:{user_id}")
    kb.adjust(1)
    return kb.as_markup()

# --- helpers for trade mode normalization ---
def _read_trade_mode(settings: dict) -> str:
    if not settings:
        return "mixed"
    val = settings.get("TRADE_MODE") or settings.get("trade_mode") or settings.get("Trade_Mode") or ""
    if not val:
        return "mixed"
    v = str(val).strip().lower()
    if v in ("futures", "futures_only", "futures-only", "futuresonly"):
        return "futures_only"
    if v in ("spot", "spot_only", "spot-only", "spotonly"):
        return "spot_only"
    return "mixed"

def _friendly_mode_label(mode: str) -> str:
    if mode == "futures_only":
        return "Futures Only"
    if mode == "spot_only":
        return "Spot Only"
    return "Mixed"

# ---------- Handlers ----------

@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    db.create_default_user(m.from_user.id, m.from_user.username)
    u = db.get_user(m.from_user.id) or {}
    s = (u.get("settings") or {})
    lang = s.get("lang") or s.get("language")
    if not lang:
        kb = InlineKeyboardBuilder()
        kb.button(text="🇷🇺 Русский", callback_data="lang:ru")
        kb.button(text="🇬🇧 English", callback_data="lang:en")
        kb.button(text="🇪🇸 Español", callback_data="lang:es")
        kb.adjust(3)
        await m.answer(LOCALE["ru"]["choose_lang"], reply_markup=kb.as_markup())
        return
    await m.answer(t(m.from_user.id, "welcome"), reply_markup=main_reply_kb(m.from_user.id))

@dp.callback_query(lambda c: c.data and c.data.startswith("lang:"))
async def cb_lang_set(c: types.CallbackQuery):
    await c.answer()
    try:
        _, lang = c.data.split(":", 1)
    except Exception:
        lang = "ru"
    db.create_default_user(c.from_user.id, c.from_user.username)
    db.update_setting(c.from_user.id, "lang", lang)
    try:
        await c.message.delete()
    except Exception:
        pass
    await c.message.answer(t(c.from_user.id, "welcome"), reply_markup=main_reply_kb(c.from_user.id))

# API keys flow
@dp.callback_query(lambda c: c.data == "settings_api")
async def cb_settings_api(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    await c.message.answer(t(c.from_user.id, "enter_api_key"), reply_markup=main_reply_kb(c.from_user.id))
    await state.set_state(Form.api_key)

@dp.message(Form.api_key)
async def process_api_key(m: types.Message, state: FSMContext):
    await state.update_data(api_key=m.text.strip())
    await m.answer(t(m.from_user.id, "enter_api_secret"), reply_markup=main_reply_kb(m.from_user.id))
    await state.set_state(Form.api_secret)

@dp.message(Form.api_secret)
async def process_api_secret(m: types.Message, state: FSMContext):
    data = await state.get_data()
    key_plain = data.get("api_key", "").strip()
    secret_plain = m.text.strip()
    try:
        enc_key = encrypt(key_plain)
        enc_secret = encrypt(secret_plain)
        db.set_api_keys(m.from_user.id, enc_key, enc_secret)
    except Exception:
        logger.exception("Failed to save api keys to DB")
        await m.answer(t(m.from_user.id, "save_failed"), reply_markup=main_reply_kb(m.from_user.id))
        await state.clear()
        return

    ok, info = validate_user_keys(m.from_user.id)
    if ok:
        await m.answer(t(m.from_user.id, "keys_saved_ok"), reply_markup=main_reply_kb(m.from_user.id))
    else:
        if info == "no_client":
            await m.answer(t(m.from_user.id, "keys_saved_no_client"), reply_markup=main_reply_kb(m.from_user.id))
        elif info == "missing_keys":
            await m.answer(t(m.from_user.id, "no_keys"), reply_markup=main_reply_kb(m.from_user.id))
        elif info == "auth_or_rights":
            await m.answer(t(m.from_user.id, "keys_saved_warn", info="401/unauthorized or insufficient rights"), reply_markup=main_reply_kb(m.from_user.id))
        else:
            await m.answer(t(m.from_user.id, "keys_saved_warn", info=str(info)), reply_markup=main_reply_kb(m.from_user.id))
        notify_admin_rate_limited_sync(f"User {m.from_user.id} saved API keys but validation failed: {info}", key="user_key_invalid")
    await state.clear()

# Toggle trading via keyboard button
@dp.message(lambda m: (m.text and (m.text.startswith("🤖 Бот:") or m.text.startswith("🤖 Bot:"))))
async def toggle_bot_via_button(m: types.Message):
    uid = m.from_user.id
    db.create_default_user(uid)
    current = is_trading_active(uid)
    if not current:
        if not has_active_sub(uid):
            await m.reply(t(uid, "subscribe_required"), reply_markup=main_reply_kb(uid))
            return
        u = db.get_user(uid) or {}
        api_key_enc = u.get("api_key") or ""
        api_secret_enc = u.get("api_secret") or ""
        if not api_key_enc or not api_secret_enc:
            await m.reply(t(uid, "no_keys"), reply_markup=main_reply_kb(uid))
            return
        ok, info = validate_user_keys(uid)
        if not ok:
            if info == "no_client":
                await m.reply(t(uid, "keys_saved_no_client"), reply_markup=main_reply_kb(uid))
            elif info == "auth_or_rights":
                await m.reply(t(uid, "invalid_keys", info="401/unauthorized"), reply_markup=main_reply_kb(uid))
            else:
                await m.reply(t(uid, "invalid_keys", info=str(info)), reply_markup=main_reply_kb(uid))
            db.update_setting(uid, "active", False)
            notify_admin_rate_limited_sync(f"User {uid} tried to enable trading but key validation failed: {info}", key="user_enable_fail")
            return
        db.update_setting(uid, "active", True)
        await m.reply(t(uid, "trading_on"), reply_markup=main_reply_kb(uid))
    else:
        db.update_setting(uid, "active", False)
        await m.reply(t(uid, "trading_off"), reply_markup=main_reply_kb(uid))

# ---------- Subscription menu + handlers----------

# ---------- /buy flows (замена) ----------
# Помести в верх файла (или импортируй)
import os
import logging
import aiohttp
from typing import Tuple, Optional

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

logger = logging.getLogger(__name__)

# Настройки (подставь/оставь как у тебя)
CRYPTOPAY_API_URL = os.getenv("CRYPTOPAY_API_URL", "https://pay.crypt.bot/api/createInvoice")
CRYPTOPAY_TOKEN = os.getenv("CRYPTOPAY_TOKEN")
# PAYMENT_AMOUNT, ADMIN_ID, db, dp, bot, t, LOCALE, main_reply_kb должны быть в модуле уже

# ------------------ helper: create_invoice ------------------
async def create_invoice(uid: int, amount: float, asset: str = "USDT", description: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Создаёт инвойс в Crypto Pay и возвращает (public_pay_url, invoice_id_or_hash).
    Берёт в приоритетe: bot_invoice_url -> web_app_invoice_url -> mini_app_invoice_url -> pay_url
    """
    if not CRYPTOPAY_TOKEN:
        logger.error("CRYPTOPAY_TOKEN not set in environment")
        raise RuntimeError("CRYPTOPAY_TOKEN not configured")

    payload = {
        "amount": str(amount),
        "asset": asset,
        # payload связывает инвойс с пользователем — удобно проверять позже
        "payload": f"user:{uid}",
    }
    if description:
        payload["description"] = description

    headers = {
        "Crypto-Pay-API-Token": CRYPTOPAY_TOKEN,
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(CRYPTOPAY_API_URL, json=payload, headers=headers, timeout=15) as resp:
                try:
                    data = await resp.json()
                except Exception:
                    text = await resp.text()
                    logger.exception("Failed to parse JSON from CryptoPay response: %s", text)
                    raise
    except Exception:
        logger.exception("CryptoPay createInvoice request failed")
        raise

    # Ожидаем структуру { ok: True, result: {...} } — defensive checks
    if not data.get("ok"):
        logger.error("CryptoPay returned error: %s", data)
        raise RuntimeError(f"CryptoPay createInvoice failed: {data.get('error') or data}")

    inv = data.get("result", {})

    # Смотрим первыми доступные публичные ссылки
    pay_url = inv.get("bot_invoice_url") or inv.get("web_app_invoice_url") or inv.get("mini_app_invoice_url") or inv.get("pay_url")
    invoice_id = inv.get("invoice_id") or inv.get("hash") or inv.get("id") or str(inv.get("invoice_id", ""))

    # Если библиотека вернула внутренний путь pay.crypt.bot/invoice/<id>, попытаться взять web_app/bot версии
    if pay_url and "pay.crypt.bot/invoice/" in str(pay_url):
        alt = inv.get("bot_invoice_url") or inv.get("web_app_invoice_url") or inv.get("mini_app_invoice_url")
        if alt:
            logger.warning("createInvoice returned internal pay.crypt.bot link; prefer web/bot url instead")
            pay_url = alt

    # Для отладки — отправим админу сырой inv (опционально)
    try:
        if ADMIN_ID:
            await bot.send_message(ADMIN_ID, f"DEBUG CryptoPay invoice result for user {uid}:\n`{inv}`", parse_mode="Markdown")
    except Exception:
        # Не критично, только лог
        logger.debug("Could not send DEBUG invoice to admin")

    return pay_url, invoice_id

# ------------------ handlers ------------------

@dp.message(lambda m: m.text == t(m.from_user.id, "menu_subscription"))
async def menu_subscription(m: types.Message):
    uid = m.from_user.id

    # use db helper has_used_trial if available, fallback to settings flag
    try:
        used_trial = db.has_used_trial(uid) if hasattr(db, "has_used_trial") else bool((db.get_user(uid) or {}).get("settings", {}).get("used_trial", False))
    except Exception:
        used_trial = False

    kb = InlineKeyboardBuilder()
    # show Free trial only if not used
    if not used_trial:
        kb.button(text="Free trial (30 days)", callback_data="buy_choice:trial")
    kb.button(text="30 days", callback_data="buy_choice:30")
    kb.button(text="365 days", callback_data="buy_choice:365")
    kb.button(text="Forever", callback_data="buy_choice:forever")
    kb.adjust(1)

    prompt = t(uid, "choose_subscription") if "choose_subscription" in LOCALE.get("ru", {}) else "Choose subscription duration:"
    await m.reply(prompt, reply_markup=kb.as_markup())


@dp.callback_query(lambda c: c.data and c.data.startswith("buy_choice:"))
async def cb_buy_choice(c: types.CallbackQuery):
    await c.answer()  # acknowledge callback
    try:
        _, choice = c.data.split(":", 1)
    except Exception:
        try:
            await c.message.answer("Invalid choice.")
        except Exception:
            pass
        return

    uid = c.from_user.id

    # ---------- Free trial handling ----------
    if choice == "trial":
        # race-safety: re-check via db.has_used_trial if exists
        try:
            already = db.has_used_trial(uid) if hasattr(db, "has_used_trial") else bool((db.get_user(uid) or {}).get("settings", {}).get("used_trial", False))
        except Exception:
            already = False

        if already:
            try:
                await c.message.answer(t(uid, "buy_fail") if "buy_fail" in LOCALE.get("ru", {}) else "You already used the free trial.", reply_markup=main_reply_kb(uid))
            except Exception:
                await c.message.answer("You already used the free trial.", reply_markup=main_reply_kb(uid))
            return

        # grant 30-day subscription and mark trial used
        try:
            db.set_subscription(uid, days=30)
        except Exception:
            logger.exception("Failed to set trial subscription for user %s", uid)

        # mark trial used (use provided helper if exists)
        try:
            if hasattr(db, "set_used_trial"):
                db.set_used_trial(uid, True)
            else:
                # fallback: store in settings
                db.update_setting(uid, "used_trial", True)
        except Exception:
            logger.exception("Failed to mark trial used for user %s", uid)

        # remove the menu message to keep chat clean (best-effort)
        try:
            await c.message.delete()
        except Exception:
            pass

        # send confirmation
        try:
            await c.message.answer(t(uid, "invoice_paid", days=30), reply_markup=main_reply_kb(uid))
        except Exception:
            await c.message.answer("✅ Free trial activated for 30 days.", reply_markup=main_reply_kb(uid))

        # notify admin
        if ADMIN_ID:
            try:
                await bot.send_message(ADMIN_ID, f"Free trial granted to @{c.from_user.username} (id={uid}).")
            except Exception:
                logger.exception("Failed to notify admin about trial")
        return
    # ---------- End trial ----------

    # ---------- Paid choices ----------
    try:
        prices = {
            "30": float(os.getenv("PAYMENT_AMOUNT_30", PAYMENT_AMOUNT)),
            "365": float(os.getenv("PAYMENT_AMOUNT_365", PAYMENT_AMOUNT * 10)),
            "forever": float(os.getenv("PAYMENT_AMOUNT_FOREVER", PAYMENT_AMOUNT * 50)),
        }
    except Exception:
        prices = {"30": PAYMENT_AMOUNT, "365": PAYMENT_AMOUNT * 10, "forever": PAYMENT_AMOUNT * 50}

    if choice not in ("30", "365", "forever"):
        try:
            await c.message.answer("Unknown option.")
        except Exception:
            pass
        return

    amount = prices.get(choice, PAYMENT_AMOUNT)

    # create invoice (async)
    try:
        pay_url, invoice_id = await create_invoice(uid, amount=amount, asset="USDT", description=f"Subscription {choice} days for {uid}")
    except Exception:
        logger.exception("create_invoice failed")
        pay_url, invoice_id = None, None

    if pay_url:
        # store invoice metadata for later checking
        try:
            db.update_setting(uid, "last_invoice_id", invoice_id)
            db.update_setting(uid, "last_invoice_choice", choice)
        except Exception:
            logger.exception("Failed to save invoice meta to DB")

        # try to delete the menu message to keep chat clean (best-effort)
        try:
            await c.message.delete()
        except Exception:
            pass

        # send invoice text + inline "Pay" button
        try:
            kb = InlineKeyboardBuilder()
            kb.button(text="Pay", url=pay_url)
            kb.adjust(1)
            try:
                text = t(uid, "buy_success", url=pay_url)
            except Exception:
                text = f"Please pay: {pay_url}"
            await c.message.answer(text, reply_markup=kb.as_markup())
        except Exception:
            # fallback: plain text with URL
            try:
                await c.message.answer(pay_url)
            except Exception:
                logger.exception("Failed to send pay link to user")
        # notify admin
        if ADMIN_ID:
            try:
                await bot.send_message(ADMIN_ID, f"User @{c.from_user.username} (id={uid}) created invoice {invoice_id} for {choice} days. URL: {pay_url}")
            except Exception:
                logger.exception("Failed to notify admin about invoice")
    else:
        try:
            await c.message.answer(t(uid, "buy_fail"), reply_markup=main_reply_kb(uid))
        except Exception:
            await c.message.answer("❌ Failed to create invoice. Try later.", reply_markup=main_reply_kb(uid))


@dp.message(Command("buy"))
async def cmd_buy(m: types.Message):
    await menu_subscription(m)

# Settings menu
@dp.message(lambda m: m.text == t(m.from_user.id, "menu_settings"))
async def menu_settings_main(m: types.Message):
    if not has_active_sub(m.from_user.id):
        await m.reply(t(m.from_user.id, "subscribe_required"), reply_markup=main_reply_kb(m.from_user.id))
        return
    db.create_default_user(m.from_user.id, m.from_user.username)
    kb = InlineKeyboardBuilder()
    kb.button(text="🔑 API keys", callback_data="settings_api")
    kb.button(text="🌐 TESTNET (ON/OFF)", callback_data="settings_testnet")
    kb.button(text="💱Pairs", callback_data="settings_pairs")
    kb.button(text="💰Risk management", callback_data="settings_risk")
    kb.button(text="📊Indicators", callback_data="settings_indicators")
    kb.button(text="🛠 Trade Modes", callback_data="settings_trade_mode")
    kb.button(text=t(m.from_user.id, "settings_lang"), callback_data="settings_lang")
    kb.button(text=t(m.from_user.id, "settings_back"), callback_data="settings_back")
    kb.adjust(1)
    await m.reply(t(m.from_user.id, "settings_menu_title"), reply_markup=kb.as_markup())

@dp.callback_query(lambda c: c.data == "settings_testnet")
async def cb_settings_testnet(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    db.create_default_user(uid, c.from_user.username)
    user = db.get_user(uid) or {}
    settings = user.get("settings", {}) or {}
    cur = bool(settings.get("TESTNET", False) or settings.get("testnet", False))
    new = not cur
    db.update_setting(uid, "TESTNET", new)
    status = "ON" if new else "OFF"
    await c.message.answer(t(uid, "settings_testnet_status", status=status), reply_markup=main_reply_kb(uid))

@dp.callback_query(lambda c: c.data == "settings_lang")
async def cb_settings_lang(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    kb = InlineKeyboardBuilder()
    kb.button(text="🇷🇺 Русский", callback_data="lang:ru")
    kb.button(text="🇬🇧 English", callback_data="lang:en")
    kb.button(text="🇪🇸 Español", callback_data="lang:es")
    kb.adjust(3)
    await c.message.answer(t(uid, "choose_lang"), reply_markup=kb.as_markup())

# ---- CHANGED: settings_pairs now shows a readable list + Add/Remove buttons (like your second screenshot) ----
@dp.callback_query(lambda c: c.data == "settings_pairs")
async def cb_settings_pairs(c: types.CallbackQuery):
    """
    Show user's current pairs as a list and provide 'Add pair' / 'Remove pair' buttons.
    This replaces the previous grid of checkboxes with a clearer list view.
    """
    await c.answer()
    uid = c.from_user.id
    db.create_default_user(uid, c.from_user.username)
    user = db.get_user(uid) or {}
    settings = user.get("settings", {}) or {}
    symbols = settings.get("symbols") or settings.get("SYMBOLS") or SYMBOLS_ENV

    # normalize and preserve order / uniqueness
    symbols = [normalize_symbol(x) for x in symbols if x and normalize_symbol(x)]
    seen = set()
    symbols = [s for s in symbols if not (s in seen or seen.add(s))]

    pairs_str = "\n".join(f"- {s}" for s in symbols) if symbols else t(uid, "no_pairs_yet")

    txt = t(uid, "manage_pairs_title", pairs=pairs_str)
    kb = InlineKeyboardBuilder()
    kb.button(text=t(uid, "add_pair_button"), callback_data="trades_add_pair")
    kb.button(text=t(uid, "remove_pair_button"), callback_data="trades_remove_pair")
    kb.button(text=t(uid, "settings_back"), callback_data="settings_back")   
    kb.adjust(1)
    try:
        await c.message.edit_text(txt, reply_markup=kb.as_markup())
    except Exception:
        await c.message.answer(txt, reply_markup=kb.as_markup())

@dp.callback_query(lambda c: c.data == "trades_add_pair")
async def cb_trades_add_pair(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    uid = c.from_user.id
    try:
        await c.message.delete()  # remove menu for cleanliness
    except Exception:
        pass
    await c.message.answer(t(uid, "pairs_input_prompt"), reply_markup=main_reply_kb(uid))
    await state.update_data(pairs_origin="trades_add")
    await state.set_state(Form.pairs_input)

@dp.callback_query(lambda c: c.data == "trades_remove_pair")
async def cb_trades_remove_pair(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    db.create_default_user(uid, c.from_user.username)
    user = db.get_user(uid) or {}
    symbols = user.get("settings", {}).get("symbols") or user.get("settings", {}).get("SYMBOLS") or SYMBOLS_ENV
    symbols = [normalize_symbol(x) for x in symbols if x and normalize_symbol(x)]
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        try:
            await c.message.delete()
        except Exception:
            pass
        await c.message.answer(t(uid, "no_pairs_yet"), reply_markup=main_reply_kb(uid))
        return

    kb = InlineKeyboardBuilder()
    for sym in symbols:
        kb.button(text=f"🗑 {sym}", callback_data=f"trades_remove_sym:{sym}")
    kb.button(text=t(uid, "pairs_done_button"), callback_data="pairs_done")
    kb.adjust(2)
    try:
        await c.message.edit_text(t(uid, "pairs_title"), reply_markup=kb.as_markup())
    except Exception:
        await c.message.answer(t(uid, "pairs_title"), reply_markup=kb.as_markup())

# Pairs selection (legacy grid kept, not removed — still usable via direct callback if needed)
@dp.callback_query(lambda c: c.data and c.data.startswith("pairs_toggle:"))
async def cb_pairs_toggle(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    try:
        _, sym = c.data.split(":", 1)
        sym = sym.upper()
    except Exception:
        await c.answer(t(uid, "error_data"))
        return
    db.create_default_user(uid, c.from_user.username)
    user = db.get_user(uid) or {}
    s = set([p.upper() for p in (user.get("settings", {}) or {}).get("symbols", user.get("settings", {}).get("SYMBOLS", SYMBOLS_ENV))])
    if sym in s:
        s.remove(sym)
    else:
        s.add(sym)
    db.update_setting(uid, "symbols", list(s))
    # refresh previous pairs menu if applicable
    try:
        await cb_settings_pairs(c)
    except Exception:
        pass

@dp.callback_query(lambda c: c.data == "pairs_done")
async def cb_pairs_done(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    user = db.get_user(uid) or {}
    symbols = user.get("settings", {}).get("symbols") or user.get("settings", {}).get("SYMBOLS") or SYMBOLS_ENV
    await c.message.answer(t(uid, "pairs_saved", pairs=",".join(symbols)), reply_markup=main_reply_kb(uid))

@dp.callback_query(lambda c: c.data == "pairs_input")
async def cb_pairs_input(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    uid = c.from_user.id
    try:
        await c.message.delete()
    except Exception:
        pass
    await c.message.answer(t(uid, "pairs_input_prompt"), reply_markup=main_reply_kb(uid))
    # clear any origin and set pairs_input
    await state.update_data(pairs_origin=None)
    await state.set_state(Form.pairs_input)

@dp.message(Form.pairs_input)
async def process_pairs_input(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    raw = m.text or ""
    data = await state.get_data()
    origin = data.get("pairs_origin")  # None or "trades_add"

    parts = re.split(r'[,;\n]+', raw)
    tokens = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if ("," not in raw and ";" not in raw and "\n" not in raw) and " " in p:
            tokens.extend([x.strip() for x in p.split() if x.strip()])
        else:
            tokens.append(p)
    tokens = [normalize_symbol(x) for x in tokens if x and normalize_symbol(x)]
    if not tokens:
        await m.reply(t(uid, "pairs_invalid_none", invalid=raw), reply_markup=main_reply_kb(uid))
        await state.clear()
        return

    valid, invalid = validate_symbols(uid, tokens)
    if not valid:
        await m.reply(t(uid, "pairs_invalid_none", invalid=",".join(invalid)), reply_markup=main_reply_kb(uid))
        await state.clear()
        return

    if origin == "trades_add":
        user = db.get_user(uid) or {}
        cur = [normalize_symbol(x) for x in (user.get("settings", {}) or {}).get("symbols", []) if x]
        merged = cur + [v for v in valid if v not in cur]
        db.update_setting(uid, "symbols", merged)
        await m.reply(t(uid, "pair_added", pairs=",".join(valid)), reply_markup=main_reply_kb(uid))
    else:
        db.update_setting(uid, "symbols", valid)
        if invalid:
            await m.reply(t(uid, "pairs_saved_partial", valid=",".join(valid), invalid=",".join(invalid)), reply_markup=main_reply_kb(uid))
        else:
            await m.reply(t(uid, "pairs_manual_saved", pairs=",".join(valid)), reply_markup=main_reply_kb(uid))
    await state.clear()

@dp.callback_query(lambda c: c.data and c.data.startswith("trades_remove_sym:"))
async def cb_trades_remove_sym(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    try:
        _, sym = c.data.split(":", 1)
        sym = normalize_symbol(sym)
    except Exception:
        await c.message.answer(t(uid, "error_data"))
        return
    db.create_default_user(uid, c.from_user.username)
    user = db.get_user(uid) or {}
    s = [normalize_symbol(x) for x in (user.get("settings", {}) or {}).get("symbols", []) if x]
    if sym not in s:
        await c.message.answer(t(uid, "pair_not_found", pair=sym), reply_markup=main_reply_kb(uid))
        return
    s = [x for x in s if x != sym]
    db.update_setting(uid, "symbols", s)
    try:
        await c.message.delete()
    except Exception:
        pass
    await c.message.answer(t(uid, "pair_removed", pair=sym), reply_markup=main_reply_kb(uid))

@dp.callback_query(lambda c: c.data == "settings_risk")
async def cb_settings_risk(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    db.create_default_user(uid, c.from_user.username)
    user = db.get_user(uid) or {}
    s = user.get("settings", {}) or {}
    fields = {k: s.get(k) for k in ("ORDER_PERCENT", "ORDER_SIZE_USD", "TP_PCT", "SL_PCT", "MIN_NOTIONAL") if k in s}
    txt = t(uid, "risk_title", fields=json.dumps(fields, indent=2, ensure_ascii=False))
    kb = InlineKeyboardBuilder(); kb.button(text=t(uid, "settings_back"), callback_data="settings_back"); kb.adjust(1)
    try:
        await c.message.edit_text(txt, reply_markup=kb.as_markup())
    except Exception:
        await c.message.answer(txt, reply_markup=kb.as_markup())

@dp.callback_query(lambda c: c.data == "settings_indicators")
async def cb_settings_indicators(c: types.CallbackQuery):
    await c.answer()
    kb = InlineKeyboardBuilder()
    kb.button(text="🌐 Global", callback_data="ind_global")
    kb.button(text="🔧 Advanced", callback_data="ind_advanced")
    kb.button(text=t(c.from_user.id, "settings_back"), callback_data="settings_back")
    kb.adjust(1)
    try:
        await c.message.edit_text(t(c.from_user.id, "indicators_menu_title"), reply_markup=kb.as_markup())
    except Exception:
        await c.message.answer(t(c.from_user.id, "indicators_menu_title"), reply_markup=kb.as_markup())

@dp.callback_query(lambda c: c.data == "ind_global")
async def cb_ind_global(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    db.create_default_user(uid, c.from_user.username)
    settings = (db.get_user(uid) or {}).get("settings", {}) or {}
    kb = InlineKeyboardBuilder()
    for ind in ("RSI", "MACD", "EMA", "OI"):
        key = f"{ind}_ENABLED"
        cur = bool(settings.get(key, True))
        label = f"{ind}: {'ON' if cur else 'OFF'}"
        kb.button(text=label, callback_data=f"ind_toggle:{ind}")
    kb.adjust(2)
    kb.button(text=t(uid, "settings_back"), callback_data="settings_indicators")
    try:
        await c.message.edit_text(t(uid, "indicators_global_title"), reply_markup=kb.as_markup())
    except Exception:
        await c.message.answer(t(uid, "indicators_global_title"), reply_markup=kb.as_markup())

@dp.callback_query(lambda c: c.data and c.data.startswith("ind_toggle:"))
async def cb_ind_toggle(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    try:
        _, ind = c.data.split(":", 1)
    except Exception:
        await c.message.answer(t(uid, "error_data"))
        return
    key = f"{ind}_ENABLED"
    db.create_default_user(uid, c.from_user.username)
    user = db.get_user(uid) or {}
    settings = user.get("settings", {}) or {}
    cur = bool(settings.get(key, True))
    new = not cur
    db.update_setting(uid, key, new)
    await cb_ind_global(c)

@dp.callback_query(lambda c: c.data == "ind_advanced")
async def cb_ind_advanced(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    user = db.get_user(uid) or {}
    settings = user.get("settings", {}) or {}
    txt = t(uid, "indicators_advanced_text", settings=json.dumps(settings, indent=2, ensure_ascii=False))
    kb = InlineKeyboardBuilder(); kb.button(text=t(uid, "settings_back"), callback_data="settings_indicators"); kb.adjust(1)
    try:
        await c.message.edit_text(txt, reply_markup=kb.as_markup())
    except Exception:
        await c.message.answer(txt, reply_markup=kb.as_markup())

# Trade mode handlers (NEW)
@dp.callback_query(lambda c: c.data == "settings_trade_mode")
async def cb_settings_trade_mode(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    db.create_default_user(uid, c.from_user.username)
    user = db.get_user(uid) or {}
    settings = user.get("settings", {}) or {}
    current = _read_trade_mode(settings)
    kb = InlineKeyboardBuilder()
    kb.button(text=f"📊 Mixed {'✅' if current == 'mixed' else ''}", callback_data="trade_mode:mixed")
    kb.button(text=f"📈 Futures Only {'✅' if current == 'futures_only' else ''}", callback_data="trade_mode:futures_only")
    kb.button(text=f"💱 Spot Only {'✅' if current == 'spot_only' else ''}", callback_data="trade_mode:spot_only")
    kb.button(text=t(uid, "settings_back"), callback_data="settings_back")
    kb.adjust(1)
    try:
        await c.message.edit_text(t(uid, "trade_mode_title", current=_friendly_mode_label(current)), reply_markup=kb.as_markup())
    except Exception:
        await c.message.answer(t(uid, "trade_mode_title", current=_friendly_mode_label(current)), reply_markup=kb.as_markup())

@dp.callback_query(lambda c: c.data and c.data.startswith("trade_mode:"))
async def cb_trade_mode_set(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    try:
        _, mode = c.data.split(":", 1)
    except Exception:
        await c.message.answer(t(uid, "error_data"))
        return
    db.create_default_user(uid, c.from_user.username)
    mode_norm = mode if mode in ("mixed", "futures_only", "spot_only") else "mixed"
    db.update_setting(uid, "TRADE_MODE", mode_norm)
    try:
        await c.message.delete()
    except Exception:
        pass
    await c.message.answer(t(uid, "trade_mode_set_ok", mode=_friendly_mode_label(mode_norm)), reply_markup=main_reply_kb(uid))

# Оьработчик "Back" для возврата в главное меню

@dp.callback_query(lambda c: c.data.endswith("_back"))
async def cb_any_back(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    try:
        await c.message.delete()
    except Exception:
        pass
    await c.message.answer(
        t(uid, "🏠 You are in the main menu\n✨ Choose an action below to get started"),
        reply_markup=main_reply_kb(uid)
    )


# ---------- New "My trades" replaced by Pairs management UI ----------
@dp.message(lambda m: m.text == t(m.from_user.id, "menu_trades"))
async def menu_trades(m: types.Message):
    uid = m.from_user.id
    # проверка подписки (как было)
    if not has_active_sub(uid):
        await m.reply(t(uid, "subscribe_required"), reply_markup=main_reply_kb(uid))
        return

    # Попытка загрузить trades.json
    try:
        if not os.path.exists(TRADES_FILE):
            await m.reply(t(uid, "no_trades"), reply_markup=main_reply_kb(uid))
            return
        with open(TRADES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.exception("Failed to load trades file: %s", e)
        await m.reply(t(uid, "no_trades"), reply_markup=main_reply_kb(uid))
        return

    # Собираем сделки относящиеся к пользователю
    trades_for_user = []

    # Если файл — список записей:
    if isinstance(data, list):
        for item in data:
            try:
                # допускаем, что user_id может быть строкой или числом
                if str(item.get("user_id", "")).strip() == str(uid):
                    trades_for_user.append(item)
            except Exception:
                continue

    # Если файл — словарь: попробуем найти ключ = uid или в значениях списки
    elif isinstance(data, dict):
        # 1) прямой ключ
        try:
            if str(uid) in data and isinstance(data[str(uid)], list):
                trades_for_user = data[str(uid)]
        except Exception:
            pass

        # 2) иначе пробуем собрать из вложенных списков/записей
        if not trades_for_user:
            for k, v in data.items():
                if isinstance(v, list):
                    for item in v:
                        try:
                            if str(item.get("user_id", "")).strip() == str(uid):
                                trades_for_user.append(item)
                        except Exception:
                            continue
    else:
        # непонятный формат
        logger.warning("Unknown trades.json structure: %s", type(data))

    if not trades_for_user:
        await m.reply(t(uid, "no_trades"), reply_markup=main_reply_kb(uid))
        return

    # Форматирование и отправка (последние 50 записей пользователя)
    lines = []
    for r in trades_for_user[-50:]:
        ts = r.get("ts") or r.get("timestamp") or r.get("time") or ""
        symbol = r.get("symbol") or r.get("pair") or ""
        side = r.get("side") or r.get("action") or ""
        qty = r.get("qty") or r.get("amount") or ""
        price = r.get("price") or ""
        pnl = r.get("pnl") or r.get("profit") or ""
        # используем локализованный шаблон
        try:
            lines.append(t(uid, "trade_notification",
                           symbol=symbol, side=side, price=price, qty=qty, pnl=pnl, ts=ts))
        except Exception:
            # fallback plain formatting
            lines.append(f"{ts} {symbol} {side} {qty}@{price} PnL:{pnl}")

    # Отправляем частями чтобы не привысить лимиты
    chunk_size = 5
    for i in range(0, len(lines), chunk_size):
        await m.reply("\n\n".join(lines[i : i + chunk_size]))

    await m.reply(t(uid, "trades_end"), reply_markup=main_reply_kb(uid))

# support / admin flows
@dp.message(lambda m: m.text == t(m.from_user.id, "menu_support"))
async def menu_support(m: types.Message, state: FSMContext):
    await m.reply(t(m.from_user.id, "support_prompt"), reply_markup=main_reply_kb(m.from_user.id))
    await state.set_state(Form.support_user)

@dp.message(Form.support_user)
async def process_support_user(m: types.Message, state: FSMContext):
    txt = m.text or "<non-text>"
    uname = m.from_user.username or m.from_user.full_name or str(m.from_user.id)
    admin_text = f"📩 Support from @{uname} (id={m.from_user.id}):\n{txt}"
    try:
        if ADMIN_ID:
            await bot.send_message(ADMIN_ID, admin_text, reply_markup=admin_reply_kb_for_user(m.from_user.id))
        else:
            logger.warning("ADMIN_ID not configured - support message not forwarded to admin")
        await m.answer(t(m.from_user.id, "support_sent"), reply_markup=main_reply_kb(m.from_user.id))
    except Exception:
        logger.exception("Failed to forward support to admin")
        await m.answer(t(m.from_user.id, "support_failed"), reply_markup=main_reply_kb(m.from_user.id))
    await state.clear()

@dp.message(lambda m: m.text == t(m.from_user.id, "menu_info"))
async def menu_info(m: types.Message):
    uid = m.from_user.id
    await m.reply(t(uid, "info_text"), reply_markup=main_reply_kb(uid))

@dp.callback_query(lambda c: c.data and c.data.startswith("admin_reply:"))
async def cb_admin_reply(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    if c.from_user.id != ADMIN_ID:
        await c.message.answer(t(c.from_user.id, "admin_only"))
        return
    try:
        _, uid_s = c.data.split(":", 1)
        uid = int(uid_s)
    except Exception:
        await c.message.answer(t(c.from_user.id, "invalid_user_id"))
        return
    await c.message.answer(t(c.from_user.id, "enter_reply_prompt", user_id=uid))
    await state.update_data(reply_to=uid)
    await state.set_state(Form.admin_reply)

@dp.message(Form.admin_reply)
async def process_admin_reply(m: types.Message, state: FSMContext):
    data = await state.get_data()
    target = data.get("reply_to")
    if not target:
        await m.reply(t(m.from_user.id, "error_data"))
        await state.clear()
        return
    text = m.text or ""
    try:
        await bot.send_message(int(target), f"📩 {t(m.from_user.id, 'reply_sent')}\n\n{text}")
        await m.reply(t(m.from_user.id, "reply_sent"), reply_markup=main_reply_kb(m.from_user.id))
        if ADMIN_ID and ADMIN_ID != m.from_user.id:
            try:
                await bot.send_message(ADMIN_ID, f"Admin @{m.from_user.username} replied to user {target}.")
            except Exception:
                pass
    except Exception:
        logger.exception("Failed to send admin reply to user %s", target)
        await m.reply(t(m.from_user.id, "support_failed"))
    await state.clear()

@dp.message(Command("cancel"))
async def cmd_cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.reply(t(m.from_user.id, "action_cancelled"), reply_markup=main_reply_kb(m.from_user.id))

@dp.message(Command("help"))
async def cmd_help(m: types.Message):
    await m.reply(t(m.from_user.id, "welcome_short"), reply_markup=main_reply_kb(m.from_user.id))

@dp.message(lambda m: m.text and m.text.upper().startswith("SET "))
async def cmd_set_text(m: types.Message):
    try:
        _, key, val = m.text.strip().split(None, 2)
        if val.replace(".", "", 1).lstrip("-").isdigit():
            v = float(val) if "." in val else int(val)
        else:
            if val.lower() in ("true", "false"):
                v = val.lower() == "true"
            else:
                v = val
        db.update_setting(m.from_user.id, key, v)
        await m.reply(f"✅ {key} -> {v}", reply_markup=main_reply_kb(m.from_user.id))
    except Exception:
        await m.reply(t(m.from_user.id, "set_usage"), reply_markup=main_reply_kb(m.from_user.id))

# helper: create invoice (simple wrapper, optional)
def create_invoice_sync(user_id: int, amount: float):
    try:
        if CRYPTOBOT_TOKEN:
            payload = {"amount": float(amount), "currency": PAYMENT_ASSET, "payload": str(user_id)}
            r = session.post(CRYPTO_CREATE_INVOICE_URL, json=payload, headers=CRYPTO_HEADERS, timeout=8)
            j = r.json() if r is not None else {}
            inv_id = j.get("id") or j.get("invoiceId") or str(int(time.time()))
            url = j.get("payUrl") or j.get("url") or f"https://pay.crypt.bot/invoice/{inv_id}"
            return url, inv_id
    except Exception:
        logger.exception("create_invoice crypt.bot failed")
    inv = f"manual-{int(time.time())}"
    url = f"https://your-pay.example.com/invoice/{inv}"
    return url, inv

def fetch_invoice_status(inv_id: str):
    try:
        if CRYPTOBOT_TOKEN:
            r = session.get(CRYPTO_GET_INVOICES_URL, headers=CRYPTO_HEADERS, timeout=8, params={"invoiceId": inv_id})
            j = r.json() if r is not None else {}
            return j
    except Exception:
        logger.exception("fetch_invoice_status failed")
    return None

# ---------- Workers ----------

async def trades_worker():
    last_index = 0
    try:
        if os.path.exists(TRADES_FILE):
            with open(TRADES_FILE, "r", encoding="utf-8") as f:
                arr = json.load(f)
                last_index = len(arr)
    except Exception:
        last_index = 0

    try:
        await bot.get_me()
    except Exception:
        logger.warning("Bot.get_me failed at trades_worker startup")

    while True:
        try:
            if not os.path.exists(TRADES_FILE):
                await asyncio.sleep(2)
                continue
            with open(TRADES_FILE, "r", encoding="utf-8") as f:
                trades = json.load(f)
            if len(trades) > last_index:
                new_items = trades[last_index:]
                await send_trade_notifications(new_items)
                last_index = len(trades)
        except Exception:
            logger.exception("trades_worker error")
        await asyncio.sleep(3)

async def send_trade_notifications(trade_items):
    for t_item in trade_items:
        try:
            uid = int(t_item.get("user_id"))
            if not has_active_sub(uid) or not is_trading_active(uid):
                continue
            try:
                await bot.send_message(uid, t(uid, "trade_notification",
                                             symbol=t_item.get('symbol'), side=t_item.get('side'),
                                             price=t_item.get('price'), qty=t_item.get('qty'),
                                             pnl=t_item.get('pnl'), ts=t_item.get('ts') or t_item.get('timestamp')))
            except Exception:
                await bot.send_message(uid, f"⚡️ Trade: {t_item.get('symbol')} {t_item.get('side')}\nPrice: {t_item.get('price')}\nQty: {t_item.get('qty')}\nPnL: {t_item.get('pnl')}\n{t_item.get('ts') or t_item.get('timestamp')}")
        except Exception:
            logger.exception("send_trade_notifications error for trade %s", t_item)

async def check_invoices_worker():
    try:
        await bot.get_me()
    except Exception:
        logger.warning("Bot.get_me failed at invoices_worker startup")

    while True:
        try:
            users = db.load_users() if hasattr(db, "load_users") else {}
            for uid_str, u in users.items():
                try:
                    uid = int(uid_str)
                except Exception:
                    continue
                settings = (u.get("settings") or {})
                inv_id = settings.get("last_invoice_id")
                if not inv_id:
                    continue
                inv = fetch_invoice_status(str(inv_id))
                if not inv:
                    continue
                status_val = ""
                if isinstance(inv, dict):
                    status_val = str(inv.get("status") or inv.get("state") or inv.get("result") or "").lower()
                if any(k in status_val for k in ("paid", "confirmed", "success")):
                    try:
                        choice = settings.get("last_invoice_choice", "30")
                        if choice == "30":
                            days = 30
                        elif choice == "365":
                            days = 365
                        elif choice == "forever":
                            days = 365 * 100
                        else:
                            days = 30
                        db.set_subscription(uid, days=days)
                        db.update_setting(uid, "last_invoice_id", None)
                        db.update_setting(uid, "last_invoice_choice", None)
                        try:
                            await bot.send_message(uid, t(uid, "invoice_paid", days=days if days < 100000 else "forever"))
                        except Exception:
                            pass
                        if ADMIN_ID:
                            try:
                                await bot.send_message(ADMIN_ID, f"💰 User {uid} paid subscription (invoice {inv_id}).")
                            except Exception:
                                pass
                    except Exception:
                        logger.exception("Failed to set subscription for paid invoice")
        except Exception:
            logger.exception("check_invoices_worker error")
        await asyncio.sleep(8)

# Admin broadcast & give_sub
@dp.message(lambda m: m.from_user.id == ADMIN_ID and m.text and m.text.startswith("/broadcast "))
async def admin_broadcast(m: types.Message):
    text = m.text[len("/broadcast "):].strip()
    if not text:
        await m.reply("Usage: /broadcast <text>")
        return
    users = db.load_users() if hasattr(db, "load_users") else {}
    failed = 0
    sent = 0
    for uid_str in users.keys():
        try:
            uid = int(uid_str)
            try:
                await bot.send_message(uid, text)
                sent += 1
            except Exception:
                failed += 1
        except Exception:
            failed += 1
    await m.reply(f"Broadcast sent: {sent} success, {failed} failed.")

@dp.message(lambda m: m.from_user.id == ADMIN_ID and m.text and m.text.startswith("/give_sub"))
async def admin_give_sub(m: types.Message):
    parts = m.text.strip().split()
    if len(parts) < 3:
        await m.reply("Usage: /give_sub <user_id> <days|forever>")
        return
    _, uid_s, days_s = parts[:3]
    try:
        uid = int(uid_s)
    except Exception:
        await m.reply("Invalid user id.")
        return
    if days_s.lower() == "forever":
        days = 365 * 100
    else:
        try:
            days = int(days_s)
        except Exception:
            await m.reply("Invalid days parameter.")
            return
    try:
        db.set_subscription(uid, days=days)
        await m.reply(f"Subscription granted to {uid} for {('forever' if days>36500 else days)} days.")
        try:
            await bot.send_message(uid, f"✅ Admin granted you subscription for {('forever' if days>36500 else days)} days.")
        except Exception:
            pass
    except Exception as e:
        await m.reply(f"Error giving subscription: {e}")

async def _global_errors_handler(update: types.Update, exception: Exception = None):
    try:
        logger.exception("Unhandled exception for update %s: %s", update, exception)
        if ADMIN_ID and _should_notify("dp_unhandled"):
            msg = f"❗️Unhandled error: {type(exception).__name__}\n{str(exception)[:800]}"
            await _async_send_admin(msg)
    except Exception:
        logger.exception("Error in global error handler")
    return True

dp.errors.register(_global_errors_handler)

async def main():
    tasks = [
        asyncio.create_task(trades_worker(), name="trades_worker"),
        asyncio.create_task(check_invoices_worker(), name="check_invoices_worker"),
        asyncio.create_task(dp.start_polling(bot), name="telegram_poller"),
    ]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for t in done:
            if t.exception():
                raise t.exception()
    except asyncio.CancelledError:
        logger.info("Main cancelled")
    except Exception:
        logger.exception("Unhandled exception in main tasks")
        notify_admin_rate_limited_sync("Main loop crashed: check logs", key="main_crash")
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        try:
            await bot.session.close()
        except Exception:
            pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down (KeyboardInterrupt)...")
    except Exception:
        logger.exception("Unhandled exception in __main__")
