"""
🤖 Crypto Signal Bot - MEXC Futures
- Autenticação via API Key/Secret
- Gestão de risco: 4/7 -> 5% · 6/7+ -> 10% · Alavancagem 10x
- Stop Loss automático (topos/fundos) · Take Profit 3:1
- Máximo 2 trades simultâneos
- 3 timeframes: 15m, 1h, 4h
- Indicadores: RSI, EMA 9/21, MACD, Topos/Fundos, CVD, Order Blocks, Divergência RSI (4/7 para sinal)
- Filtros: ADX >= 10 (mercado em tendência) + Volume >= 0.8x da média
- Notificações Telegram com 3 entradas parciais e 3 TPs

Este arquivo contem APENAS as rotas Flask. A logica fica em:
  config.py      - constantes
  mexc_api.py     - autenticacao MEXC, saldo, posicoes, candles
  telegram.py     - envio e formatacao de alertas
  indicadores.py  - calculo dos indicadores tecnicos
  sinais.py       - score, filtros de tendencia/MTF/padrao, trailing, posicao

MELHORIAS v2:
  [#1] detectar_niveis: margem 1.5% -> 2.5% + resolve conflito suporte/resistência simultâneo
  [#2] Confirmação multi-timeframe: sinal 15m só é gerado se 1h estiver alinhado na mesma direção
  [#3] Trailing Stop após TP1: bot calcula e informa no Telegram o nível de trailing stop

AUDITORIA 20/06/2026 (divisao em modulos + fixes):
  - Filtro Volume/ADX agora bloqueia de fato o sinal (antes so era exibido)
  - EMA deadband de 0.1% evita contar cruzamentos insignificantes como direcao
  - /api/mtf aplica a mesma cascata de tendencia 4h->1h->15m usada em /api/sinais
  - /api/mtf le o ADX do dict correto (ind, nao sinal - essa chave nao existia)
"""

import time
from datetime import datetime
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

from config import PARES_DEFAULT, TIMEFRAMES, MAX_TRADES, RISCO_SEGURO, RISCO_ARRISCADO
from mexc_api import get_futures_balance, get_open_positions, validate_api_keys, buscar_candles
from telegram import enviar_telegram, formatar_alerta_telegram
from indicadores import calcular_indicadores
from sinais import analisar_sinal, calcular_tamanho_posicao
import worker
import pares_store

app = Flask(__name__)
CORS(app)

_sinais_notificados = {}

# [FIX 23/06] Inicia o worker de background assim que o processo Flask sobe -
# roda mesmo sem nenhum navegador conectado ao painel. Ver worker.py.
_scheduler = worker.iniciar_scheduler()


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
        par = par.upper().strip().replace("_", "").replace(" ", "")
        if not par.endswith("USDT"):
            par = par + "USDT"
        par_data = {"par": par, "timeframes": {}}

        # ── Calcula tendência de fundo (4h) ──────────────────────────────────
        # Direção (ALTA/BAIXA) vem só da EMA9 vs EMA21, sem depender do ADX.
        tendencia_4h = "NEUTRO"
        try:
            df_4h = buscar_candles(par, "4h")
            if df_4h is not None and len(df_4h) >= 50:
                ind_4h = calcular_indicadores(df_4h)
                if ind_4h["ema9"] > ind_4h["ema21"]:
                    tendencia_4h = "ALTA"
                elif ind_4h["ema9"] < ind_4h["ema21"]:
                    tendencia_4h = "BAIXA"
        except Exception as e:
            print(f"Erro ao calcular tendencia 4h para {par}: {e}")

        # [#2] Calcula direção do 1h para filtrar o 15m ───────────────────────
        direcao_1h = "NEUTRO"
        try:
            df_1h = buscar_candles(par, "1h")
            if df_1h is not None and len(df_1h) >= 50:
                ind_1h = calcular_indicadores(df_1h)
                sinal_1h = analisar_sinal(ind_1h, tendencia_maior=tendencia_4h, direcao_1h="NEUTRO")
                direcao_1h = sinal_1h["direcao"]
        except Exception as e:
            print(f"Erro ao calcular direcao 1h para {par}: {e}")

        for tf in TIMEFRAMES:
            try:
                df = buscar_candles(par, tf)
                if df is None or len(df) < 50:
                    par_data["timeframes"][tf] = {"erro": f"Par {par} nao encontrado ou dados insuficientes"}
                    continue

                ind = calcular_indicadores(df)

                # [#2] Passa direcao_1h apenas para o 15m
                tm = tendencia_4h if tf != "4h" else "NEUTRO"
                d1h = direcao_1h if tf == "15m" else "NEUTRO"

                sinal = analisar_sinal(ind, tendencia_maior=tm, direcao_1h=d1h)

                gestao = {}
                if saldo_disponivel > 0 and sinal["direcao"] != "NEUTRO":
                    gestao = calcular_tamanho_posicao(saldo_disponivel, sinal, ind["preco"])

                # Notificação Telegram
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
                            trailing=sinal.get("trailing"),       # [#3]
                            mtf_alinhado=sinal.get("mtf_alinhado", False),  # [#2]
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
                    "trailing":      sinal.get("trailing"),        # [#3]
                    "mtf_alinhado":  sinal.get("mtf_alinhado", False),  # [#2]
                    "classificacao": sinal["classificacao"],
                    "risco_pct":     sinal["risco_pct"],
                    "gestao":        gestao,
                    "topos":         ind["niveis"]["topos"],
                    "fundos":        ind["niveis"]["fundos"],
                    "closes":        ind["closes"],
                    "timestamps":    ind["timestamps"],
                    "rsi":           float(round(ind["rsi"], 2)),
                    "ema9":          float(round(ind["ema9"], 4)),
                    "ema21":         float(round(ind["ema21"], 4)),
                    "vol_ratio":     float(round(ind["vol_ratio"], 2)),
                    "adx":           float(round(ind["adx"], 1)),
                    "filtro_adx":    bool(sinal.get("filtro_adx", True)),
                    "filtro_volume": bool(sinal.get("filtro_volume", True)),
                    "tendencia_4h":  tendencia_4h,
                    "direcao_1h":    direcao_1h,   # [#2]
                    "bloqueado_tendencia": sinal.get("bloqueado_tendencia", ""),
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


@app.route("/api/worker-pares", methods=["GET", "POST"])
def api_worker_pares():
    """[FIX 23/06] Sincroniza a lista de pares entre o painel web e o worker
    de background. O painel chama POST sempre que o usuario adiciona/remove
    um par; o worker chama (indiretamente, via pares_store.ler_pares) a cada
    ciclo para saber quais pares monitorar."""
    if request.method == "POST":
        body = request.json or {}
        pares = body.get("pares", [])
        salvos = pares_store.salvar_pares(pares)
        return jsonify({"ok": True, "pares": salvos})
    return jsonify({"pares": pares_store.ler_pares()})


@app.route("/api/worker-status")
def api_worker_status():
    """[FIX 23/06] Permite checar se o worker de background esta rodando,
    quando foi o ultimo ciclo, e se houve erro (ex: variaveis de ambiente
    do Telegram nao configuradas)."""
    return jsonify({
        "worker_ativo": _scheduler is not None and _scheduler.running if _scheduler else False,
        "ultimo_ciclo": worker._ultimo_ciclo,
        "intervalo_segundos": __import__("config").WORKER_INTERVAL_SEGUNDOS,
        "telegram_configurado": bool(__import__("config").TELEGRAM_TOKEN_ENV and __import__("config").TELEGRAM_CHAT_ID_ENV),
    })


@app.route("/api/meu-ip")
def api_meu_ip():
    import requests
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
        "✅ <b>Signal Bot conectado!</b>\n\nVoce vai receber alertas aqui quando surgir sinal de 4/7 ou mais indicadores.")
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
            dados[tf]={"ind":calcular_indicadores(df)}  # sinal calculado depois, em cascata
        tf4=dados.get("4h"); tf1=dados.get("1h"); tf15=dados.get("15m")
        if not all([tf4,tf1,tf15]): resultado.append({"par":par,"mtf_sinal":"NEUTRO","erro":"Dados insuficientes"}); continue

        # Mesma cascata de tendencia usada em /api/sinais: 4h -> 1h -> 15m.
        tf4["sinal"] = analisar_sinal(tf4["ind"])
        tendencia_4h_mtf = "NEUTRO"
        if tf4["ind"]["ema9"] > tf4["ind"]["ema21"]: tendencia_4h_mtf = "ALTA"
        elif tf4["ind"]["ema9"] < tf4["ind"]["ema21"]: tendencia_4h_mtf = "BAIXA"

        tf1["sinal"] = analisar_sinal(tf1["ind"], tendencia_maior=tendencia_4h_mtf, direcao_1h="NEUTRO")
        tf15["sinal"] = analisar_sinal(tf15["ind"], tendencia_maior=tendencia_4h_mtf, direcao_1h=tf1["sinal"]["direcao"])

        d4=tf4["sinal"]["direcao"]; d1=tf1["sinal"]["direcao"]; d15=tf15["sinal"]["direcao"]
        # ADX correto vem de ind, nao de sinal (essa chave nao existe no retorno de analisar_sinal)
        adx4=tf4["ind"].get("adx",0)
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


# ── Sempre retorna JSON em erros, nunca HTML ──────────────────────────────────
@app.errorhandler(400)
def bad_request(e):
    return jsonify({"erro": f"Bad request: {str(e)}"}), 400

@app.errorhandler(404)
def not_found(e):
    return jsonify({"erro": "Rota nao encontrada"}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({"erro": f"Erro interno: {str(e)}"}), 500

@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    print(traceback.format_exc())
    return jsonify({"erro": f"Erro inesperado: {str(e)[:200]}"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
