"""
Integracao com a API da MEXC: autenticacao, saldo, posicoes abertas e candles.
"""
import time
import hmac
import hashlib
import threading
import requests
import pandas as pd

from config import MEXC_BASE, MEXC_SPOT_BASE, TF_MAP, LIMITE_CANDLES, PARES_ESPECIAIS

_cache      = {}
_cache_ttl  = 60
_cache_lock = threading.Lock()


# ─────────────────────────────────────────────
# 🔐  AUTENTICAÇÃO MEXC
# ─────────────────────────────────────────────

def get_futures_balance(api_key, api_secret):
    path = "/api/v1/private/account/assets"
    ts   = str(int(time.time() * 1000))
    raw  = api_key + ts
    sig  = hmac.new(api_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    headers = {"ApiKey": api_key, "Request-Time": ts, "Signature": sig, "Content-Type": "application/json"}
    try:
        r = requests.get(MEXC_BASE + path, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("success"):
            assets = data.get("data", [])
            usdt = next((a for a in assets if a.get("currency") == "USDT"), None)
            if usdt:
                return {
                    "disponivel": float(usdt.get("availableBalance", 0)),
                    "total":      float(usdt.get("equity", 0)),
                    "em_uso":     float(usdt.get("positionMargin", 0)),
                    "pnl":        float(usdt.get("unrealisedPnl", 0)),
                }
        return {"erro": data.get("message", "Resposta inesperada")}
    except Exception as e:
        return {"erro": str(e)}


def get_open_positions(api_key, api_secret):
    path = "/api/v1/private/position/open_positions"
    ts   = str(int(time.time() * 1000))
    raw  = api_key + ts
    sig  = hmac.new(api_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    headers = {"ApiKey": api_key, "Request-Time": ts, "Signature": sig, "Content-Type": "application/json"}
    try:
        r = requests.get(MEXC_BASE + path, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("success"):
            posicoes = []
            for p in data.get("data", []):
                posicoes.append({
                    "par":        p.get("symbol", ""),
                    "lado":       "LONG" if p.get("positionType") == 1 else "SHORT",
                    "tamanho":    float(p.get("vol", 0)),
                    "entrada":    float(p.get("openAvgPrice", 0)),
                    "pnl":        float(p.get("unrealisedPnl", 0)),
                    "margem":     float(p.get("im", 0)),
                    "alavancagem": int(p.get("leverage", 10)),
                })
            return posicoes
        return []
    except Exception as e:
        print(f"Erro posicoes: {e}")
        return []


def validate_api_keys(api_key, api_secret):
    result = get_futures_balance(api_key, api_secret)
    return "erro" not in result


# ─────────────────────────────────────────────
# 📡  MEXC - candles
# ─────────────────────────────────────────────

def normalizar_par(par):
    """Limpa e padroniza o nome do par digitado pelo usuario (ex: 'spcx' -> 'SPCXUSDT')."""
    par = par.upper().strip().replace("_", "").replace(" ", "")
    if not par.endswith("USDT"):
        par = par + "USDT"
    return par


INTERVALO_CONTRATO = {
    "15m": "Min15", "1h": "Hour1", "60m": "Hour1", "4h": "Hour4",
}

CONTRACT_BASES = ["https://api.mexc.com", MEXC_BASE]


def _buscar_candles_contrato(par_real, intervalo):
    """Fallback: busca candles na API de contratos quando o par nao existe na spot."""
    base = par_real[:-4] if par_real.endswith("USDT") else par_real
    simbolo_contrato = PARES_ESPECIAIS.get(base, f"{base}_USDT")
    interval_api = INTERVALO_CONTRATO.get(intervalo, "Min15")

    for contract_base in CONTRACT_BASES:
        url = f"{contract_base}/api/v1/contract/kline/{simbolo_contrato}"
        params = {"interval": interval_api}
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            raw = r.json()
            if not raw.get("success") or not raw.get("data"):
                continue
            d = raw["data"]
            if not d.get("time"):
                continue
            df = pd.DataFrame({
                "timestamp": [t * 1000 for t in d["time"]],
                "open":   d["open"],
                "high":   d["high"],
                "low":    d["low"],
                "close":  d["close"],
                "volume": d["vol"],
            })
            return df.tail(LIMITE_CANDLES).reset_index(drop=True)
        except Exception as e:
            print(f"Candles contrato {simbolo_contrato} {intervalo} via {contract_base}: {e}")
            continue
    return None


def buscar_candles(par, intervalo, limite=None):
    """
    Busca candles da MEXC para um par e intervalo.

    limite: numero de candles a buscar. Usa LIMITE_CANDLES por padrao.
      Passe um valor maior (ex: 250) para timeframes onde indicadores de
      longo periodo sao calculados, como EMA200 no 1D.
    """
    par_real = normalizar_par(par)
    lim = limite or LIMITE_CANDLES
    # Inclui o limite na chave de cache quando diferente do padrao,
    # para nao misturar datasets de tamanhos diferentes.
    key = f"{par_real}_{intervalo}" if lim == LIMITE_CANDLES else f"{par_real}_{intervalo}_{lim}"
    now = time.time()
    with _cache_lock:
        if key in _cache:
            df_c, ts = _cache[key]
            if now - ts < _cache_ttl:
                return df_c

    intervalo_api = TF_MAP.get(intervalo, intervalo)
    url    = f"{MEXC_SPOT_BASE}/api/v3/klines"
    params = {"symbol": par_real, "interval": intervalo_api, "limit": lim}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        raw = r.json()
        if not raw or not isinstance(raw, list):
            df = _buscar_candles_contrato(par_real, intervalo)
            if df is not None:
                with _cache_lock:
                    _cache[key] = (df, now)
                return df
            return None
        ncols = len(raw[0]) if raw else 0
        if ncols >= 12:
            cols = ["timestamp","open","high","low","close","volume",
                    "close_time","quote_volume","trades",
                    "taker_buy_base","taker_buy_quote","ignore"]
        else:
            cols = ["timestamp","open","high","low","close","volume","close_time","quote_volume"]
        df = pd.DataFrame(raw, columns=cols[:ncols])
        for c in ["open","high","low","close","volume"]:
            df[c] = pd.to_numeric(df[c])
        df["timestamp"] = pd.to_numeric(df["timestamp"])
        with _cache_lock:
            _cache[key] = (df, now)
        return df
    except Exception as e:
        print(f"Candles {par_real} {intervalo}: {e}")
        df = _buscar_candles_contrato(par_real, intervalo)
        if df is not None:
            with _cache_lock:
                _cache[key] = (df, now)
        return df
