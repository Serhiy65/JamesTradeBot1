import time, json, hmac, hashlib, logging
import requests

logger = logging.getLogger("client")

class BybitClient:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.testnet = bool(testnet)
        self.base = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"
        self.sess = requests.Session()
        self.recv_window = 5000

        self.account_mode = None  # "UTA" or "CLASSIC"

    # -------------------- SIGN --------------------
    def _ts(self):
        return str(int(time.time() * 1000))

    def _sign(self, payload: str):
        return hmac.new(
            self.api_secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()

    def _headers(self, body=""):
        ts = self._ts()
        payload = ts + self.api_key + str(self.recv_window) + body
        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": str(self.recv_window),
            "X-BAPI-SIGN": self._sign(payload)
        }

    # -------------------- MODE DETECT --------------------
    def detect_mode(self):
        """Определяет UTA или Classic через v5 balance."""
        url = self.base + "/v5/account/wallet-balance"
        params = {"accountType": "UNIFIED", "coin": "USDT"}
        try:
            r = self.sess.get(url, params=params, headers=self._headers(""), timeout=10)
            data = r.json()
        except:
            self.account_mode = "CLASSIC"
            return

        if data.get("retCode") == 0:
            self.account_mode = "UTA"
        else:
            self.account_mode = "CLASSIC"

        logger.info(f"[client] autodetect account mode = {self.account_mode}")

    # -------------------- PUBLIC --------------------
    def fetch_ohlcv(self, symbol, interval="5", limit=200):
        url = self.base + "/v5/market/kline"
        r = self.sess.get(url, params={"symbol": symbol, "interval": str(interval), "limit": limit}, timeout=10)
        try:
            return r.json()
        except:
            return None

    def fetch_open_interest(self, symbol, interval="5", limit=200):
        url = self.base + "/v5/market/open-interest"
        r = self.sess.get(url, params={"symbol": symbol, "interval": str(interval), "limit": limit}, timeout=10)
        try:
            return r.json()
        except:
            return None

    # -------------------- BALANCE --------------------
    def get_balance_usdt(self):
        if self.account_mode is None:
            self.detect_mode()

        if self.account_mode == "UTA":
            url = self.base + "/v5/account/wallet-balance"
            params = {"accountType": "UNIFIED", "coin": "USDT"}
            r = self.sess.get(url, params=params, headers=self._headers(""), timeout=10)
            data = r.json()
            if data.get("retCode") != 0:
                return data
            lst = (data.get("result") or {}).get("list", [])
            for it in lst:
                if it.get("coin") == "USDT":
                    return float(it.get("equity", 0))
            return 0.0

        # CLASSIC → futures v3 + spot v3
        # Try futures USDT first
        url = self.base + "/contract/v3/private/account/wallet/balance"
        r = self.sess.get(url, params={"coin": "USDT"}, headers=self._headers(""), timeout=10)
        try:
            data = r.json()
        except:
            return 0.0

        if data.get("retCode") == 0:
            return float((data.get("result") or {}).get("walletBalance", 0))

        # fallback spot
        url = self.base + "/spot/v3/private/account"
        r = self.sess.get(url, headers=self._headers(""), timeout=10)
        try:
            data = r.json()
        except:
            return 0.0

        if data.get("retCode") == 0:
            for it in (data.get("result") or {}).get("balances", []):
                if it.get("coin") == "USDT":
                    return float(it.get("free", 0))
        return 0.0

    # -------------------- SPOT ORDER --------------------
    def place_spot_order(self, side, qty, symbol):
        if self.account_mode is None:
            self.detect_mode()

        if self.account_mode == "UTA":
            body = {
                "category": "spot",
                "symbol": symbol,
                "side": side,
                "orderType": "Market",
                "qty": str(qty)
            }
            body_str = json.dumps(body, separators=(",", ":"))
            headers = self._headers(body_str)
            r = self.sess.post(self.base + "/v5/order/create", json=body, headers=headers, timeout=10)
            return r.json()

        # CLASSIC → SPOT v3
        url = self.base + "/spot/v3/private/order"
        params = {
            "symbol": symbol,
            "side": side.lower(),
            "type": "market",
            "qty": str(qty)
        }
        qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
        headers = self._headers(qs)
        r = self.sess.post(url, params=params, headers=headers, timeout=10)
        try:
            return r.json()
        except:
            return {"retCode": -1, "retMsg": "no json"}

    # -------------------- FUTURES ORDER --------------------
    def place_futures_order(self, side, qty, symbol, leverage=3, reduce_only=False):
        if self.account_mode is None:
            self.detect_mode()

        if self.account_mode == "UTA":
            body = {
                "category": "linear",
                "symbol": symbol,
                "side": side.capitalize(),
                "orderType": "Market",
                "qty": str(qty),
                "reduceOnly": bool(reduce_only)
            }
            b = json.dumps(body, separators=(",", ":"))
            headers = self._headers(b)
            r = self.sess.post(self.base + "/v5/order/create", json=body, headers=headers, timeout=10)
            return r.json()
 
        # CLASSIC → FUTURES v3
        body = {
            "symbol": symbol,
            "side": side.upper(),
            "orderType": "Market",
            "qty": str(qty),
            "reduceOnly": reduce_only
        }
        b = json.dumps(body, separators=(",", ":"))
        headers = self._headers(b)
        r = self.sess.post(self.base + "/contract/v3/private/order/create", json=body, headers=headers, timeout=10)
        try:
            return r.json()
        except:
            return {"retCode": -1, "retMsg": "no json"}
