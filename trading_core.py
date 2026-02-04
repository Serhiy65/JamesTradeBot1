# trading_core.py
# UPDATED: forceable futures trading + TRADE_MODE option
# Python 3.11+, uses local client.py (Bybit) and db_json.py (if present).
# Note: copy this file over your current trading_core.py

import sys, os, time, json, math, logging, threading, traceback, requests
from datetime import datetime
from typing import Optional, Dict, Any

# ensure utf-8 console
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("trading_core")

USERS_FILE = os.getenv("USERS_FILE", "./users.json")
TRADES_FILE = os.getenv("TRADES_FILE", "./trades.json")
LOCK = threading.Lock()

# Telegram notify config (optional)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_CHAT = os.getenv("TELEGRAM_ADMIN_CHAT")

# try local client/db modules
client_mod = None
db_mod = None
try:
    import client as client_mod
    logger.info("Imported local client.py module.")
except Exception:
    client_mod = None
    logger.info("Local client.py not found — fallback will be used.")

try:
    import db_json as db_mod
    logger.info("Imported local db_json.py module.")
except Exception:
    db_mod = None
    logger.info("Local db_json.py not found — using file helpers.")

# try pandas
try:
    import pandas as pd
    import numpy as np
except Exception:
    pd = None
    np = None
    logger.warning("pandas/numpy missing — indicators need them to work properly.")

# ----------------- file helpers -----------------
def _ensure_files():
    for path, default in [(USERS_FILE, {}), (TRADES_FILE, [])]:
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(default, f, indent=4, ensure_ascii=False)

def load_users_file():
    _ensure_files()
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_users_file(data):
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception:
        logger.exception("Failed save users.json")

def append_trade_file(tr):
    with LOCK:
        _ensure_files()
        try:
            with open(TRADES_FILE, "r", encoding="utf-8") as f:
                arr = json.load(f)
        except Exception:
            arr = []
        arr.append(tr)
        try:
            with open(TRADES_FILE, "w", encoding="utf-8") as f:
                json.dump(arr, f, indent=4, ensure_ascii=False)
        except Exception:
            logger.exception("Failed write trades.json")

def load_users():
    if db_mod and hasattr(db_mod, "load_users"):
        return db_mod.load_users()
    return load_users_file()

def save_users(u):
    if db_mod and hasattr(db_mod, "save_users"):
        return db_mod.save_users(u)
    return save_users_file(u)

def append_trade(tr):
    if db_mod and hasattr(db_mod, "append_trade"):
        return db_mod.append_trade(tr)
    return append_trade_file(tr)

# ----------------- util -----------------
def mask_key(k):
    if not k:
        return "<empty>"
    s = str(k)
    return (s[:6] + "..." + s[-6:]) if len(s) > 12 else s

def send_telegram_message(chat_id: str, text: str):
    token = TELEGRAM_BOT_TOKEN
    if not token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        r = requests.post(url, data=payload, timeout=8)
        return r.status_code == 200
    except Exception:
        logger.exception("Telegram send failed")
        return False

def notify_trade_to_user(user: dict, trade: dict):
    try:
        chat = user.get("chat_id") or user.get("tg_chat") or user.get("tg_id") or user.get("chat") or TELEGRAM_ADMIN_CHAT
        if not chat:
            return False
        dry = trade.get("dry", False)
        parts = []
        if dry:
            parts.append("🔎 <b>DRY_RUN</b> — simulated trade")
        parts.append(f"👤 User: {user.get('username','')} (id: {user.get('id') or user.get('user_id','')})")
        parts.append(f"📌 Market: {trade.get('market_type','')} | {trade.get('symbol')}")
        parts.append(f"🛠 Action: {trade.get('side')} {trade.get('action','')}".strip())
        parts.append(f"Qty: {trade.get('qty')} @ Price: {trade.get('price')}")
        if "leverage" in trade:
            parts.append(f"Leverage: {trade.get('leverage')}")
        if "result" in trade and isinstance(trade.get("result"), dict):
            res = trade.get("result")
            if res.get("retCode") is not None:
                parts.append(f"API retCode: {res.get('retCode')} retMsg: {res.get('retMsg')}")
        parts.append(f"⏱ {trade.get('timestamp')}")
        txt = "\n".join(parts)
        send_telegram_message(chat, txt)
        return True
    except Exception:
        logger.exception("notify_trade_to_user failed")
        return False

# ----------------- symbol normalization -----------------
def _normalize_symbols(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, list):
        out = []
        for it in raw:
            if not it:
                continue
            s = str(it).strip().upper()
            if s:
                out.append(s)
        return out
    if isinstance(raw, str):
        parts = [p.strip().upper() for p in raw.replace(";", ",").split(",") if p.strip()]
        return parts
    try:
        return [str(raw).strip().upper()]
    except Exception:
        return []

# ----------------- ensure defaults -----------------
def _ensure_user_defaults(users, uid):
    uid = str(uid)
    if uid not in users:
        users[uid] = {}
    u = users[uid]
    u.setdefault("username", f"user_{uid}")
    u.setdefault("api_key", "")
    u.setdefault("api_secret", "")
    if "settings" not in u or not isinstance(u["settings"], dict):
        u["settings"] = {}
    s = u["settings"]
    defaults = {
        "USE_RSI": True, "RSI_PERIOD": 14, "RSI_OVERSOLD": 40, "RSI_OVERBOUGHT": 60,
        "USE_EMA": True, "FAST_MA": 50, "SLOW_MA": 200,
        "USE_MACD": True, "MACD_FAST": 8, "MACD_SLOW": 21, "MACD_SIGNAL": 5,
        "USE_OI": False,
        "BUY_CONFIRMATION_RATIO": 0.66, "SELL_CONFIRMATION_RATIO": 0.33,
        "ORDER_PERCENT": 10.0, "ORDER_SIZE_USD": 0.0,
        "TP_PCT": 1.0, "SL_PCT": 0.5,
        "QTY_PRECISION": 6, "MIN_NOTIONAL": 5.0,
        "SYMBOLS": ["BTCUSDT"], "TIMEFRAME": "5",
        "TESTNET": True, "DRY_RUN": False, "DISABLED_AUTH": False,
        # futures-specific
        "ENABLE_SHORTS": True,
        "DEFAULT_LEVERAGE": 3,
        "FUTURES_MARGIN_MODE": "isolated",
        # new: trade mode
        "TRADE_MODE": "mixed"  # options: mixed / spot_only / futures_only
    }
    for k, v in defaults.items():
        s.setdefault(k, v)

    # migrate lowercase "symbols"
    if "symbols" in s and "SYMBOLS" not in s:
        syms = _normalize_symbols(s.get("symbols"))
        if syms:
            s["SYMBOLS"] = syms
        try:
            del s["symbols"]
        except Exception:
            pass

    if isinstance(s.get("SYMBOLS"), str):
        s["SYMBOLS"] = _normalize_symbols(s.get("SYMBOLS"))
    elif not isinstance(s.get("SYMBOLS"), list):
        s["SYMBOLS"] = defaults["SYMBOLS"][:]

    u.setdefault("_positions", {})
    users[uid] = u

# ----------------- indicators -----------------
def rsi_series(close, period=14):
    if pd is None:
        raise RuntimeError("pandas required for indicators")
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ma_up = up.ewm(com=period-1, adjust=False).mean()
    ma_down = down.ewm(com=period-1, adjust=False).mean()
    rs = ma_up / (ma_down + 1e-12)
    rsi = 100 - (100 / (1 + rs))
    return rsi

def ema_series(close, period):
    if pd is None:
        raise RuntimeError("pandas required")
    return close.ewm(span=period, adjust=False).mean()

def macd_hist_series(close, fast=12, slow=26, signal=9):
    if pd is None:
        raise RuntimeError("pandas required")
    f = ema_series(close, fast)
    s = ema_series(close, slow)
    macd = f - s
    sig = macd.ewm(span=signal, adjust=False).mean()
    hist = macd - sig
    return macd, sig, hist

# ----------------- client factory -----------------
def make_client(api_key, api_secret, testnet):
    if client_mod and hasattr(client_mod, "BybitClient"):
        try:
            return client_mod.BybitClient(api_key=api_key, api_secret=api_secret, testnet=bool(testnet))
        except Exception:
            logger.exception("Failed init local BybitClient — fallback")

    import requests as _requests, time as _time, hmac as _hmac, hashlib as _hashlib
    class Fallback:
        def __init__(self, k, s, testnet):
            self.api_key = (k or "").strip()
            self.api_secret = (s or "").strip()
            self.testnet = bool(testnet)
            self.base = "https://api-testnet.bybit.com" if self.testnet else "https://api.bybit.com"
            self.sess = _requests.Session()
            self.recv_window = 10000
            self._time_offset_ms = None

        def _sync_server_time(self):
            try:
                url = ( "https://api-testnet.bybit.com" if self.testnet else "https://api.bybit.com" ) + "/v2/public/time"
                r = self.sess.get(url, timeout=6)
                j = r.json()
                server_ms = None
                if isinstance(j, dict):
                    if "time_now" in j:
                        server_sec = float(j["time_now"])
                        server_ms = int(server_sec * 1000)
                    elif "time" in j:
                        try:
                            server_ms = int(j.get("time"))
                        except Exception:
                            server_ms = None
                if server_ms is None:
                    return False
                local_ms = int(_time.time() * 1000)
                self._time_offset_ms = server_ms - local_ms
                logger.info("[Fallback] synced server time offset %d ms", self._time_offset_ms)
                return True
            except Exception:
                logger.exception("[Fallback] server time sync failed")
                return False

        def _now_ms(self):
            local = int(_time.time() * 1000)
            if self._time_offset_ms is None:
                try:
                    self._sync_server_time()
                except Exception:
                    self._time_offset_ms = 0
            return local + (self._time_offset_ms or 0)

        def _sign_headers(self, params_or_body=""):
            try:
                ts = str(self._now_ms())
                recv = str(self.recv_window)
                if isinstance(params_or_body, dict):
                    param_str = "&".join(f"{k}={params_or_body[k]}" for k in sorted(params_or_body))
                else:
                    param_str = str(params_or_body or "")
                origin = ts + (self.api_key or "") + recv + param_str
                sign = _hmac.new((self.api_secret or "").encode(), origin.encode(), _hashlib.sha256).hexdigest()
                return {
                    "X-BAPI-API-KEY": self.api_key,
                    "X-BAPI-TIMESTAMP": ts,
                    "X-BAPI-RECV-WINDOW": recv,
                    "X-BAPI-SIGN": sign
                }
            except Exception:
                logger.exception("Fallback._sign_headers exception")
                return {}

        def _get(self, path, params=None, auth=False):
            url = self.base + path
            try:
                if auth:
                    headers = self._sign_headers(params or {})
                    r = self.sess.get(url, params=params, headers=headers, timeout=12)
                else:
                    r = self.sess.get(url, params=params, timeout=12)
                try:
                    return r.json()
                except Exception:
                    logger.warning("Fallback._get: json decode failed, status=%s", r.status_code)
                    return {"retCode": -1, "retMsg": f"json decode failed status={r.status_code}"}
            except Exception as e:
                logger.exception("Fallback._get exception for %s", url)
                return {"retCode": -1, "retMsg": str(e)}

        def get_balance_usdt(self):
            try:
                params = {"coin": "USDT", "accountType": "UNIFIED"}
                r = self._get("/v5/account/wallet-balance", params=params, auth=True)
                if not isinstance(r, dict):
                    return None
                if r.get("retCode", 0) != 0:
                    logger.warning("get_balance_usdt: retCode=%s retMsg=%s", r.get("retCode"), r.get("retMsg"))
                    return r
                res = r.get("result") or {}
                lst = res.get("list") or []
                for it in lst:
                    coin = (it.get("coin") or it.get("currency") or it.get("asset") or "").upper()
                    if coin == "USDT":
                        for fld in ("equity", "availableBalance", "available", "walletBalance", "balance"):
                            if fld in it:
                                try:
                                    return float(it.get(fld) or 0)
                                except Exception:
                                    pass
                        try:
                            return float(it.get("balance", 0) or 0)
                        except Exception:
                            return 0.0
                return 0.0
            except Exception as e:
                logger.exception("get_balance_usdt exception")
                return {"retCode": -1, "retMsg": str(e)}

        def fetch_ohlcv(self, symbol, interval="5", limit=200):
            return self._get("/v5/market/kline", params={"symbol": symbol, "interval": str(interval), "limit": int(limit)}, auth=False)

        def fetch_open_interest(self, symbol, interval="5", limit=200):
            return self._get("/v5/market/open-interest", params={"symbol": symbol, "interval": str(interval), "limit": int(limit)}, auth=False)

        def place_spot_order(self, side, qty, symbol):
            body = {"category":"spot","symbol":symbol,"side":side,"orderType":"Market","qty":str(qty)}
            body_str = json.dumps(body, separators=(",",":"), ensure_ascii=False)
            headers = self._sign_headers(body_str)
            try:
                r = self.sess.post(self.base+"/v5/order/create", json=body, headers=headers, timeout=12)
                try:
                    return r.json()
                except Exception:
                    return {"retCode": r.status_code, "retMsg": r.text}
            except Exception as e:
                logger.exception("place_spot_order exception")
                return {"retCode": -1, "retMsg": str(e)}

        def place_futures_order(self, side, qty, symbol, leverage=3, reduce_only=False):
            body = {"category":"linear","symbol":symbol,"side":side.capitalize(),"orderType":"Market","qty":str(qty),"reduceOnly": bool(reduce_only)}
            body_str = json.dumps(body, separators=(",",":"), ensure_ascii=False)
            headers = self._sign_headers(body_str)
            try:
                r = self.sess.post(self.base+"/v5/order/create", json=body, headers=headers, timeout=12)
                try:
                    return r.json()
                except Exception:
                    return {"retCode": r.status_code, "retMsg": r.text}
            except Exception as e:
                logger.exception("place_futures_order exception")
                return {"retCode": -1, "retMsg": str(e)}

    return Fallback(api_key, api_secret, testnet)

# ----------------- helpers for qty/position detection -----------------
def floor_qty(q, prec):
    try:
        if q <= 0:
            return 0.0
        f = 10 ** int(prec)
        return math.floor(float(q) * f) / f
    except Exception:
        return 0.0

def read_trades():
    _ensure_files()
    try:
        with open(TRADES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def has_open_spot(user_id, symbol):
    trades = read_trades()
    last_buy = None
    last_sell = None
    for t in trades:
        if str(t.get("user_id")) != str(user_id):
            continue
        if t.get("symbol") != symbol:
            continue
        if t.get("market_type") != "spot":
            continue
        side = (t.get("side") or "").lower()
        if side == "buy":
            last_buy = t
        if side == "sell":
            last_sell = t
    if last_buy and (not last_sell or last_buy.get("timestamp") > last_sell.get("timestamp")):
        return last_buy
    return None

def has_open_futures_short(user_id, symbol):
    trades = read_trades()
    last_open = None
    last_close = None
    for t in trades:
        if str(t.get("user_id")) != str(user_id):
            continue
        if t.get("symbol") != symbol:
            continue
        if t.get("market_type") != "futures":
            continue
        side = (t.get("side") or "").lower()
        # open short recorded as side="Sell" and action="open"
        if side == "sell" and t.get("action","") == "open":
            last_open = t
        if side == "buy" and t.get("action","") == "close":
            last_close = t
    if last_open and (not last_close or last_open.get("timestamp") > last_close.get("timestamp")):
        return last_open
    return None

def has_open_futures_long(user_id, symbol):
    trades = read_trades()
    last_open = None
    last_close = None
    for t in trades:
        if str(t.get("user_id")) != str(user_id):
            continue
        if t.get("symbol") != symbol:
            continue
        if t.get("market_type") != "futures":
            continue
        side = (t.get("side") or "").lower()
        # open long recorded as side="Buy" and action="open"
        if side == "buy" and t.get("action","") == "open":
            last_open = t
        if side == "sell" and t.get("action","") == "close":
            last_close = t
    if last_open and (not last_close or last_open.get("timestamp") > last_close.get("timestamp")):
        return last_open
    return None

# ----------------- normalize OHLCV to DataFrame -----------------
def normalize_ohlcv(raw):
    if raw is None:
        return None
    try:
        import pandas as pd
    except Exception:
        pd = None
    if pd is not None and hasattr(raw, "columns"):
        return raw
    items = None
    if isinstance(raw, dict):
        if raw.get("retCode") is not None and raw.get("retCode") != 0:
            logger.debug("normalize_ohlcv: retCode != 0 -> %s", raw.get("retMsg"))
        res = raw.get("result") or raw
        if isinstance(res, dict) and isinstance(res.get("list"), list):
            items = res.get("list")
        elif isinstance(res, list):
            items = res
        elif isinstance(res, dict):
            for key in ("list", "data", "rows", "candles"):
                if isinstance(res.get(key), list):
                    items = res.get(key)
                    break
    elif isinstance(raw, list):
        items = raw
    elif isinstance(raw, str):
        try:
            j = json.loads(raw)
            return normalize_ohlcv(j)
        except Exception:
            return None
    if not items or len(items) == 0:
        return None
    rows = []
    for it in items:
        if isinstance(it, dict):
            ts = it.get("t") or it.get("startTime") or it.get("time") or it.get("timestamp") or it.get("start_at")
            o = it.get("o") or it.get("open") or it.get("openPrice")
            h = it.get("h") or it.get("high") or it.get("highPrice")
            l = it.get("l") or it.get("low") or it.get("lowPrice")
            c = it.get("c") or it.get("close") or it.get("closePrice")
            v = it.get("v") or it.get("volume") or it.get("vol")
            rows.append((ts, o, h, l, c, v))
        elif isinstance(it, (list, tuple)) and len(it) >= 6:
            rows.append((it[0], it[1], it[2], it[3], it[4], it[5]))
    if not rows:
        return None
    if pd is None:
        return {"rows": rows}
    df = pd.DataFrame(rows, columns=["t","open","high","low","close","volume"])
    try:
        df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True, errors="coerce")
    except Exception:
        df["t"] = pd.to_datetime(df["t"], utc=True, errors="coerce")
    for col in ["open","high","low","close","volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["t"]).set_index("t").sort_index()
    return df

# ----------------- main analysis and trade logic -----------------
def analyze_and_trade_for_user(uid):
    users = load_users()
    if str(uid) not in users:
        return
    user = users[str(uid)]
    _ensure_user_defaults(users, uid)
    save_users(users)
    settings = user["settings"]
    if settings.get("DISABLED_AUTH"):
        logger.info("User %s disabled auth, skip", uid)
        return
    api_key = user.get("api_key","") or ""
    api_secret = user.get("api_secret","") or ""
    if not api_key or not api_secret:
        logger.info("User %s missing api keys, skip", uid)
        return
    testnet = bool(settings.get("TESTNET", True))
    c = make_client(api_key, api_secret, testnet)

    # read balance
    bal = None
    try:
        bal = c.get_balance_usdt()
        if isinstance(bal, dict) and (bal.get("retCode") or ""):
            logger.warning("User %s balance/auth issue: %s", uid, bal)
    except Exception:
        logger.exception("balance read error for %s", uid)

    balance_usd = 0.0
    try:
        if isinstance(bal, (int, float)):
            balance_usd = float(bal)
        elif isinstance(bal, dict):
            res = bal.get("result") or {}
            lst = res.get("list",[])
            for it in lst:
                if it.get("coin") == "USDT":
                    balance_usd = float(it.get("equity",0) or 0)
    except Exception:
        balance_usd = 0.0

    # symbols
    raw_symbols = settings.get("SYMBOLS") or settings.get("symbols")
    symbols = _normalize_symbols(raw_symbols) if raw_symbols is not None else _normalize_symbols(settings.get("SYMBOLS"))
    if not symbols:
        symbols = ["BTCUSDT"]
    timeframe = settings.get("TIMEFRAME","5")
    trade_mode = str(settings.get("TRADE_MODE","mixed")).lower()  # mixed / spot_only / futures_only

    for symbol in symbols:
        symbol = str(symbol).strip().upper()
        try:
            logger.info("User %s symbol %s fetching ohlcv", uid, symbol)
            raw = None
            if hasattr(c, "fetch_ohlcv"):
                raw = c.fetch_ohlcv(symbol, interval=timeframe, limit=200)
            else:
                raw = None

            preview = ""
            if raw is None:
                logger.warning("fetch_ohlcv returned None for %s %s", uid, symbol)
            else:
                try:
                    preview = raw if isinstance(raw, str) else json.dumps(raw)[:800]
                except Exception:
                    preview = str(raw)[:800]
                logger.info("fetch_ohlcv raw preview for %s %s: %s", uid, symbol, preview)

            df = normalize_ohlcv(raw)
            if df is None or (pd is not None and df.empty):
                logger.warning("No ohlcv for %s %s (normalize returned None/empty). raw_preview=%s", uid, symbol, preview)
                continue
            close = df["close"]

            # indicators & votes
            votes = {"buy":0,"sell":0}
            active = 0
            indicators = {}

            if settings.get("USE_RSI", True):
                try:
                    p = int(settings.get("RSI_PERIOD",14))
                    r = rsi_series(close, period=p)
                    lr = float(r.iloc[-1])
                    indicators["rsi"]=lr
                    if lr <= float(settings.get("RSI_OVERSOLD",40)):
                        votes["buy"]+=1
                    elif lr >= float(settings.get("RSI_OVERBOUGHT",60)):
                        votes["sell"]+=1
                    active+=1
                except Exception:
                    logger.exception("RSI fail")

            if settings.get("USE_EMA", True):
                try:
                    f = int(settings.get("FAST_MA",50))
                    s = int(settings.get("SLOW_MA",200))
                    ef = ema_series(close, f)
                    es = ema_series(close, s)
                    lf = float(ef.iloc[-1])
                    ls = float(es.iloc[-1])
                    indicators["ema_fast"]=lf
                    indicators["ema_slow"]=ls
                    if lf > ls:
                        votes["buy"]+=1
                    else:
                        votes["sell"]+=1
                    active+=1
                except Exception:
                    logger.exception("EMA fail")

            if settings.get("USE_MACD", True):
                try:
                    mf = int(settings.get("MACD_FAST",8))
                    ms = int(settings.get("MACD_SLOW",21))
                    sig = int(settings.get("MACD_SIGNAL",5))
                    _,_,hist = macd_hist_series(close, fast=mf, slow=ms, signal=sig)
                    hlast = float(hist.iloc[-1])
                    indicators["macd_hist"]=hlast
                    if hlast > 0:
                        votes["buy"]+=1
                    else:
                        votes["sell"]+=1
                    active+=1
                except Exception:
                    logger.exception("MACD fail")

            if settings.get("USE_OI", False):
                try:
                    oi_raw = c.fetch_open_interest(symbol, interval=timeframe, limit=50) if hasattr(c, "fetch_open_interest") else None
                    if oi_raw and isinstance(oi_raw, dict):
                        res = oi_raw.get("result") or {}
                        lst = res.get("list") or []
                        if pd is not None and lst:
                            srs = []
                            for it in lst:
                                val = it.get("open_interest") or it.get("oi") or it.get("openInterest")
                                srs.append(float(val or 0))
                            if len(srs) >= 2:
                                pct = (srs[-1]-srs[0]) / max(srs[0],1e-9) * 100.0
                                indicators["oi_pct"]=pct
                                if pct >= float(settings.get("OI_MIN_CHANGE_PCT",5.0)):
                                    votes["buy"]+=1
                                elif pct <= -float(settings.get("OI_MIN_CHANGE_PCT",5.0)):
                                    votes["sell"]+=1
                                active+=1
                except Exception:
                    logger.exception("OI fail")

            if active == 0:
                logger.info("No active indicators for %s %s", uid, symbol)
                continue

            buy_ratio = votes["buy"]/active
            sell_ratio = votes["sell"]/active
            logger.info("User %s %s votes %s active %d buy_ratio %.2f sell_ratio %.2f", uid, symbol, votes, active, buy_ratio, sell_ratio)

            buy_threshold = float(settings.get("BUY_CONFIRMATION_RATIO",0.66))
            sell_threshold = float(settings.get("SELL_CONFIRMATION_RATIO",0.33))

            # positions detection:
            spot_pos = has_open_spot(uid, symbol)
            short_pos = has_open_futures_short(uid, symbol)
            long_pos = has_open_futures_long(uid, symbol)

            price = float(close.iloc[-1])
            timestamp = datetime.utcnow().isoformat()

            # determine order USD size
            def compute_order_usd():
                order_usd = float(settings.get("ORDER_SIZE_USD",0.0) or 0.0)
                if order_usd <= 0:
                    return balance_usd * (float(settings.get("ORDER_PERCENT",10.0))/100.0)
                return order_usd

            # Helper to place futures order with long/short semantics depending on reduce_only flag
            def place_futures_open(side, qty, lev):
                if hasattr(c, "set_leverage"):
                    try:
                        c.set_leverage(symbol, lev)
                    except Exception:
                        logger.debug("set_leverage failed or not supported")
                return c.place_futures_order(side, qty, symbol, leverage=lev, reduce_only=False)

            def place_futures_close(side, qty, lev):
                # close uses reduce_only True and side is the closing side (Buy to close short, Sell to close long)
                return c.place_futures_order(side, qty, symbol, leverage=lev, reduce_only=True)

            # ----------------- Trading behavior by TRADE_MODE -----------------
            if trade_mode == "futures_only":
                # Use futures for BOTH long and short
                # BUY signal -> close short if exists else open long
                if buy_ratio >= buy_threshold:
                    order_usd = compute_order_usd()
                    if order_usd <= 0:
                        logger.warning("No capital for user %s", uid)
                    else:
                        qty = order_usd / price if price>0 else 0
                        qty = floor_qty(qty, int(settings.get("QTY_PRECISION",6)))
                        if qty * price < float(settings.get("MIN_NOTIONAL",5.0)):
                            logger.warning("Order below min notional for %s", uid)
                        else:
                            lev = int(settings.get("DEFAULT_LEVERAGE",3))
                            dry = bool(settings.get("DRY_RUN", False))
                            if short_pos:
                                # close short: buy reduce_only
                                if dry:
                                    logger.info("[DRY] FUTURES CLOSE SHORT (buy) user %s %s qty=%.8f price=%.2f", uid, symbol, qty, price)
                                    tr = {"user_id": uid, "symbol": symbol, "market_type":"futures", "side":"Buy", "action":"close", "qty":qty, "price":price, "timestamp":timestamp, "dry":True}
                                    append_trade(tr); notify_trade_to_user(user, tr)
                                else:
                                    res = place_futures_close("Buy", qty, lev)
                                    logger.info("Futures close short res: %s", res)
                                    tr = {"user_id": uid, "symbol": symbol, "market_type":"futures", "side":"Buy", "action":"close", "qty":qty, "price":price, "result":res, "timestamp":timestamp}
                                    append_trade(tr); notify_trade_to_user(user, tr)
                            else:
                                # open long: buy open
                                if dry:
                                    logger.info("[DRY] FUTURES OPEN LONG (buy) user %s %s qty=%.8f price=%.2f lev=%d", uid, symbol, qty, price, lev)
                                    tr = {"user_id": uid, "symbol": symbol, "market_type":"futures", "side":"Buy", "action":"open", "qty":qty, "price":price, "leverage":lev, "timestamp":timestamp, "dry":True}
                                    append_trade(tr); notify_trade_to_user(user, tr)
                                else:
                                    res = place_futures_open("Buy", qty, lev)
                                    logger.info("Futures open long res: %s", res)
                                    tr = {"user_id": uid, "symbol": symbol, "market_type":"futures", "side":"Buy", "action":"open", "qty":qty, "price":price, "leverage":lev, "result":res, "timestamp":timestamp}
                                    append_trade(tr); notify_trade_to_user(user, tr)

                # SELL signal -> close long if exists else open short
                if sell_ratio >= sell_threshold:
                    order_usd = compute_order_usd()
                    if order_usd <= 0:
                        logger.warning("No capital for user %s", uid)
                    else:
                        qty = order_usd / price if price>0 else 0
                        qty = floor_qty(qty, int(settings.get("QTY_PRECISION",6)))
                        if qty * price < float(settings.get("MIN_NOTIONAL",5.0)):
                            logger.warning("Order below min notional for %s", uid)
                        else:
                            lev = int(settings.get("DEFAULT_LEVERAGE",3))
                            dry = bool(settings.get("DRY_RUN", False))
                            if long_pos:
                                # close long: sell reduce_only
                                if dry:
                                    logger.info("[DRY] FUTURES CLOSE LONG (sell) user %s %s qty=%.8f price=%.2f", uid, symbol, qty, price)
                                    tr = {"user_id": uid, "symbol": symbol, "market_type":"futures", "side":"Sell", "action":"close", "qty":qty, "price":price, "timestamp":timestamp, "dry":True}
                                    append_trade(tr); notify_trade_to_user(user, tr)
                                else:
                                    res = place_futures_close("Sell", qty, lev)
                                    logger.info("Futures close long res: %s", res)
                                    tr = {"user_id": uid, "symbol": symbol, "market_type":"futures", "side":"Sell", "action":"close", "qty":qty, "price":price, "result":res, "timestamp":timestamp}
                                    append_trade(tr); notify_trade_to_user(user, tr)
                            else:
                                # open short: sell open
                                if dry:
                                    logger.info("[DRY] FUTURES OPEN SHORT user %s %s qty=%.8f price=%.2f lev=%d", uid, symbol, qty, price, lev)
                                    tr = {"user_id": uid, "symbol": symbol, "market_type":"futures", "side":"Sell", "action":"open", "qty":qty, "price":price, "leverage":lev, "timestamp":timestamp, "dry":True}
                                    append_trade(tr); notify_trade_to_user(user, tr)
                                else:
                                    if hasattr(c, "set_leverage"):
                                        try:
                                            c.set_leverage(symbol, lev)
                                        except Exception:
                                            logger.debug("set_leverage failed or not supported")
                                    res = place_futures_open("Sell", qty, lev)
                                    logger.info("Futures open short res: %s", res)
                                    tr = {"user_id": uid, "symbol": symbol, "market_type":"futures", "side":"Sell", "action":"open", "qty":qty, "price":price, "leverage":lev, "result":res, "timestamp":timestamp}
                                    append_trade(tr); notify_trade_to_user(user, tr)

            else:
                # MIXED or SPOT_ONLY behavior (existing logic)
                # BUY -> spot buy (unless TRADE_MODE == spot_only then same); if you want buy via futures for mixed, change config
                if buy_ratio >= buy_threshold and not spot_pos and trade_mode != "futures_only":
                    order_usd = compute_order_usd()
                    if order_usd <= 0:
                        logger.warning("No capital for user %s", uid)
                    else:
                        qty = order_usd / price if price>0 else 0
                        qty = floor_qty(qty, int(settings.get("QTY_PRECISION",6)))
                        if qty * price < float(settings.get("MIN_NOTIONAL",5.0)):
                            logger.warning("Order below min notional for %s", uid)
                        else:
                            dry = bool(settings.get("DRY_RUN", False))
                            if dry:
                                logger.info("[DRY] Spot BUY user %s %s qty=%.8f price=%.2f", uid, symbol, qty, price)
                                tr = {"user_id": uid, "symbol": symbol, "market_type":"spot", "side":"Buy", "qty":qty, "price":price, "timestamp":timestamp, "dry":True}
                                append_trade(tr); notify_trade_to_user(user, tr)
                            else:
                                if hasattr(c, "place_spot_order"):
                                    res = c.place_spot_order("Buy", qty, symbol)
                                else:
                                    res = None
                                logger.info("Spot buy result: %s", res)
                                tr = {"user_id": uid, "symbol": symbol, "market_type":"spot", "side":"Buy", "qty":qty, "price":price, "result":res, "timestamp":timestamp}
                                append_trade(tr); notify_trade_to_user(user, tr)

                # CLOSE SPOT
                if sell_ratio >= sell_threshold and spot_pos and trade_mode != "futures_only":
                    qty = float(spot_pos.get("qty",0) or 0)
                    if qty <= 0:
                        logger.warning("Cannot determine spot qty to close for %s", uid)
                    else:
                        dry = bool(settings.get("DRY_RUN", False))
                        if dry:
                            logger.info("[DRY] Spot SELL (close) user %s %s qty=%.8f price=%.2f", uid, symbol, qty, price)
                            tr = {"user_id": uid, "symbol": symbol, "market_type":"spot", "side":"Sell", "qty":qty, "price":price, "timestamp":timestamp, "dry":True}
                            append_trade(tr); notify_trade_to_user(user, tr)
                        else:
                            res = c.place_spot_order("Sell", qty, symbol)
                            logger.info("Spot sell result: %s", res)
                            tr = {"user_id": uid, "symbol": symbol, "market_type":"spot", "side":"Sell", "qty":qty, "price":price, "result":res, "timestamp":timestamp}
                            append_trade(tr); notify_trade_to_user(user, tr)

                # SHORTS via futures (same as before)
                if sell_ratio >= sell_threshold and settings.get("ENABLE_SHORTS", True) and not short_pos:
                    order_usd = compute_order_usd()
                    if order_usd <= 0:
                        logger.warning("No capital for futures short user %s", uid)
                    else:
                        lev = int(settings.get("DEFAULT_LEVERAGE",3))
                        qty = order_usd / price if price>0 else 0
                        qty = floor_qty(qty, int(settings.get("QTY_PRECISION",6)))
                        if qty * price < float(settings.get("MIN_NOTIONAL",5.0)):
                            logger.warning("Futures order below min notional for %s", uid)
                        else:
                            dry = bool(settings.get("DRY_RUN", False))
                            if dry:
                                logger.info("[DRY] FUTURES OPEN SHORT user %s %s qty=%.8f price=%.2f lev=%d", uid, symbol, qty, price, lev)
                                tr = {"user_id": uid, "symbol": symbol, "market_type":"futures", "side":"Sell", "action":"open", "qty":qty, "price":price, "leverage":lev, "timestamp":timestamp, "dry":True}
                                append_trade(tr); notify_trade_to_user(user, tr)
                            else:
                                if hasattr(c, "set_leverage"):
                                    try:
                                        c.set_leverage(symbol, lev)
                                    except Exception:
                                        logger.debug("set_leverage failed or not supported")
                                res = c.place_futures_order("Sell", qty, symbol, leverage=lev, reduce_only=False)
                                logger.info("Futures open short res: %s", res)
                                tr = {"user_id": uid, "symbol": symbol, "market_type":"futures", "side":"Sell", "action":"open", "qty":qty, "price":price, "leverage":lev, "result":res, "timestamp":timestamp}
                                append_trade(tr); notify_trade_to_user(user, tr)

                # CLOSE SHORT (buy to close)
                if buy_ratio >= buy_threshold and short_pos:
                    qty = float(short_pos.get("qty",0) or 0)
                    if qty <= 0:
                        logger.warning("Cannot determine futures qty to close for %s", uid)
                    else:
                        dry = bool(settings.get("DRY_RUN", False))
                        if dry:
                            logger.info("[DRY] FUTURES CLOSE SHORT (buy to close) user %s %s qty=%.8f price=%.2f", uid, symbol, qty, price)
                            tr = {"user_id": uid, "symbol": symbol, "market_type":"futures", "side":"Buy", "action":"close", "qty":qty, "price":price, "timestamp":timestamp, "dry":True}
                            append_trade(tr); notify_trade_to_user(user, tr)
                        else:
                            res = c.place_futures_order("Buy", qty, symbol, leverage=int(settings.get("DEFAULT_LEVERAGE",3)), reduce_only=True)
                            logger.info("Futures close short res: %s", res)
                            tr = {"user_id": uid, "symbol": symbol, "market_type":"futures", "side":"Buy", "action":"close", "qty":qty, "price":price, "result":res, "timestamp":timestamp}
                            append_trade(tr); notify_trade_to_user(user, tr)

        except Exception:
            logger.exception("Symbol loop error for user %s symbol %s", uid, symbol)

def run_once():
    users = load_users()
    if not isinstance(users, dict):
        logger.error("users.json invalid")
        return
    changed = False
    for uid in list(users.keys()):
        before = dict(users.get(uid, {}))
        _ensure_user_defaults(users, uid)
        if users.get(uid) != before:
            changed = True
    if changed:
        save_users(users)
    for uid in list(users.keys()):
        try:
            analyze_and_trade_for_user(uid)
        except Exception:
            logger.exception("User loop error for %s", uid)

# CLI diag
def diag(uid):
    users = load_users()
    u = users.get(str(uid))
    if not u:
        print("User not found:", uid)
        return
    s = u.get("settings", {})
    print("User:", uid)
    print("TESTNET:", s.get("TESTNET"))
    print("Masked API key:", mask_key(u.get("api_key","")))
    print("DRY_RUN:", s.get("DRY_RUN"))
    print("SYMBOLS:", s.get("SYMBOLS"))
    print("TRADE_MODE:", s.get("TRADE_MODE"))
    try:
        c = make_client(u.get("api_key",""), u.get("api_secret",""), bool(s.get("TESTNET",True)))
        bal = c.get_balance_usdt()
        print("Balance:", bal)
        if hasattr(c, "fetch_ohlcv"):
            r = c.fetch_ohlcv("BTCUSDT", interval=str(s.get("TIMEFRAME","5")), limit=5)
            print("KLINE preview type:", type(r))
            try:
                import json as _j
                print("KLINE preview:", _j.dumps(r)[:800])
            except Exception:
                print("KLINE preview (raw):", str(r)[:800])
    except Exception as e:
        print("Diag client error:", e)

if __name__ == "__main__":
    _ensure_files()
    if len(sys.argv)>=2 and sys.argv[1]=="diag":
        if len(sys.argv)>=3:
            diag(sys.argv[2])
        else:
            print("Usage: python trading_core.py diag <UID>")
        sys.exit(0)
    if len(sys.argv)>=2 and sys.argv[1]=="loop":
        sec=60
        try:
            if len(sys.argv)>=3:
                sec=int(sys.argv[2])
        except:
            pass
        logger.info("Starting loop mode: interval %s sec", sec)
        while True:
            try:
                run_once()
            except Exception:
                logger.exception("run_once crashed")
            time.sleep(max(1, sec))
    else:
        run_once()
        logger.info("Run complete.")
