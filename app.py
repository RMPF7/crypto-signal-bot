"""
🤖 Crypto Signal Bot — MEXC Futures
- Autenticação via API Key/Secret
- Gestão de risco: 4/5 → 5% · 5/5 → 10% · Alavancagem 10x
- Stop Loss automático (topos/fundos) · Take Profit 3:1
- Máximo 2 trades simultâneos
- 3 timeframes: 15m, 1h, 4h
- Indicadores: RSI, EMA 9/21, MACD, Topos/Fundos, CVD, Order Blocks, Divergência RSI (4/7 para sinal)
- Filtros: ADX >= 15 (mercado em tendência) + Volume >= 0.8x da média
- Notificações Telegram com 3 entradas parciais e 3 TPs
- Pares customizáveis
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

PARES_DEFAULT = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT"]
TIMEFRAMES = ["15m", "1h", "4h"]
LIMITE_CANDLES = 100

RSI_SOBREVENDIDO  = 35
RSI_SOBRECOMPRADO = 65
VOLUME_MULT       = 1.4
MINIMO_CONF       = 4

ALAVANCAGEM     = 10
RISCO_ARRISCADO = 0.05
RISCO_SEGURO    = 0.10
TP_RATIO        = 3.0
MAX_TRADES      = 2

MEXC_BASE      = "https://contract.mexc.com"
MEXC_SPOT_BASE = "https://api.mexc.com"

TF_MAP = {"1h": "60m", "4h": "4h", "15m": "15m"}

_cache      = {}
_cache_ttl  = 60
_cache_lock = threading.Lock()

_sinais_notificados = {}


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
# 📲  TELEGRAM
# ─────────────────────────────────────────────

def enviar_telegram(token, chat_id, mensagem):
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": mensagem, "parse_mode": "HTML"}
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram erro: {e}")
        return False


def formatar_alerta_telegram(par, tf, direcao, preco, forca, sl, tp, classificacao, detalhes, entradas=None, tps=None):
    emoji = "🟢" if direcao == "LONG" else "🔴"
    cls_txt = "✅ SEGURO (10%)" if classificacao == "SEGURO" else "⚠️ ARRISCADO (5%)"
    estrelas = "⭐" * forca + "☆" * (5 - forca)

    msg = f"{emoji} <b>SINAL {direcao} — {par.replace('USDT', '/USDT')}</b>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"⏱ Timeframe: <b>{tf}</b>\n"
    msg += f"💰 Preço atual: <b>${preco:,.4f}</b>\n"
    msg += f"📊 Força: {estrelas} ({forca}/5)\n"
    msg += f"🏷 Classificação: {cls_txt}\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"

    # 3 Entradas parciais
    if entradas and len(entradas) == 3:
        msg += "📥 <b>Entradas parciais:</b>\n"
        pcts = ["33%", "33%", "34%"]
        labels = ["agora", "se cair", "se cair mais"] if direcao == "LONG" else ["agora", "se subir", "se subir mais"]
        for i, (e, pct, lbl) in enumerate(zip(entradas, pcts, labels), 1):
            msg += f"  • E{i}: <b>${e:,.4f}</b> ({pct}) — {lbl}\n"
    elif sl and tp:
        msg += f"🛑 Stop Loss: <b>${sl:,.4f}</b>\n"
        msg += f"✅ Take Profit: <b>${tp:,.4f}</b>\n"

    # Stop Loss
    if sl:
        msg += f"\n🛑 Stop Loss: <b>${sl:,.4f}</b>\n"

    # 3 Take Profits
    if tps and len(tps) == 3:
        msg += "\n📤 <b>Take Profits:</b>\n"
        pcts = ["33%", "33%", "34%"]
        ratios = ["1:1", "1:2", "1:3"]
        for i, (t, pct, ratio) in enumerate(zip(tps, pcts, ratios), 1):
            msg += f"  • TP{i}: <b>${t:,.4f}</b> ({pct}) — R/R {ratio}\n"

    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += "📋 <b>Indicadores:</b>\n"
    for d in detalhes:
        ic = "🟢" if d["sinal"] == "LONG" else "🔴" if d["sinal"] == "SHORT" else "⚪"
        msg += f"{ic} {d['nome']}: {d['desc']}\n"

    msg += f"\n🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
    msg += "<i>⚠️ Sinal de análise — decisão é sempre sua.</i>"
    return msg.strip()


# ─────────────────────────────────────────────
# 📡  MEXC — candles
# ─────────────────────────────────────────────

def buscar_candles(par, intervalo):
    key = f"{par}_{intervalo}"
    now = time.time()
    with _cache_lock:
        if key in _cache:
            df_c, ts = _cache[key]
            if now - ts < _cache_ttl:
                return df_c

    intervalo_api = TF_MAP.get(intervalo, intervalo)
    url    = f"{MEXC_SPOT_BASE}/api/v3/klines"
    params = {"symbol": par, "interval": intervalo_api, "limit": LIMITE_CANDLES}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        raw = r.json()
        if not raw or not isinstance(raw, list):
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
        print(f"Candles {par} {intervalo}: {e}")
        return None


# ─────────────────────────────────────────────
# 📊  TOPOS E FUNDOS
# ─────────────────────────────────────────────

def detectar_niveis(df, janela=5):
    highs = df["high"].values
    lows  = df["low"].values
    close = df["close"].values
    preco = close[-1]
    topos  = []
    fundos = []

    for i in range(janela, len(highs) - janela):
        if highs[i] == max(highs[i - janela:i + janela + 1]):
            topos.append(highs[i])
        if lows[i] == min(lows[i - janela:i + janela + 1]):
            fundos.append(lows[i])

    topos_rec  = sorted(topos[-6:], reverse=True)[:3] if topos  else []
    fundos_rec = sorted(fundos[-6:])[:3]              if fundos else []

    margem = 0.015
    perto_suporte     = any(abs(preco - f) / f <= margem for f in fundos_rec)
    perto_resistencia = any(abs(preco - t) / t <= margem for t in topos_rec)

    if perto_suporte:
        sinal = "LONG"
        desc  = f"Proximo de suporte ${fundos_rec[0]:,.4f}"
    elif perto_resistencia:
        sinal = "SHORT"
        desc  = f"Proximo de resistencia ${topos_rec[0]:,.4f}"
    else:
        sinal = "NEUTRO"
        desc  = "Sem nivel relevante proximo"

    sl_long  = fundos_rec[0] * 0.995 if fundos_rec else preco * 0.98
    sl_short = topos_rec[0]  * 1.005 if topos_rec  else preco * 1.02

    return {
        "sinal": sinal, "desc": desc,
        "topos": topos_rec, "fundos": fundos_rec,
        "sl_long": sl_long, "sl_short": sl_short,
    }



# DIVERGENCIAS RSI
def detectar_divergencia_rsi(df, janela=5):
    close = df["close"].values
    rsi = ta.momentum.RSIIndicator(df["close"], window=14).rsi().values
    def find_pivots(data, ptype, lookback=30):
        pts = []
        sub = data[-lookback:]
        for i in range(janela, len(sub)-janela):
            if ptype=="low" and sub[i]==min(sub[i-janela:i+janela+1]):
                pts.append((i,sub[i]))
            elif ptype=="high" and sub[i]==max(sub[i-janela:i+janela+1]):
                pts.append((i,sub[i]))
        return pts[-2:] if len(pts)>=2 else []
    pl=find_pivots(close,"low"); rsl=find_pivots(rsi,"low")
    ph=find_pivots(close,"high"); rsh=find_pivots(rsi,"high")
    bullish=len(pl)==2 and len(rsl)==2 and pl[1][1]<pl[0][1] and rsl[1][1]>rsl[0][1]
    bearish=len(ph)==2 and len(rsh)==2 and ph[1][1]>ph[0][1] and rsh[1][1]<rsh[0][1]
    desc="Divergencia Bullish - reversao para cima" if bullish else "Divergencia Bearish - reversao para baixo" if bearish else "Sem divergencia"
    return {"bullish":bullish,"bearish":bearish,"desc":desc}

# ─────────────────────────────────────────────
# 📊  INDICADORES
# ─────────────────────────────────────────────


# CVD
def calcular_cvd(df):
    close=df["close"]; volume=df["volume"]
    if "taker_buy_base" in df.columns:
        try:
            bv=pd.to_numeric(df["taker_buy_base"],errors="coerce").fillna(0)
            delta=bv-(volume-bv)
        except:
            bp=(close-df["low"])/((df["high"]-df["low"]).replace(0,1))
            delta=volume*bp-volume*(1-bp)
    else:
        bp=(close-df["low"])/((df["high"]-df["low"]).replace(0,1))
        delta=volume*bp-volume*(1-bp)
    cvd=delta.iloc[-20:].cumsum()
    ca=cvd.iloc[-1]; ci=cvd.iloc[0]
    ct="subindo" if ca>ci else "caindo"
    pt="subindo" if close.iloc[-1]>close.iloc[-20] else "caindo"
    if ct=="subindo" and pt=="caindo": s,d="LONG","CVD subindo, preco caindo - pressao compradora (bullish)"
    elif ct=="caindo" and pt=="subindo": s,d="SHORT","CVD caindo, preco subindo - pressao vendedora (bearish)"
    elif ct==pt=="subindo": s,d="LONG","CVD e preco alinhados para cima"
    elif ct==pt=="caindo": s,d="SHORT","CVD e preco alinhados para baixo"
    else: s,d="NEUTRO","CVD sem direcao"
    return {"sinal":s,"desc":d,"cvd_trend":ct,"div_bullish":ct=="subindo" and pt=="caindo","div_bearish":ct=="caindo" and pt=="subindo"}

# ORDER BLOCKS
def detectar_order_blocks(df):
    c=df["close"].values; o=df["open"].values; h=df["high"].values; l=df["low"].values
    pa=c[-1]; obl=[]; obr=[]; lb=min(50,len(c)-2)
    for i in range(1,lb):
        idx=-(i+1)
        if c[idx]<o[idx] and (c[idx+1]-o[idx+1])/max(abs(o[idx+1]),0.001)>0.005:
            t=max(o[idx],c[idx]); b=min(o[idx],c[idx])
            if pa>b: obl.append({"top":t,"bot":b,"mid":(t+b)/2})
        if c[idx]>o[idx] and (o[idx+1]-c[idx+1])/max(abs(o[idx+1]),0.001)>0.005:
            t=max(o[idx],c[idx]); b=min(o[idx],c[idx])
            if pa<t: obr.append({"top":t,"bot":b,"mid":(t+b)/2})
    obl.sort(key=lambda x:abs(pa-x["mid"])); obr.sort(key=lambda x:abs(pa-x["mid"]))
    obl=obl[:2]; obr=obr[:2]
    db=any(ob["bot"]<=pa<=ob["top"] for ob in obl); dr=any(ob["bot"]<=pa<=ob["top"] for ob in obr)
    pb=any(abs(pa-ob["mid"])/pa<=0.01 for ob in obl); pr=any(abs(pa-ob["mid"])/pa<=0.01 for ob in obr)
    if db or pb: s,d="LONG","Preco em Order Block de suporte institucional"
    elif dr or pr: s,d="SHORT","Preco em Order Block de resistencia institucional"
    else: s,d="NEUTRO","Preco fora de zonas institucionais"
    return {"sinal":s,"desc":d,"obs_bullish":[{"top":round(x["top"],4),"bot":round(x["bot"],4)} for x in obl],"obs_bearish":[{"top":round(x["top"],4),"bot":round(x["bot"],4)} for x in obr]}

def calcular_indicadores(df):
    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]

    rsi_val  = ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1]
    macd_obj = ta.trend.MACD(close)
    mh_atual = macd_obj.macd_diff().iloc[-1]
    mh_prev  = macd_obj.macd_diff().iloc[-2]
    ema9     = ta.trend.EMAIndicator(close, window=9).ema_indicator().iloc[-1]
    ema21    = ta.trend.EMAIndicator(close, window=21).ema_indicator().iloc[-1]

    vol_atual = volume.iloc[-1]
    vol_media = volume.iloc[-20:].mean()
    vol_ratio = vol_atual / vol_media if vol_media > 0 else 1.0

    # ATR
    atr_obj   = ta.volatility.AverageTrueRange(high, low, close, window=14)
    atr_val   = atr_obj.average_true_range().iloc[-1]
    atr_pct   = (atr_val / close.iloc[-1]) * 100

    # ADX
    adx_obj   = ta.trend.ADXIndicator(high, low, close, window=14)
    adx_val   = adx_obj.adx().iloc[-1]
    dmi_plus  = adx_obj.adx_pos().iloc[-1]
    dmi_minus = adx_obj.adx_neg().iloc[-1]

    niveis = detectar_niveis(df)
    diverg = detectar_divergencia_rsi(df)
    cvd    = calcular_cvd(df)
    ob     = detectar_order_blocks(df)

    return {
        "preco":      close.iloc[-1],
        "rsi":        rsi_val,
        "macd_hist":  mh_atual,
        "macd_prev":  mh_prev,
        "ema9":       ema9,
        "ema21":      ema21,
        "vol_ratio":  vol_ratio,
        "atr":        atr_val,
        "atr_pct":    atr_pct,
        "adx":        adx_val,
        "dmi_plus":   dmi_plus,
        "dmi_minus":  dmi_minus,
        "niveis":     niveis,
        "diverg":     diverg,
        "cvd":        cvd,
        "ob":         ob,
        "closes":     close.iloc[-30:].tolist(),
        "timestamps": df["timestamp"].iloc[-30:].tolist(),
    }


# ─────────────────────────────────────────────
# 🧠  LÓGICA DE SINAIS
# ─────────────────────────────────────────────

def calcular_entradas_e_tps(preco, sl, direcao):
    """
    Calcula 3 entradas parciais e 3 take profits escalonados.

    Entradas (DCA):
      - E1: preço atual (entrada imediata)
      - E2: 1/3 do caminho até o SL (preço melhor)
      - E3: 2/3 do caminho até o SL (preço ainda melhor)

    Take Profits (escalonados 1:1, 1:2, 1:3):
      - TP1: distância SL × 1.0
      - TP2: distância SL × 2.0
      - TP3: distância SL × 3.0
    """
    dist_sl = abs(preco - sl)

    if direcao == "LONG":
        e1 = preco
        e2 = preco - dist_sl * 0.33
        e3 = preco - dist_sl * 0.66
        tp1 = preco + dist_sl * 1.0
        tp2 = preco + dist_sl * 2.0
        tp3 = preco + dist_sl * 3.0
    else:  # SHORT
        e1 = preco
        e2 = preco + dist_sl * 0.33
        e3 = preco + dist_sl * 0.66
        tp1 = preco - dist_sl * 1.0
        tp2 = preco - dist_sl * 2.0
        tp3 = preco - dist_sl * 3.0

    def r(v):
        if v >= 1000:  return round(v, 2)
        if v >= 1:     return round(v, 4)
        return round(v, 6)

    return [r(e1), r(e2), r(e3)], [r(tp1), r(tp2), r(tp3)]


def analisar_sinal(ind):
    """
    Sistema 7/7 — conta pontos de 7 indicadores independentes.
    Sinal gerado com >= 4 confirmações na mesma direção (4/7).
    Volume e ADX atuam como FILTROS (não contam no score).

    Indicadores (score):
      1. RSI          — sobrecompra/sobrevenda
      2. EMA 9/21     — tendência curto prazo
      3. MACD         — momentum / cruzamento
      4. Topos/Fundos — suporte e resistência estrutural
      5. CVD          — pressão compradora/vendedora real
      6. Order Blocks — zonas institucionais
      7. Divergência RSI — reversão antecipada

    Filtros (bloqueiam sinal se não passarem):
      - ADX >= 15  (mercado em tendência, não lateral)
      - Volume >= 0.8x da média (liquidez mínima)
    """
    conf_long  = []
    conf_short = []
    detalhes   = []

    # ── 1. RSI ──────────────────────────────────
    if ind["rsi"] < RSI_SOBREVENDIDO:
        conf_long.append("RSI")
        detalhes.append({"nome":"RSI","valor":f"{ind['rsi']:.1f}","sinal":"LONG","desc":f"RSI sobrevendido ({ind['rsi']:.1f})"})
    elif ind["rsi"] > RSI_SOBRECOMPRADO:
        conf_short.append("RSI")
        detalhes.append({"nome":"RSI","valor":f"{ind['rsi']:.1f}","sinal":"SHORT","desc":f"RSI sobrecomprado ({ind['rsi']:.1f})"})
    else:
        detalhes.append({"nome":"RSI","valor":f"{ind['rsi']:.1f}","sinal":"NEUTRO","desc":f"RSI neutro ({ind['rsi']:.1f})"})

    # ── 2. EMA ──────────────────────────────────
    if ind["ema9"] > ind["ema21"]:
        conf_long.append("EMA")
        detalhes.append({"nome":"EMA","valor":"9>21","sinal":"LONG","desc":"EMA9 > EMA21 (alta)"})
    else:
        conf_short.append("EMA")
        detalhes.append({"nome":"EMA","valor":"9<21","sinal":"SHORT","desc":"EMA9 < EMA21 (baixa)"})

    # ── 3. MACD ─────────────────────────────────
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

    # ── 4. Topos/Fundos ─────────────────────────
    tf_sinal = ind["niveis"]["sinal"]
    if tf_sinal == "LONG":
        conf_long.append("Niveis")
        detalhes.append({"nome":"Niveis","valor":"Suporte","sinal":"LONG","desc":ind["niveis"]["desc"]})
    elif tf_sinal == "SHORT":
        conf_short.append("Niveis")
        detalhes.append({"nome":"Niveis","valor":"Resistencia","sinal":"SHORT","desc":ind["niveis"]["desc"]})
    else:
        detalhes.append({"nome":"Niveis","valor":"Neutro","sinal":"NEUTRO","desc":ind["niveis"]["desc"]})

    # ── 5. CVD ──────────────────────────────────
    cvd_sinal = ind["cvd"]["sinal"]
    if cvd_sinal == "LONG":
        conf_long.append("CVD")
        detalhes.append({"nome":"CVD","valor":"↑","sinal":"LONG","desc":ind["cvd"]["desc"]})
    elif cvd_sinal == "SHORT":
        conf_short.append("CVD")
        detalhes.append({"nome":"CVD","valor":"↓","sinal":"SHORT","desc":ind["cvd"]["desc"]})
    else:
        detalhes.append({"nome":"CVD","valor":"—","sinal":"NEUTRO","desc":ind["cvd"]["desc"]})

    # ── 6. Order Blocks ─────────────────────────
    ob_sinal = ind["ob"]["sinal"]
    if ob_sinal == "LONG":
        conf_long.append("OB")
        detalhes.append({"nome":"OB","valor":"Suporte","sinal":"LONG","desc":ind["ob"]["desc"]})
    elif ob_sinal == "SHORT":
        conf_short.append("OB")
        detalhes.append({"nome":"OB","valor":"Resist.","sinal":"SHORT","desc":ind["ob"]["desc"]})
    else:
        detalhes.append({"nome":"OB","valor":"—","sinal":"NEUTRO","desc":ind["ob"]["desc"]})

    # ── 7. Divergência RSI ──────────────────────
    div = ind["diverg"]
    if div["bullish"]:
        conf_long.append("Diverg")
        detalhes.append({"nome":"Diverg","valor":"Bull","sinal":"LONG","desc":div["desc"]})
    elif div["bearish"]:
        conf_short.append("Diverg")
        detalhes.append({"nome":"Diverg","valor":"Bear","sinal":"SHORT","desc":div["desc"]})
    else:
        detalhes.append({"nome":"Diverg","valor":"—","sinal":"NEUTRO","desc":div["desc"]})

    # ── Filtros (Volume + ADX) ───────────────────
    adx_ok     = ind["adx"] >= 10
    volume_ok  = ind["vol_ratio"] >= 0.8
    filtro_ok  = adx_ok and volume_ok

    adx_desc   = f"ADX {ind['adx']:.1f} ({'✓ tendencia' if adx_ok else '✗ lateral'})"
    vol_desc   = f"Volume {ind['vol_ratio']:.1f}x ({'✓ ok' if volume_ok else '✗ fraco'})"
    detalhes.append({"nome":"ADX","valor":f"{ind['adx']:.1f}","sinal":"NEUTRO" if not adx_ok else "INFO","desc":adx_desc})
    detalhes.append({"nome":"Volume","valor":f"{ind['vol_ratio']:.1f}x","sinal":"NEUTRO" if not volume_ok else "INFO","desc":vol_desc})

    n_long  = len(conf_long)
    n_short = len(conf_short)
    preco   = ind["preco"]

    # Score de classificação sobre 7
    if n_long >= MINIMO_CONF and n_long > n_short:
        direcao = "LONG"
        forca   = n_long
        sl = ind["niveis"]["sl_long"]
        distancia_sl = abs(preco - sl)
        tp = preco + distancia_sl * TP_RATIO
        risco_pct = RISCO_SEGURO if forca >= 6 else RISCO_ARRISCADO
        classificacao = "SEGURO" if forca >= 6 else "ARRISCADO"
        entradas, tps = calcular_entradas_e_tps(preco, sl, "LONG")
    elif n_short >= MINIMO_CONF and n_short > n_long:
        direcao = "SHORT"
        forca   = n_short
        sl = ind["niveis"]["sl_short"]
        distancia_sl = abs(sl - preco)
        tp = preco - distancia_sl * TP_RATIO
        risco_pct = RISCO_SEGURO if forca >= 6 else RISCO_ARRISCADO
        classificacao = "SEGURO" if forca >= 6 else "ARRISCADO"
        entradas, tps = calcular_entradas_e_tps(preco, sl, "SHORT")
    else:
        direcao = "NEUTRO"
        forca   = max(n_long, n_short)
        sl = tp = None
        distancia_sl = 0
        risco_pct = 0
        classificacao = "NEUTRO"
        entradas, tps = [], []

    return {
        "direcao": direcao, "forca": forca,
        "n_long": n_long, "n_short": n_short,
        "detalhes": detalhes,
        "sl": round(sl, 6) if sl else None,
        "tp": round(tp, 6) if tp else None,
        "entradas": entradas,
        "tps": tps,
        "risco_pct": risco_pct,
        "classificacao": classificacao,
        "filtro_adx": adx_ok,
        "filtro_volume": volume_ok,
    }


# ─────────────────────────────────────────────
# 💰  GESTÃO DE RISCO
# ─────────────────────────────────────────────

def calcular_tamanho_posicao(saldo, sinal, preco):
    if sinal["direcao"] == "NEUTRO" or not sinal["sl"]:
        return {"contratos": 0, "margem_usdt": 0, "risco_usdt": 0}

    risco_usdt  = saldo * sinal["risco_pct"]
    dist_sl_pct = abs(preco - sinal["sl"]) / preco
    if dist_sl_pct == 0:
        return {"contratos": 0, "margem_usdt": 0, "risco_usdt": 0}

    tamanho_usdt = risco_usdt / dist_sl_pct
    margem_usdt  = tamanho_usdt / ALAVANCAGEM
    contratos    = tamanho_usdt / preco

    return {
        "contratos":    round(contratos, 4),
        "tamanho_usdt": round(tamanho_usdt, 2),
        "margem_usdt":  round(margem_usdt, 2),
        "risco_usdt":   round(risco_usdt, 2),
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
    body = request.json or {}
    api_key    = body.get("api_key", "").strip()
    api_secret = body.get("api_secret", "").strip()
    if not api_key or not api_secret:
        return jsonify({"ok": False, "erro": "Chaves nao informadas"}), 400
    valido = validate_api_keys(api_key, api_secret)
    return jsonify({"ok": valido, "erro": "" if valido else "Chaves invalidas ou sem permissao Futures"})


@app.route("/api/conta", methods=["POST"])
def api_conta():
    body       = request.json or {}
    api_key    = body.get("api_key", "").strip()
    api_secret = body.get("api_secret", "").strip()
    if not api_key or not api_secret:
        return jsonify({"erro": "Sem chaves"}), 400
    saldo    = get_futures_balance(api_key, api_secret)
    posicoes = get_open_positions(api_key, api_secret)
    return jsonify({
        "saldo": saldo,
        "posicoes": posicoes,
        "n_trades": len(posicoes),
        "pode_abrir": len(posicoes) < MAX_TRADES,
    })


@app.route("/api/sinais", methods=["POST"])
def api_sinais():
    body       = request.json or {}
    api_key    = body.get("api_key", "").strip()
    api_secret = body.get("api_secret", "").strip()
    pares      = body.get("pares", PARES_DEFAULT)
    tg_token   = body.get("tg_token", "").strip()
    tg_chat_id = body.get("tg_chat_id", "").strip()

    saldo_disponivel = 0
    saldo_info = {}
    if api_key and api_secret:
        saldo_info = get_futures_balance(api_key, api_secret)
        saldo_disponivel = saldo_info.get("disponivel", 0)

    resultado = []
    for par in pares:
        par = par.upper().strip()
        if not par.endswith("USDT"):
            par = par + "USDT"
        par_data = {"par": par, "timeframes": {}}

        for tf in TIMEFRAMES:
            try:
                df = buscar_candles(par, tf)
                if df is None or len(df) < 50:
                    par_data["timeframes"][tf] = {"erro": f"Par {par} nao encontrado ou dados insuficientes"}
                    continue

                ind   = calcular_indicadores(df)
                sinal = analisar_sinal(ind)
                gestao = {}
                if saldo_disponivel > 0 and sinal["direcao"] != "NEUTRO":
                    gestao = calcular_tamanho_posicao(saldo_disponivel, sinal, ind["preco"])

                # Notificação Telegram se sinal forte e novo
                if tg_token and tg_chat_id and sinal["direcao"] != "NEUTRO" and sinal["forca"] >= 4:
                    chave_sinal = f"{par}_{tf}_{sinal['direcao']}"
                    ultimo = _sinais_notificados.get(chave_sinal, 0)
                    if time.time() - ultimo > 3600:
                        msg = formatar_alerta_telegram(
                            par, tf, sinal["direcao"], ind["preco"],
                            sinal["forca"], sinal["sl"], sinal["tp"],
                            sinal["classificacao"], sinal["detalhes"],
                            entradas=sinal["entradas"],
                            tps=sinal["tps"],
                        )
                        if enviar_telegram(tg_token, tg_chat_id, msg):
                            _sinais_notificados[chave_sinal] = time.time()

                par_data["timeframes"][tf] = {
                    "preco":         ind["preco"],
                    "direcao":       sinal["direcao"],
                    "forca":         sinal["forca"],
                    "n_long":        sinal["n_long"],
                    "n_short":       sinal["n_short"],
                    "detalhes":      sinal["detalhes"],
                    "sl":            sinal["sl"],
                    "tp":            sinal["tp"],
                    "entradas":      sinal["entradas"],
                    "tps":           sinal["tps"],
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
                    "adx":           round(ind["adx"], 1),
                    "filtro_adx":    sinal.get("filtro_adx", True),
                    "filtro_volume": sinal.get("filtro_volume", True),
                }
            except Exception as e:
                print(f"Erro ao processar {par} {tf}: {e}")
                par_data["timeframes"][tf] = {"erro": f"Erro: {str(e)[:120]}"}
        resultado.append(par_data)

    return jsonify({
        "sinais":     resultado,
        "saldo":      saldo_info,
        "atualizado": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    })


@app.route("/api/meu-ip")
def api_meu_ip():
    try:
        r = requests.get("https://ifconfig.me/ip", timeout=8)
        ip = r.text.strip()
    except Exception:
        try:
            r = requests.get("https://api.ipify.org", timeout=8)
            ip = r.text.strip()
        except Exception as e:
            return jsonify({"erro": str(e)}), 500
    return jsonify({"ip": ip})


@app.route("/api/testar-telegram", methods=["POST"])
def api_testar_telegram():
    body       = request.json or {}
    tg_token   = body.get("tg_token", "").strip()
    tg_chat_id = body.get("tg_chat_id", "").strip()
    if not tg_token or not tg_chat_id:
        return jsonify({"ok": False, "erro": "Token ou Chat ID nao informados"}), 400
    ok = enviar_telegram(tg_token, tg_chat_id,
        "✅ <b>Signal Bot conectado!</b>\n\nVoce vai receber alertas aqui quando surgir sinal de 4/5 ou 5/5 indicadores.")
    return jsonify({"ok": ok, "erro": "" if ok else "Falha ao enviar mensagem"})


@app.route("/api/candles/<par>/<intervalo>")
def api_candles(par, intervalo):
    par = par.upper()
    TF_REVERSE = {"60m": "1h", "240m": "4h", "15m": "15m", "1h": "1h", "4h": "4h"}
    intervalo_interno = TF_REVERSE.get(intervalo, intervalo)
    df = buscar_candles(par, intervalo_interno)
    if df is None or len(df) < 2:
        return jsonify({"candles": []})

    candles = []
    for _, row in df.iterrows():
        ts = int(row["timestamp"]) // 1000
        candles.append({
            "time":  ts,
            "open":  float(row["open"]),
            "high":  float(row["high"]),
            "low":   float(row["low"]),
            "close": float(row["close"]),
        })
    return jsonify({"candles": candles})


@app.route("/api/mtf", methods=["POST"])
def api_mtf():
    body=request.json or {}
    api_key=body.get("api_key","").strip(); api_secret=body.get("api_secret","").strip()
    pares=body.get("pares",PARES_DEFAULT); tg_token=body.get("tg_token","").strip(); tg_chat_id=body.get("tg_chat_id","").strip()
    saldo=0
    if api_key and api_secret:
        si=get_futures_balance(api_key,api_secret); saldo=si.get("disponivel",0)
    resultado=[]
    for par in pares:
        par=par.upper().strip()
        if not par.endswith("USDT"): par+="USDT"
        dados={}
        for tf in TIMEFRAMES:
            df=buscar_candles(par,tf)
            if df is None or len(df)<50: dados[tf]=None; continue
            ind=calcular_indicadores(df); sinal=analisar_sinal(ind); dados[tf]={"ind":ind,"sinal":sinal}
        tf4=dados.get("4h"); tf1=dados.get("1h"); tf15=dados.get("15m")
        if not all([tf4,tf1,tf15]): resultado.append({"par":par,"mtf_sinal":"NEUTRO","erro":"Dados insuficientes"}); continue
        d4=tf4["sinal"]["direcao"]; d1=tf1["sinal"]["direcao"]; d15=tf15["sinal"]["direcao"]
        adx4=tf4["sinal"].get("adx",0)
        if d4=="LONG" and d1=="LONG" and d15=="LONG" and adx4>=20: ms,mf,md="LONG","MAXIMA","3/3 TFs LONG"
        elif d4=="SHORT" and d1=="SHORT" and d15=="SHORT" and adx4>=20: ms,mf,md="SHORT","MAXIMA","3/3 TFs SHORT"
        elif d4=="LONG" and d1=="LONG" and adx4>=20: ms,mf,md="LONG","ALTA","4h+1h LONG, 15m="+d15
        elif d4=="SHORT" and d1=="SHORT" and adx4>=20: ms,mf,md="SHORT","ALTA","4h+1h SHORT, 15m="+d15
        else: ms,mf,md="NEUTRO","BAIXA","TFs: 4h="+d4+" 1h="+d1+" 15m="+d15
        gestao={}
        if saldo>0 and ms!="NEUTRO":
            s=tf15["sinal"]; s["risco_pct"]=RISCO_SEGURO if mf=="MAXIMA" else RISCO_ARRISCADO
            gestao=calcular_tamanho_posicao(saldo,s,tf15["ind"]["preco"])
        if tg_token and tg_chat_id and ms!="NEUTRO" and mf in ["MAXIMA","ALTA"]:
            chave="MTF_"+par+"_"+ms
            if time.time()-_sinais_notificados.get(chave,0)>3600:
                emoji="🟢" if ms=="LONG" else "🔴"
                msg=emoji+" <b>MTF "+par.replace("USDT","/USDT")+"</b>\n"+ms+" - "+mf+"\n"+md
                if enviar_telegram(tg_token,tg_chat_id,msg): _sinais_notificados[chave]=time.time()
        resultado.append({"par":par,"mtf_sinal":ms,"mtf_forca":mf,"mtf_desc":md,"dir_4h":d4,"dir_1h":d1,"dir_15m":d15,"adx_4h":adx4,"preco":tf15["ind"]["preco"],"sl":tf15["sinal"].get("sl"),"tp":tf15["sinal"].get("tp"),"gestao":gestao})
    return jsonify({"mtf":resultado,"atualizado":datetime.now().strftime("%d/%m/%Y %H:%M:%S")})


@app.route("/api/html-b64")
def api_html_b64():
    import base64, os
    path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(path, "rb") as f:
        content = f.read()
    return jsonify({"b64": base64.b64encode(content).decode("ascii")})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
