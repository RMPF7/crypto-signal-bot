"""
Calculo de indicadores tecnicos: niveis (suporte/resistencia), divergencia RSI,
CVD, Order Blocks, padroes graficos e o agregador calcular_indicadores().
"""
import pandas as pd
import ta


# ─────────────────────────────────────────────
# 📊  TOPOS E FUNDOS
# ─────────────────────────────────────────────

def detectar_niveis(df, janela=5):
    """
    [#1 FIX] Margem aumentada de 1.5% para 2.5% - permite detectar SHORTs
    quando o preço está próximo de resistência mas não exatamente no nível.

    [#1 FIX] Resolve conflito: se o preço estiver perto de suporte E resistência
    ao mesmo tempo, prefere o nível mais próximo em vez de dar LONG por padrão.
    """
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

    # [#1] Margem aumentada: 1.5% -> 2.5%
    margem = 0.025

    perto_suporte     = any(abs(preco - f) / f <= margem for f in fundos_rec)
    perto_resistencia = any(abs(preco - t) / t <= margem for t in topos_rec)

    # [#1] Resolve conflito: preço perto dos dois -> prefere o mais próximo
    if perto_suporte and perto_resistencia:
        dist_sup = min(abs(preco - f) / f for f in fundos_rec)
        dist_res = min(abs(preco - t) / t for t in topos_rec)
        if dist_sup <= dist_res:
            perto_resistencia = False  # suporte ganha
        else:
            perto_suporte = False      # resistência ganha

    if perto_suporte:
        nivel_ref = min(fundos_rec, key=lambda f: abs(preco - f))
        sinal = "LONG"
        desc  = f"Proximo de suporte ${nivel_ref:,.4f}"
    elif perto_resistencia:
        nivel_ref = min(topos_rec, key=lambda t: abs(preco - t))
        sinal = "SHORT"
        desc  = f"Proximo de resistencia ${nivel_ref:,.4f}"
    else:
        sinal = "NEUTRO"
        desc  = "Sem nivel relevante proximo"

    sl_long  = fundos_rec[0] * 0.98 if fundos_rec else preco * 0.98   # [BACKTEST] 2% de folga
    sl_short = topos_rec[0]  * 1.02 if topos_rec  else preco * 1.02   # [BACKTEST] 2% de folga

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
    return {"bullish":bool(bullish),"bearish":bool(bearish),"desc":desc}


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


def detectar_padroes_graficos(df, niveis):
    """
    Detecta padroes graficos classicos para uso como FILTRO (nao soma pontos
    no score). Combina:
      1) Candlestick (engolfo, martelo, estrela cadente, doji) nos ultimos candles
      2) Estrutura maior (topo duplo, fundo duplo) usando topos_rec/fundos_rec
         ja calculados em detectar_niveis()

    Retorna o padrao mais relevante encontrado, com sinal LONG/SHORT/NEUTRO
    e forca FORTE/FRACO. Se um padrao bearish forte aparecer junto com um
    padrao bullish, prevalece o mais forte; em empate, o mais recente.
    """
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    preco = c[-1]

    candidatos = []  # lista de (prioridade, sinal, forca, nome, desc)

    # ── Candlestick: usa os 2 ultimos candles fechados (i=-2 corpo, i=-1 atual) ──
    if len(c) >= 3:
        # Engolfo (compara candle -2 com -3 para evitar usar candle em formacao)
        o1, c1, h1, l1 = o[-3], c[-3], h[-3], l[-3]
        o2, c2, h2, l2 = o[-2], c[-2], h[-2], l[-2]
        corpo1 = abs(c1 - o1)
        corpo2 = abs(c2 - o2)

        # Engolfo de alta: candle1 baixa, candle2 alta e "engole" o corpo do 1
        if c1 < o1 and c2 > o2 and c2 >= o1 and o2 <= c1 and corpo2 > corpo1:
            candidatos.append((1, "LONG", "FORTE", "Engolfo de Alta",
                                "Candle de alta engoliu o corpo do candle anterior de baixa"))
        # Engolfo de baixa: candle1 alta, candle2 baixa e "engole" o corpo do 1
        if c1 > o1 and c2 < o2 and o2 >= c1 and c2 <= o1 and corpo2 > corpo1:
            candidatos.append((1, "SHORT", "FORTE", "Engolfo de Baixa",
                                "Candle de baixa engoliu o corpo do candle anterior de alta"))

        # Martelo / Estrela cadente (candle -2, ja fechado)
        range2 = h2 - l2
        if range2 > 0:
            corpo_pct = corpo2 / range2
            sombra_inf = min(o2, c2) - l2
            sombra_sup = h2 - max(o2, c2)
            # Martelo: corpo pequeno no topo do range, sombra inferior longa
            if corpo_pct <= 0.35 and sombra_inf >= corpo2 * 2 and sombra_sup <= corpo2 * 0.6:
                candidatos.append((2, "LONG", "FRACO", "Martelo",
                                    "Sombra inferior longa sugere rejeicao de precos mais baixos"))
            # Estrela cadente: corpo pequeno na base do range, sombra superior longa
            if corpo_pct <= 0.35 and sombra_sup >= corpo2 * 2 and sombra_inf <= corpo2 * 0.6:
                candidatos.append((2, "SHORT", "FRACO", "Estrela Cadente",
                                    "Sombra superior longa sugere rejeicao de precos mais altos"))

            # Doji: corpo muito pequeno relativo ao range -> indecisao, nao da direcao
            if corpo_pct <= 0.1:
                candidatos.append((3, "NEUTRO", "FRACO", "Doji",
                                    "Corpo minimo indica indecisao do mercado"))

    # ── Estrutura maior: topo duplo / fundo duplo ──────────────────────────
    topos_rec  = niveis.get("topos", [])
    fundos_rec = niveis.get("fundos", [])

    if len(topos_rec) >= 2:
        t1, t2 = topos_rec[0], topos_rec[1]
        if abs(t1 - t2) / max(t1, t2) <= 0.015:  # topos a menos de 1.5% um do outro
            nivel = max(t1, t2)
            if abs(preco - nivel) / nivel <= 0.03:
                candidatos.append((1, "SHORT", "FORTE", "Topo Duplo",
                                    f"Dois topos proximos perto de ${nivel:,.4f} sugerem resistencia forte"))

    if len(fundos_rec) >= 2:
        f1, f2 = fundos_rec[0], fundos_rec[1]
        if abs(f1 - f2) / max(f1, f2) <= 0.015:
            nivel = min(f1, f2)
            if abs(preco - nivel) / nivel <= 0.03:
                candidatos.append((1, "LONG", "FORTE", "Fundo Duplo",
                                    f"Dois fundos proximos perto de ${nivel:,.4f} sugerem suporte forte"))

    if not candidatos:
        return {"sinal": "NEUTRO", "forca": "FRACO", "padrao": "Nenhum",
                "desc": "Nenhum padrao grafico relevante detectado"}

    # Prioridade menor = mais forte/confiavel. Em empate, prefere FORTE.
    candidatos.sort(key=lambda x: (x[0], 0 if x[2] == "FORTE" else 1))
    prioridade, sinal, forca, nome, desc = candidatos[0]
    return {"sinal": sinal, "forca": forca, "padrao": nome, "desc": desc}


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
    # EMA200: usada para definir tendencia macro no 1D (preco abaixo = baixa estrutural).
    # Requer pelo menos 200 candles; se o df for menor, retorna NaN -> tratado como 0.
    ema200_s = ta.trend.EMAIndicator(close, window=200).ema_indicator()
    ema200   = ema200_s.iloc[-1] if len(ema200_s) >= 200 else float("nan")

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
    padrao = detectar_padroes_graficos(df, niveis)

    return {
        "preco":      float(close.iloc[-1]),
        "rsi":        float(rsi_val),
        "macd_hist":  float(mh_atual),
        "macd_prev":  float(mh_prev),
        "ema9":       float(ema9),
        "ema21":      float(ema21),
        "ema200":     float(ema200) if ema200 == ema200 else 0.0,  # nan -> 0
        "vol_ratio":  float(vol_ratio),
        "atr":        float(atr_val),
        "atr_pct":    float(atr_pct),
        "adx":        float(adx_val) if adx_val == adx_val else 0.0,
        "dmi_plus":   float(dmi_plus) if dmi_plus == dmi_plus else 0.0,
        "dmi_minus":  float(dmi_minus) if dmi_minus == dmi_minus else 0.0,
        "niveis":     niveis,
        "diverg":     diverg,
        "cvd":        cvd,
        "ob":         ob,
        "padrao":     padrao,
        "closes":     [float(v) for v in close.iloc[-30:].tolist()],
        "timestamps": [int(v) for v in df["timestamp"].iloc[-30:].tolist()],
    }
