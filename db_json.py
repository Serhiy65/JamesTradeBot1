import sys
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

import os, json, threading, traceback
from datetime import datetime, timedelta

# --- Encryption disabled ---
def decrypt(value):
    return value


LOCK = threading.Lock()
USERS_FILE = os.getenv('USERS_FILE', './users.json')
TRADES_FILE = os.getenv('TRADES_FILE', './trades.json')


def _ensure_files():
    for path, default in [(USERS_FILE, {},), (TRADES_FILE, [])]:
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(default, f, indent=4, ensure_ascii=False)


def _read(path, default):
    try:
        if not os.path.exists(path):
            _ensure_files()
            return default
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        traceback.print_exc()
        return default


def _write(path, data):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except:
        traceback.print_exc()


# ------------------------ DEFAULT SETTINGS ------------------------

DEFAULT_SETTINGS = {
    # Indicators
    'USE_RSI': True, 'RSI_PERIOD': 14, 'RSI_OVERSOLD': 30, 'RSI_OVERBOUGHT': 70,
    'USE_EMA': True, 'FAST_MA': 50, 'SLOW_MA': 200,
    'USE_MACD': True, 'MACD_FAST': 8, 'MACD_SLOW': 21, 'MACD_SIGNAL': 5,

    # Market behavior
    'USE_OI': False,
    'OI_WINDOW': 3,
    'OI_MIN_CHANGE_PCT': 5.0,
    'OI_DIRECTION': 'up',

    # Confirmation logic
    'BUY_CONFIRMATION_RATIO': 0.66,
    'SELL_CONFIRMATION_RATIO': 0.33,

    # Order size
    'ORDER_PERCENT': 10.0,
    'ORDER_SIZE_USD': 0.0,

    # Risk
    'TP_PCT': 1.0,
    'SL_PCT': 0.5,
    'QTY_PRECISION': 6,
    'MIN_NOTIONAL': 5.0,

    # Symbols & timeframe
    'SYMBOLS': ['BTCUSDT'],
    'TIMEFRAME': '5',

    # System
    'TESTNET': True,
    'DRY_RUN': False,
    'DISABLED_AUTH': False,

    # Futures / Spot handling
    'TRADE_MODE': 'FUTURES',       # SPOT / FUTURES / FUTURES_ONLY
    'ENABLE_SHORTS': True,
    'ENABLE_LONGS': True,
    'FUTURES_ONLY': True,
    'DEFAULT_LEVERAGE': 3,

    # Extra behavior
    'ALLOW_SPOT_BUY': False,
    'ALLOW_SPOT_SELL': False,
}


def set_subscription(uid, days, path=None):
    """
    Добавляет подписку пользователю:
    - если подписки нет -> начинает с сегодняшнего дня
    - если подписка активна -> продлевает от текущей даты окончания
    - days: количество дней подписки
    """
    users = load_users(path)
    users = _ensure_user_defaults(users, uid)
    u = users[str(uid)]

    now = datetime.utcnow()

    # старая дата
    old = u.get("sub_until")

    if old:
        try:
            old_dt = datetime.fromisoformat(old)
        except:
            old_dt = now
    else:
        old_dt = now

    # если просрочена — начинаем заново
    if old_dt < now:
        new_dt = now + timedelta(days=days)
    else:
        new_dt = old_dt + timedelta(days=days)

    u["sub_until"] = new_dt.isoformat()

    users[str(uid)] = u
    save_users(users, path)

    return u["sub_until"]



def is_subscribed(uid, path=None):
    users = load_users(path)
    u = users.get(str(uid))
    if not u:
        return False

    until = u.get("sub_until")
    if not until:
        return False

    try:
        dt = datetime.fromisoformat(until)
    except:
        return False

    return dt > datetime.utcnow()



# ------------------------ USER NORMALIZATION ------------------------

def _ensure_user_defaults(users, uid, username=None):
    uid = str(uid)
    if uid not in users:
        users[uid] = {}

    u = users[uid]
    u.setdefault('username', username or f"user_{uid}")
    u.setdefault('api_key', '')
    u.setdefault('api_secret', '')
    u.setdefault('sub_until', None)

    # NEW: trial flag — False by default
    u.setdefault('used_trial', False)

    if 'settings' not in u or not isinstance(u['settings'], dict):
        u['settings'] = {}

    # inject missing defaults
    for k, v in DEFAULT_SETTINGS.items():
        u['settings'].setdefault(k, v)

    # internal
    u.setdefault('_positions', {})

    users[uid] = u
    return users


# ------------------------ MIGRATION ------------------------

def _looks_encrypted_key(s: str) -> bool:
    if not s or not isinstance(s, str):
        return False
    s = s.strip()
    if s.startswith("gAAAA") and len(s) > 50:
        return True
    if "..." in s:
        return True
    return False


def _migrate_encrypted_keys(users):
    changed = False
    for uid, u in users.items():
        ak = u.get('api_key', '')
        sk = u.get('api_secret', '')

        if _looks_encrypted_key(ak):
            u['api_key_encrypted_value'] = ak
            u['api_key'] = ''
            u['needs_key_entry'] = True
            changed = True

        if _looks_encrypted_key(sk):
            u['api_secret_encrypted_value'] = sk
            u['api_secret'] = ''
            u['needs_key_entry'] = True
            changed = True

    return users, changed


# ------------------------ CRUD ------------------------

def load_users(path=None):
    _ensure_files()
    return _read(path or USERS_FILE, {})


def save_users(data, path=None):
    with LOCK:
        _write(path or USERS_FILE, data)


def get_user(uid, path=None):
    users = load_users(path)
    users = _ensure_user_defaults(users, uid)
    save_users(users, path)
    return users[str(uid)]


def create_default_user(uid, username=None, path=None):
    users = load_users(path)
    users = _ensure_user_defaults(users, uid, username)
    save_users(users, path)
    return users[str(uid)]


def set_api_keys(uid, api_key, api_secret, path=None):
    users = load_users(path)
    users = _ensure_user_defaults(users, uid)
    u = users[str(uid)]

    u['api_key'] = (api_key or "").strip()
    u['api_secret'] = (api_secret or "").strip()

    # remove old flags
    for k in ['api_key_encrypted_value','api_key_encrypted',
              'api_secret_encrypted_value','api_secret_encrypted',
              'needs_key_entry']:
        u.pop(k, None)

    users[str(uid)] = u
    save_users(users, path)


def update_setting(uid, key, value, path=None):
    users = load_users(path)
    users = _ensure_user_defaults(users, uid)
    users[str(uid)]['settings'][key] = value
    save_users(users, path)
    return users[str(uid)]['settings']


def append_trade(tr, path=None):
    path = path or TRADES_FILE
    with LOCK:
        arr = _read(path, [])
        arr.append(tr)
        _write(path, arr)


def get_trades_for_user(uid, limit=100, path=None):
    trades = _read(path or TRADES_FILE, [])
    uid = str(uid)
    return [t for t in trades if str(t.get('user_id')) == uid][-limit:]


# ------------------------ TRIAL HELPERS ------------------------

def has_used_trial(user_id: int) -> bool:
    """True если пользователь уже использовал trial (флаг used_trial в settings)."""
    try:
        u = get_user(user_id) or {}
        settings = u.get("settings") or {}
        return bool(settings.get("used_trial", False))
    except Exception:
        return False

def set_used_trial(user_id: int, used: bool = True):
    """Установить флаг used_trial (использован ли trial)."""
    try:
        update_setting(user_id, "used_trial", bool(used))
    except Exception:
        # не фатально, логгируем
        import logging
        logging.getLogger(__name__).exception("set_used_trial failed for %s", user_id)


# ------------------------ AUTO START ------------------------

def _startup():
    _ensure_files()
    users = load_users()

    changed = False

    # normalize
    for uid in list(users.keys()):
        before = json.dumps(users.get(uid), sort_keys=True)
        users = _ensure_user_defaults(users, uid)
        after = json.dumps(users.get(uid), sort_keys=True)
        if before != after:
            changed = True

    # migrate
    users, migrated = _migrate_encrypted_keys(users)
    if migrated:
        changed = True

    if changed:
        save_users(users)

    print("[DB_JSON] Готово — пользователи нормализованы, трейд-режимы добавлены! ✅")

_startup()
