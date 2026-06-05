"""
🤖 Crypto Signal Bot — MEXC Futures
- Autenticação via API Key/Secret
- Gestão de risco: 4/5 → 5% · 5/5 → 10% · Alavancagem 10x
- Stop Loss automático (topos/fundos) · Take Profit 3:1
- Máximo 2 trades simultâneos
- 3 timeframes: 15m, 1h, 4h
- Indicadores: RSI, EMA 9/21, MACD, Volume, Topos/Fundos (4/5 para sinal)
"""

import time
import hmac
import hashlib
import threading
import requests
import pandas as pd
import numpy as np
import ta
from datetime import datetime
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ─────────────────────────────────────────────
# ⚙️  CONFIGURAÇÕES
# ─────────────────────────────────────────────

PARES = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT"]
TIMEFRAMES = ["15m", "1h", "4h"]
LIMITE_CANDLES = 120

RSI_SOBREVENDIDO  = 35
RSI_SOBRECOMPRADO = 65
VOLUME_MULT       = 1.4   # Volume > 1.4x média = confirmação
MINIMO_CONF       = 4     # Mínimo de confirmações para sinal

ALAVANCAGEM       = 10
RISCO_ARRISCADO   = 0.05  # 5%  → 4/5 confirmações
RISCO_SEGURO      = 0.10  # 10% → 5/5 confirmações
TP_RATIO          = 3.0   # Take Profit = 3x o Stop Loss
MAX_TRADES        = 2     # Máximo de trades simultâneos

MEXC_BASE         = "https://contract.mexc.com"
MEXC_SPOT_BASE    = "https://api.mexc.com"

# Cache de candles
_cache     = {}
_cache_ttl = 60
_cache_lock = threading.Lock()


# ─────────────────────────────────────────────
# 🔐  AUTENTICAÇÃO MEXC
# ─────────────────────────────────────────────

def _sign(api_secret: str, params: str) -> str:
    return hmac.new(
        api_secret.encode("utf-8"),
        params.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def _headers(api_key: str, api_secret: str, params_str: str = "") -> dict:
    ts = str(int(time.time() * 1000))
    raw = api_key + ts + params_str
    sig = hmac.new(api_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return {
        "ApiKey":      api_key,
        "Request-Time": ts,
        "Signature":   sig,
        "Content-Type": "application/json",
    }


def get_futures_balance(api_key: str, api_secret: str) -> dict:
    """Busca saldo disponível na conta Futures da MEXC."""
    path = "/api/v1/private/account/assets"
    ts   = str(int(time.time() * 1000))
    raw  = api_key + ts
    sig  = hmac.new(api_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    headers = {
        "ApiKey": api_key,
        "Request-Time": ts,
        "Signature": sig,
        "Content-Type": "application/json",
    }
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


def get_open_positions(api_key: str, api_secret: str) -> list:
    """Busca posições abertas no Futures."""
    path = "/api/v1/private/position/open_positions"
    ts   = str(int(time.time() * 1000))
    raw  = api_key + ts
    sig  = hmac.new(api_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    headers = {
        "ApiKey": api_key,
        "Request-Time": ts,
        "Signature": sig,
        "Content-Type": "application/json",
    }
    try:
        r = requests.get(MEXC_BASE + path, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("success"):
            posicoes = []
            for p in data.get("data", []):
                posicoes.append({
                    "par":        p.get("symbol",""),
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
        print(f"Erro posições: {e}")
        return []


def validate_api_keys(api_key: str, api_secret: str) -> bool:
    """Verifica se as chaves são válidas."""
    result = get_futures_balance(api_key, api_secret)
    return "erro" not in result


# ─────────────────────────────────────────────
# 📡  MEXC — candles (público)
# ─────────────────────────────────────────────

def buscar_candles(par: str, intervalo: str) -> pd.DataFrame | None:
    key = f"{par}_{intervalo}"
    now = time.time()
    with _cache_lock:
        if key in _cache:
            df_c, ts = _cache[key]
            if now - ts < _cache_ttl:
                return df_c

    url    = f"{MEXC_SPOT_BASE}/api/v3/klines"
    params = {"symbol": par, "interval": intervalo, "limit": LIMITE_CANDLES}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        raw = r.json()
        df  = pd.DataFrame(raw, columns=[
            "timestamp","open","high","low","close","volume",
            "close_time","quote_volume","trades",
            "taker_buy_base","taker_buy_quote","ignore"
        ])
        for c in ["open","high","low","close","volume"]:
            df[c] = pd.to_numeric(df[c])
        df["timestamp"] = pd.to_numeric(df["timestamp"])
        with _cache_lock:
            _cache[key] = (df, now)
        return df
    except Exception as e:
        print(f"Candles {par} {intervalo}: {e}")
        return None


# ─────────────────────────────────────────────
# 📊  TOPOS E FUNDOS
# ─────────────────────────────────────────────

def detectar_niveis(df: pd.DataFrame, janela: int = 5) -> dict:
    highs  = df["high"].values
    lows   = df["low"].values
    close  = df["close"].values
    preco  = close[-1]
    topos  = []
    fundos = []

    for i in range(janela, len(highs) - janela):
        if highs[i] == max(highs[i - janela:i + janela + 1]):
            topos.append(highs[i])
        if lows[i] == min(lows[i - janela:i + janela + 1]):
            fundos.append(lows[i])

    topos_rec  = sorted(topos[-6:],  reverse=True)[:3] if topos  else []
    fundos_rec = sorted(fundos[-6:])[:3]               if fundos else []

    margem = 0.015
    perto_suporte    = any(abs(preco - f) / f <= margem for f in fundos_rec)
    perto_resistencia = any(abs(preco - t) / t <= margem for t in topos_rec)

    if perto_suporte:
        sinal = "LONG"
        desc  = f"Próximo de suporte ${fundos_rec[0]:,.4f}"
    elif perto_resistencia:
        sinal = "SHORT"
        desc  = f"Próximo de resistência ${topos_rec[0]:,.4f}"
    else:
        sinal = "NEUTRO"
        desc  = "Sem nível relevante próximo"

    # SL baseado no nível mais próximo
    sl_long  = fundos_rec[0] * 0.995 if fundos_rec  else preco * 0.98
    sl_short = topos_rec[0]  * 1.005 if topos_rec   else preco * 1.02

    return {
        "sinal": sinal, "desc": desc,
        "topos": topos_rec, "fundos": fundos_rec,
        "sl_long": sl_long, "sl_short": sl_short,
    }


# ─────────────────────────────────────────────
# 📊  INDICADORES
# ─────────────────────────────────────────────

def calcular_indicadores(df: pd.DataFrame) -> dict:
    close  = df["close"]
    volume = df["volume"]

    rsi_val = ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1]

    macd_obj  = ta.trend.MACD(close)
    macd_hist = macd_obj.macd_diff()
    mh_atual  = macd_hist.iloc[-1]
    mh_prev   = macd_hist.iloc[-2]

    ema9  = ta.trend.EMAIndicator(close, window=9).ema_indicator().iloc[-1]
    ema21 = ta.trend.EMAIndicator(close, window=21).ema_indicator().iloc[-1]

    vol_atual = volume.iloc[-1]
    vol_media = volume.iloc[-20:].mean()
    vol_ratio = vol_atual / vol_media if vol_media > 0 else 1.0

    niveis = detectar_niveis(df)

    return {
        "preco":      close.iloc[-1],
        "rsi":        rsi_val,
        "macd_hist":  mh_atual,
        "macd_prev":  mh_prev,
        "ema9":       ema9,
        "ema21":      ema21,
        "vol_ratio":  vol_ratio,
        "vol_atual":  vol_atual,
        "vol_media":  vol_media,
        "niveis":     niveis,
        "closes":     close.iloc[-30:].tolist(),
        "timestamps": df["timestamp"].iloc[-30:].tolist(),
    }


# ─────────────────────────────────────────────
# 🧠  LÓGICA DE SINAIS
# ─────────────────────────────────────────────

def analisar_sinal(ind: dict) -> dict:
    conf_long  = []
    conf_short = []
    detalhes   = []

    # 1. RSI
    if ind["rsi"] < RSI_SOBREVENDIDO:
        conf_long.append("RSI")
        detalhes.append({"nome":"RSI","valor":f"{ind['rsi']:.1f}","sinal":"LONG","desc":f"RSI sobrevendido ({ind['rsi']:.1f})"})
    elif ind["rsi"] > RSI_SOBRECOMPRADO:
        conf_short.append("RSI")
        detalhes.append({"nome":"RSI","valor":f"{ind['rsi']:.1f}","sinal":"SHORT","desc":f"RSI sobrecomprado ({ind['rsi']:.1f})"})
    else:
        detalhes.append({"nome":"RSI","valor":f"{ind['rsi']:.1f}","sinal":"NEUTRO","desc":f"RSI neutro ({ind['rsi']:.1f})"})

    # 2. EMA
    if ind["ema9"] > ind["ema21"]:
        conf_long.append("EMA")
        detalhes.append({"nome":"EMA","valor":"9>21","sinal":"LONG","desc":f"EMA9>${ind['ema9']:,.2f} > EMA21${ind['ema21']:,.2f}"})
    else:
        conf_short.append("EMA")
        detalhes.append({"nome":"EMA","valor":"9<21","sinal":"SHORT","desc":f"EMA9${ind['ema9']:,.2f} < EMA21${ind['ema21']:,.2f}"})

    # 3. MACD
    if ind["macd_hist"] > 0 and ind["macd_prev"] <= 0:
        conf_long.append("MACD")
        detalhes.append({"nome":"MACD","valor":f"{ind['macd_hist']:.4f}","sinal":"LONG","desc":"MACD cruzou para cima (bullish)"})
    elif ind["macd_hist"] < 0 and ind["macd_prev"] >= 0:
        conf_short.append("MACD")
        detalhes.append({"nome":"MACD","valor":f"{ind['macd_hist']:.4f}","sinal":"SHORT","desc":"MACD cruzou para baixo (bearish)"})
    elif ind["macd_hist"] > 0:
        conf_long.append("MACD")
        detalhes.append({"nome":"MACD","valor":f"{ind['macd_hist']:.4f}","sinal":"LONG","desc":"MACD histograma positivo"})
    else:
        conf_short.append("MACD")
        detalhes.append({"nome":"MACD","valor":f"{ind['macd_hist']:.4f}","sinal":"SHORT","desc":"MACD histograma negativo"})

    # 4. Volume
    if ind["vol_ratio"] >= VOLUME_MULT:
        direcao_vol = "LONG" if len(conf_long) >= len(conf_short) else "SHORT"
        if direcao_vol == "LONG":
            conf_long.append("Volume")
        else:
            conf_short.append("Volume")
        detalhes.append({"nome":"Volume","valor":f"{ind['vol_ratio']:.1f}x","sinal":direcao_vol,"desc":f"Volume {ind['vol_ratio']:.1f}x acima da média"})
    else:
        detalhes.append({"nome":"Volume","valor":f"{ind['vol_ratio']:.1f}x","sinal":"NEUTRO","desc":f"Volume {ind['vol_ratio']:.1f}x da média (fraco)"})

    # 5. Topos/Fundos
    tf_sinal = ind["niveis"]["sinal"]
    if tf_sinal == "LONG":
        conf_long.append("Níveis")
        detalhes.append({"nome":"Níveis","valor":"Suporte","sinal":"LONG","desc":ind["niveis"]["desc"]})
    elif tf_sinal == "SHORT":
        conf_short.append("Níveis")
        detalhes.append({"nome":"Níveis","valor":"Resistência","sinal":"SHORT","desc":ind["niveis"]["desc"]})
    else:
        detalhes.append({"nome":"Níveis","valor":"Neutro","sinal":"NEUTRO","desc":ind["niveis"]["desc"]})

    n_long  = len(conf_long)
    n_short = len(conf_short)
    preco   = ind["preco"]

    if n_long >= MINIMO_CONF and n_long > n_short:
        direcao = "LONG"
        forca   = n_long
        # SL baseado no fundo mais próximo
        sl = ind["niveis"]["sl_long"]
        distancia_sl = abs(preco - sl)
        tp = preco + distancia_sl * TP_RATIO
        risco_pct = RISCO_SEGURO if forca == 5 else RISCO_ARRISCADO
        classificacao = "SEGURO" if forca == 5 else "ARRISCADO"
    elif n_short >= MINIMO_CONF and n_short > n_long:
        direcao = "SHORT"
        forca   = n_short
        sl = ind["niveis"]["sl_short"]
        distancia_sl = abs(sl - preco)
        tp = preco - distancia_sl * TP_RATIO
        risco_pct = RISCO_SEGURO if forca == 5 else RISCO_ARRISCADO
        classificacao = "SEGURO" if forca == 5 else "ARRISCADO"
    else:
        direcao = "NEUTRO"
        forca   = max(n_long, n_short)
        sl = tp = None
        distancia_sl = 0
        risco_pct = 0
        classificacao = "NEUTRO"

    return {
        "direcao":       direcao,
        "forca":         forca,
        "n_long":        n_long,
        "n_short":       n_short,
        "detalhes":      detalhes,
        "sl":            round(sl, 6) if sl else None,
        "tp":            round(tp, 6) if tp else None,
        "distancia_sl":  round(distancia_sl, 6),
        "risco_pct":     risco_pct,
        "classificacao": classificacao,
    }


# ─────────────────────────────────────────────
# 💰  GESTÃO DE RISCO — tamanho da posição
# ─────────────────────────────────────────────

def calcular_tamanho_posicao(saldo_disponivel: float, sinal: dict, preco: float) -> dict:
    """
    Calcula o tamanho ideal da posição.
    Risco = saldo * risco_pct
    Distância ao SL em % = |entrada - sl| / entrada
    Tamanho (USDT) = Risco / (dist_sl%) * alavancagem já embutido via margem
    Contratos = Tamanho_USDT / preco
    """
    if sinal["direcao"] == "NEUTRO" or not sinal["sl"]:
        return {"contratos": 0, "margem_usdt": 0, "risco_usdt": 0}

    risco_usdt   = saldo_disponivel * sinal["risco_pct"]
    dist_sl_pct  = abs(preco - sinal["sl"]) / preco  # ex: 0.015 = 1.5%

    if dist_sl_pct == 0:
        return {"contratos": 0, "margem_usdt": 0, "risco_usdt": 0}

    # Valor total da posição para que a perda ao SL seja = risco_usdt
    # Com alavancagem: perda = tamanho_posicao * dist_sl_pct / alavancagem * alavancagem = tamanho * dist_sl_pct
    tamanho_usdt = risco_usdt / dist_sl_pct
    margem_usdt  = tamanho_usdt / ALAVANCAGEM  # margem real necessária
    contratos    = tamanho_usdt / preco

    return {
        "contratos":    round(contratos,   4),
        "tamanho_usdt": round(tamanho_usdt, 2),
        "margem_usdt":  round(margem_usdt,  2),
        "risco_usdt":   round(risco_usdt,   2),
        "tp_usdt":      round(risco_usdt * TP_RATIO, 2),
        "dist_sl_pct":  round(dist_sl_pct * 100, 2),
    }


# ─────────────────────────────────────────────
# 🌐  ROTAS
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/validar", methods=["POST"])
def api_validar():
    """Valida as chaves API."""
    body = request.json or {}
    api_key    = body.get("api_key", "").strip()
    api_secret = body.get("api_secret", "").strip()
    if not api_key or not api_secret:
        return jsonify({"ok": False, "erro": "Chaves não informadas"}), 400
    valido = validate_api_keys(api_key, api_secret)
    return jsonify({"ok": valido, "erro": "" if valido else "Chaves inválidas ou sem permissão Futures"})


@app.route("/api/conta", methods=["POST"])
def api_conta():
    """Retorna saldo e posições abertas."""
    body       = request.json or {}
    api_key    = body.get("api_key", "").strip()
    api_secret = body.get("api_secret", "").strip()
    if not api_key or not api_secret:
        return jsonify({"erro": "Sem chaves"}), 400

    saldo    = get_futures_balance(api_key, api_secret)
    posicoes = get_open_positions(api_key, api_secret)

    return jsonify({
        "saldo":    saldo,
        "posicoes": posicoes,
        "n_trades": len(posicoes),
        "pode_abrir": len(posicoes) < MAX_TRADES,
    })


@app.route("/api/sinais", methods=["POST"])
def api_sinais():
    """Retorna sinais + gestão de risco calculada com saldo real."""
    body       = request.json or {}
    api_key    = body.get("api_key", "").strip()
    api_secret = body.get("api_secret", "").strip()

    # Busca saldo se tiver chaves
    saldo_disponivel = 0
    saldo_info = {}
    if api_key and api_secret:
        saldo_info = get_futures_balance(api_key, api_secret)
        saldo_disponivel = saldo_info.get("disponivel", 0)

    resultado = []
    for par in PARES:
        par_data = {"par": par, "timeframes": {}}
        for tf in TIMEFRAMES:
            df = buscar_candles(par, tf)
            if df is None or len(df) < 50:
                par_data["timeframes"][tf] = {"erro": "Dados insuficientes"}
                continue

            ind   = calcular_indicadores(df)
            sinal = analisar_sinal(ind)

            # Gestão de risco
            gestao = {}
            if saldo_disponivel > 0 and sinal["direcao"] != "NEUTRO":
                gestao = calcular_tamanho_posicao(saldo_disponivel, sinal, ind["preco"])

            par_data["timeframes"][tf] = {
                "preco":         ind["preco"],
                "direcao":       sinal["direcao"],
                "forca":         sinal["forca"],
                "n_long":        sinal["n_long"],
                "n_short":       sinal["n_short"],
                "detalhes":      sinal["detalhes"],
                "sl":            sinal["sl"],
                "tp":            sinal["tp"],
                "classificacao": sinal["classificacao"],
                "risco_pct":     sinal["risco_pct"],
                "gestao":        gestao,
                "topos":         ind["niveis"]["topos"],
                "fundos":        ind["niveis"]["fundos"],
                "closes":        ind["closes"],
                "timestamps":    ind["timestamps"],
                "rsi":           round(ind["rsi"], 2),
                "ema9":          round(ind["ema9"], 4),
                "ema21":         round(ind["ema21"], 4),
                "vol_ratio":     round(ind["vol_ratio"], 2),
            }
        resultado.append(par_data)

    return jsonify({
        "sinais":     resultado,
        "saldo":      saldo_info,
        "atualizado": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    })


@app.route("/api/meu-ip")
def api_meu_ip():
    """Retorna o IP público do servidor Railway — use este na MEXC."""
    try:
        r = requests.get("https://ifconfig.me/ip", timeout=8)
        ip = r.text.strip()
    except Exception:
        try:
            r = requests.get("https://api.ipify.org", timeout=8)
            ip = r.text.strip()
        except Exception as e:
            return jsonify({"erro": str(e)}), 500

    return jsonify({
        "ip": ip,
        "instrucoes": (
            "Cole este IP na MEXC em: "
            "Perfil → Gerenciamento de API → editar chave → campo 'IP vinculado'"
        )
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
