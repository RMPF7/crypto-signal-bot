"""
Logica central de geracao de sinais: score dos 7 indicadores, filtros de
tendencia/MTF/padrao, trailing stop, entradas/TPs e tamanho de posicao.

Este e o modulo mais sensivel do bot - qualquer mudanca aqui afeta
diretamente quais sinais sao disparados e o dinheiro arriscado em cada um.
Sempre rodar test_app.py apos qualquer alteracao.
"""
from config import (
    RSI_SOBREVENDIDO, RSI_SOBRECOMPRADO, MINIMO_CONF,
    ALAVANCAGEM, RISCO_ARRISCADO, RISCO_SEGURO, TP_RATIO,
)


# ─────────────────────────────────────────────
# 📐  TRAILING STOP  [#3 NOVO]
# ─────────────────────────────────────────────

def calcular_trailing_stop(preco, sl, direcao):
    """
    [#3] Calcula os níveis de trailing stop para gestão manual.

    Lógica:
      - Após TP1 ser atingido: mover SL para breakeven (preço de entrada)
      - Após TP2 ser atingido: mover SL para 50% do caminho entre entrada e TP2
      - Step de trailing: 1 ATR (aproximado como % da distância do SL)

    Retorna um dict com as instruções de trailing para exibir no Telegram.
    """
    dist_sl = abs(preco - sl)
    # Step de trailing = 33% da distância até o SL (agressivo mas não muito)
    step_pct = (dist_sl / preco) * 100 * 0.33

    if direcao == "LONG":
        breakeven  = preco                          # SL vai para entrada após TP1
        apos_tp2   = preco + dist_sl * 1.0         # SL vai para TP1 após TP2
        apos_tp2_pct = (dist_sl / preco) * 100
    else:  # SHORT
        breakeven  = preco
        apos_tp2   = preco - dist_sl * 1.0
        apos_tp2_pct = (dist_sl / preco) * 100

    def r(v):
        if v >= 1000:  return round(v, 2)
        if v >= 1:     return round(v, 4)
        return round(v, 6)

    return {
        "breakeven":   r(breakeven),
        "apos_tp2":    r(apos_tp2),
        "apos_tp2_pct": round(apos_tp2_pct, 2),
        "step_pct":    round(step_pct, 2),
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
    [FIX] dist_sl tem um piso mínimo de 0.5% do preço. Sem isso, quando o
    nível de suporte/resistência detectado coincide (ou quase) com o preço
    atual, dist_sl fica ~0 e TP1/TP2/TP3/entradas todos colapsam no mesmo
    valor do SL — parecendo "TP1 = SL".
    """
    dist_sl = abs(preco - sl)
    dist_min = preco * 0.005
    if dist_sl < dist_min:
        dist_sl = dist_min

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


def analisar_sinal(ind, tendencia_maior="NEUTRO", direcao_1h="NEUTRO"):
    """
    Sistema 7/7 - conta pontos de 7 indicadores independentes.
    Sinal gerado com >= 4 confirmações na mesma direção (4/7).
    Volume e ADX atuam como FILTROS (não contam no score).
    tendencia_maior: 'ALTA', 'BAIXA' ou 'NEUTRO' - vem do 4h.
      ALTA  -> bloqueia SHORT (operar só a favor da tendência)
      BAIXA -> bloqueia LONG

    [#2 NOVO] direcao_1h: direção do sinal no 1h.
      Se o sinal é 15m, exige que o 1h esteja alinhado na mesma direção.
      Isso evita entrar em sinais de 15m que vão contra o 1h.

    Indicadores (score):
      1. RSI          - sobrecompra/sobrevenda
      2. EMA 9/21     - tendência curto prazo
      3. MACD         - momentum / cruzamento
      4. Topos/Fundos - suporte e resistência estrutural
      5. CVD          - pressão compradora/vendedora real
      6. Order Blocks - zonas institucionais
      7. Divergência RSI - reversão antecipada

    Filtros (bloqueiam sinal se não passarem):
      - ADX >= 10  (mercado em tendência, não lateral)
      - Volume >= 0.8x da média (liquidez mínima)
      [FIX 20/06] Esses filtros agora de fato bloqueiam o sinal (ver abaixo).
      Antes eram so calculados e exibidos no Telegram sem afetar a direcao.
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
    # [FIX] Deadband de 0.1%: cruzamentos muito apertados (EMA9 ~= EMA21) sao
    # ruido de indecisao, nao confirmacao real de direcao. Abaixo da margem,
    # o ponto vira NEUTRO em vez de forcar LONG/SHORT por uma diferenca minima.
    ema_diff_pct = abs(ind["ema9"] - ind["ema21"]) / ind["ema21"] * 100 if ind["ema21"] else 0
    EMA_DEADBAND_PCT = 0.1
    if ema_diff_pct < EMA_DEADBAND_PCT:
        detalhes.append({"nome":"EMA","valor":"9≈21","sinal":"NEUTRO","desc":f"EMA9≈EMA21 (cruzamento fraco, {ema_diff_pct:.3f}% - indecisao)"})
    elif ind["ema9"] > ind["ema21"]:
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
        detalhes.append({"nome":"CVD","valor":"-","sinal":"NEUTRO","desc":ind["cvd"]["desc"]})

    # ── 6. Order Blocks ─────────────────────────
    ob_sinal = ind["ob"]["sinal"]
    if ob_sinal == "LONG":
        conf_long.append("OB")
        detalhes.append({"nome":"OB","valor":"Suporte","sinal":"LONG","desc":ind["ob"]["desc"]})
    elif ob_sinal == "SHORT":
        conf_short.append("OB")
        detalhes.append({"nome":"OB","valor":"Resist.","sinal":"SHORT","desc":ind["ob"]["desc"]})
    else:
        detalhes.append({"nome":"OB","valor":"-","sinal":"NEUTRO","desc":ind["ob"]["desc"]})

    # ── 7. Divergência RSI ──────────────────────
    div = ind["diverg"]
    if div["bullish"]:
        conf_long.append("Diverg")
        detalhes.append({"nome":"Diverg","valor":"Bull","sinal":"LONG","desc":div["desc"]})
    elif div["bearish"]:
        conf_short.append("Diverg")
        detalhes.append({"nome":"Diverg","valor":"Bear","sinal":"SHORT","desc":div["desc"]})
    else:
        detalhes.append({"nome":"Diverg","valor":"-","sinal":"NEUTRO","desc":div["desc"]})

    # ── Padrão Gráfico [FILTRO - não soma score] ────────────────────────
    padrao = ind.get("padrao", {"sinal": "NEUTRO", "forca": "FRACO", "padrao": "Nenhum", "desc": ""})
    detalhes.append({"nome":"Padrao","valor":padrao["padrao"],"sinal":padrao["sinal"] if padrao["sinal"] != "NEUTRO" else "NEUTRO","desc":padrao["desc"]})

    # ── Filtros (Volume + ADX) ───────────────────
    adx_ok     = bool(ind["adx"] >= 10)
    volume_ok  = bool(ind["vol_ratio"] >= 0.8)
    filtro_ok  = adx_ok and volume_ok

    adx_desc   = f"ADX {ind['adx']:.1f} ({'✓ tendencia' if adx_ok else '✗ lateral'})"
    vol_desc   = f"Volume {ind['vol_ratio']:.1f}x ({'✓ ok' if volume_ok else '✗ fraco'})"
    detalhes.append({"nome":"ADX","valor":f"{ind['adx']:.1f}","sinal":"NEUTRO" if not adx_ok else "INFO","desc":adx_desc})
    detalhes.append({"nome":"Volume","valor":f"{ind['vol_ratio']:.1f}x","sinal":"NEUTRO" if not volume_ok else "INFO","desc":vol_desc})

    n_long  = len(conf_long)
    n_short = len(conf_short)
    preco   = ind["preco"]

    # ── [FIX 20/06] Filtro Volume+ADX agora bloqueia de fato ────────────────
    # filtro_ok era calculado mas nunca verificado - sinais disparavam mesmo
    # com volume muito abaixo da media (ex: 0.2x), explicando entradas de
    # baixa conviccao classificadas como ARRISCADO que vinham passando.
    if not filtro_ok and (n_long >= MINIMO_CONF or n_short >= MINIMO_CONF):
        motivo = []
        if not adx_ok: motivo.append(f"ADX {ind['adx']:.1f} < 10 (lateral)")
        if not volume_ok: motivo.append(f"Volume {ind['vol_ratio']:.1f}x < 0.8x (fraco)")
        return {
            "direcao": "NEUTRO", "forca": max(n_long, n_short),
            "n_long": n_long, "n_short": n_short, "detalhes": detalhes,
            "sl": None, "tp": None, "entradas": [], "tps": [], "trailing": None,
            "risco_pct": 0, "classificacao": "NEUTRO",
            "filtro_adx": adx_ok, "filtro_volume": volume_ok,
            "bloqueado_tendencia": "Sinal bloqueado - filtro liquidez/tendencia: " + "; ".join(motivo),
            "mtf_alinhado": False,
        }

    # ── Filtro de tendência maior (4h) ──────────────────────────────────────
    if tendencia_maior == "ALTA" and n_short >= MINIMO_CONF and n_short > n_long:
        return {
            "direcao": "NEUTRO", "forca": n_short,
            "n_long": n_long, "n_short": n_short, "detalhes": detalhes,
            "sl": None, "tp": None, "entradas": [], "tps": [], "trailing": None,
            "risco_pct": 0, "classificacao": "NEUTRO",
            "filtro_adx": adx_ok, "filtro_volume": volume_ok,
            "bloqueado_tendencia": "SHORT bloqueado - tendência maior é ALTA",
            "mtf_alinhado": False,
        }
    if tendencia_maior == "BAIXA" and n_long >= MINIMO_CONF and n_long > n_short:
        return {
            "direcao": "NEUTRO", "forca": n_long,
            "n_long": n_long, "n_short": n_short, "detalhes": detalhes,
            "sl": None, "tp": None, "entradas": [], "tps": [], "trailing": None,
            "risco_pct": 0, "classificacao": "NEUTRO",
            "filtro_adx": adx_ok, "filtro_volume": volume_ok,
            "bloqueado_tendencia": "LONG bloqueado - tendência maior é BAIXA",
            "mtf_alinhado": False,
        }

    # ── [#2] Filtro multi-timeframe 1h ──────────────────────────────────────
    # Se direcao_1h foi passado (ou seja, estamos no 15m), bloqueia se o 1h
    # não estiver alinhado. Para 1h e 4h, direcao_1h="NEUTRO" -> sem bloqueio.
    mtf_alinhado = False
    if direcao_1h != "NEUTRO":
        if n_long >= MINIMO_CONF and n_long > n_short and direcao_1h != "LONG":
            return {
                "direcao": "NEUTRO", "forca": n_long,
                "n_long": n_long, "n_short": n_short, "detalhes": detalhes,
                "sl": None, "tp": None, "entradas": [], "tps": [], "trailing": None,
                "risco_pct": 0, "classificacao": "NEUTRO",
                "filtro_adx": adx_ok, "filtro_volume": volume_ok,
                "bloqueado_tendencia": f"LONG 15m bloqueado - 1h está {direcao_1h}",
                "mtf_alinhado": False,
            }
        if n_short >= MINIMO_CONF and n_short > n_long and direcao_1h != "SHORT":
            return {
                "direcao": "NEUTRO", "forca": n_short,
                "n_long": n_long, "n_short": n_short, "detalhes": detalhes,
                "sl": None, "tp": None, "entradas": [], "tps": [], "trailing": None,
                "risco_pct": 0, "classificacao": "NEUTRO",
                "filtro_adx": adx_ok, "filtro_volume": volume_ok,
                "bloqueado_tendencia": f"SHORT 15m bloqueado - 1h está {direcao_1h}",
                "mtf_alinhado": False,
            }
        # Se chegou aqui com sinal, o 1h está alinhado
        mtf_alinhado = True

    # ── Filtro de Padrão Gráfico FORTE contrário ────────────────────────────
    # Um padrão gráfico forte (topo/fundo duplo, engolfo) na direção contrária
    # ao sinal bloqueia a entrada, igual ao filtro de tendência 4h.
    if padrao["forca"] == "FORTE" and padrao["sinal"] != "NEUTRO":
        if padrao["sinal"] == "SHORT" and n_long >= MINIMO_CONF and n_long > n_short:
            return {
                "direcao": "NEUTRO", "forca": n_long,
                "n_long": n_long, "n_short": n_short, "detalhes": detalhes,
                "sl": None, "tp": None, "entradas": [], "tps": [], "trailing": None,
                "risco_pct": 0, "classificacao": "NEUTRO",
                "filtro_adx": adx_ok, "filtro_volume": volume_ok,
                "bloqueado_tendencia": f"LONG bloqueado - padrão grafico forte contrário ({padrao['padrao']})",
                "mtf_alinhado": False,
            }
        if padrao["sinal"] == "LONG" and n_short >= MINIMO_CONF and n_short > n_long:
            return {
                "direcao": "NEUTRO", "forca": n_short,
                "n_long": n_long, "n_short": n_short, "detalhes": detalhes,
                "sl": None, "tp": None, "entradas": [], "tps": [], "trailing": None,
                "risco_pct": 0, "classificacao": "NEUTRO",
                "filtro_adx": adx_ok, "filtro_volume": volume_ok,
                "bloqueado_tendencia": f"SHORT bloqueado - padrão grafico forte contrário ({padrao['padrao']})",
                "mtf_alinhado": False,
            }

    # ── Geração do sinal final ───────────────────────────────────────────────
    if n_long >= MINIMO_CONF and n_long > n_short:
        direcao = "LONG"
        forca   = n_long
        sl = ind["niveis"]["sl_long"]
        distancia_sl = abs(preco - sl)
        dist_min = preco * 0.005  # [FIX] piso de 0.5% p/ evitar TP=SL quando nivel coincide com o preco
        if distancia_sl < dist_min:
            distancia_sl = dist_min
            sl = preco - dist_min
        tp = preco + distancia_sl * TP_RATIO
        risco_pct = RISCO_SEGURO if forca >= 6 else RISCO_ARRISCADO
        classificacao = "SEGURO" if forca >= 6 else "ARRISCADO"
        # Padrão gráfico fraco contrário rebaixa a classificação para ARRISCADO
        if padrao["sinal"] == "SHORT":
            classificacao = "ARRISCADO"
            risco_pct = RISCO_ARRISCADO
        entradas, tps = calcular_entradas_e_tps(preco, sl, "LONG")
        trailing = calcular_trailing_stop(preco, sl, "LONG")  # [#3]
    elif n_short >= MINIMO_CONF and n_short > n_long:
        direcao = "SHORT"
        forca   = n_short
        sl = ind["niveis"]["sl_short"]
        distancia_sl = abs(sl - preco)
        dist_min = preco * 0.005  # [FIX] piso de 0.5% p/ evitar TP=SL quando nivel coincide com o preco
        if distancia_sl < dist_min:
            distancia_sl = dist_min
            sl = preco + dist_min
        tp = preco - distancia_sl * TP_RATIO
        risco_pct = RISCO_SEGURO if forca >= 6 else RISCO_ARRISCADO
        classificacao = "SEGURO" if forca >= 6 else "ARRISCADO"
        # Padrão gráfico fraco contrário rebaixa a classificação para ARRISCADO
        if padrao["sinal"] == "LONG":
            classificacao = "ARRISCADO"
            risco_pct = RISCO_ARRISCADO
        entradas, tps = calcular_entradas_e_tps(preco, sl, "SHORT")
        trailing = calcular_trailing_stop(preco, sl, "SHORT")  # [#3]
    else:
        direcao = "NEUTRO"
        forca   = max(n_long, n_short)
        sl = tp = None
        distancia_sl = 0
        risco_pct = 0
        classificacao = "NEUTRO"
        entradas, tps = [], []
        trailing = None

    return {
        "direcao": direcao, "forca": forca,
        "n_long": n_long, "n_short": n_short,
        "detalhes": detalhes,
        "sl": round(sl, 6) if sl else None,
        "tp": round(tp, 6) if tp else None,
        "entradas": entradas,
        "tps": tps,
        "trailing": trailing,           # [#3]
        "mtf_alinhado": mtf_alinhado,   # [#2]
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
