"""
Worker de background: calcula sinais e envia notificacoes Telegram de forma
PERIODICA E INDEPENDENTE do navegador/painel web estar aberto.

[FIX 23/06] Antes deste modulo, a unica forma de gerar e notificar sinais
era a rota /api/sinais ser chamada pelo frontend (setInterval de 60s no
JS do painel). Se o app/aba fosse fechado, nada era calculado nem enviado,
mesmo com o servidor Flask de pe. Esse worker roda dentro do mesmo processo
Flask via APScheduler, usando credenciais de variavel de ambiente, e nao
depende de nenhuma requisicao HTTP externa para funcionar.
"""
import time
from datetime import datetime

from config import (
    TIMEFRAMES, TELEGRAM_TOKEN_ENV, TELEGRAM_CHAT_ID_ENV,
    WORKER_INTERVAL_SEGUNDOS, WORKER_ENABLED,
)
from mexc_api import buscar_candles
from indicadores import calcular_indicadores
from sinais import analisar_sinal
from telegram import enviar_telegram, formatar_alerta_telegram
import pares_store

_sinais_notificados_worker = {}
_ultimo_ciclo = {"hora": None, "erro": None, "pares_processados": 0}


def _pares_do_worker():
    # [FIX 23/06] Le a lista sincronizada com o painel em vez de uma lista
    # fixa de variavel de ambiente - assim os pares que o usuario adiciona
    # no app sao automaticamente monitorados tambem pelo worker.
    return pares_store.ler_pares()


def checar_par(par, tg_token, tg_chat_id, notificados_dict):
    """
    Calcula a cascata 4h->1h->15m para um par e notifica via Telegram
    se houver sinal valido e ainda nao notificado na ultima hora.
    Reaproveita exatamente a mesma logica de /api/sinais (analisar_sinal),
    garantindo que o worker e o painel nunca fiquem dessincronizados.
    """
    par = par.upper().strip().replace("_", "").replace(" ", "")
    if not par.endswith("USDT"):
        par += "USDT"

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
        print(f"[worker] erro tendencia 4h {par}: {e}")

    direcao_1h = "NEUTRO"
    try:
        df_1h = buscar_candles(par, "1h")
        if df_1h is not None and len(df_1h) >= 50:
            ind_1h = calcular_indicadores(df_1h)
            sinal_1h = analisar_sinal(ind_1h, tendencia_maior=tendencia_4h, direcao_1h="NEUTRO")
            direcao_1h = sinal_1h["direcao"]
    except Exception as e:
        print(f"[worker] erro direcao 1h {par}: {e}")

    enviados = []
    for tf in TIMEFRAMES:
        try:
            df = buscar_candles(par, tf)
            if df is None or len(df) < 50:
                continue
            ind = calcular_indicadores(df)
            tm = tendencia_4h if tf != "4h" else "NEUTRO"
            d1h = direcao_1h if tf == "15m" else "NEUTRO"
            sinal = analisar_sinal(ind, tendencia_maior=tm, direcao_1h=d1h)

            if sinal["direcao"] != "NEUTRO" and sinal["forca"] >= 4 and tg_token and tg_chat_id:
                chave = f"{par}_{tf}_{sinal['direcao']}"
                ultimo = notificados_dict.get(chave, 0)
                if time.time() - ultimo > 3600:
                    msg = formatar_alerta_telegram(
                        par, tf, sinal["direcao"], ind["preco"],
                        sinal["forca"], sinal["sl"], sinal["tp"],
                        sinal["classificacao"], sinal["detalhes"],
                        entradas=sinal["entradas"], tps=sinal["tps"],
                        trailing=sinal.get("trailing"),
                        mtf_alinhado=sinal.get("mtf_alinhado", False),
                    )
                    if enviar_telegram(tg_token, tg_chat_id, msg):
                        notificados_dict[chave] = time.time()
                        enviados.append(f"{par} {tf} {sinal['direcao']}")
        except Exception as e:
            print(f"[worker] erro ao processar {par} {tf}: {e}")
    return enviados


def ciclo_worker():
    """Executado periodicamente pelo scheduler. Roda todos os pares configurados."""
    global _ultimo_ciclo
    if not WORKER_ENABLED:
        return
    if not TELEGRAM_TOKEN_ENV or not TELEGRAM_CHAT_ID_ENV:
        _ultimo_ciclo = {
            "hora": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            "erro": "TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID nao configurados nas variaveis de ambiente do Railway",
            "pares_processados": 0,
        }
        print(f"[worker] {_ultimo_ciclo['erro']}")
        return

    pares = _pares_do_worker()
    total_enviados = []
    for par in pares:
        try:
            enviados = checar_par(par, TELEGRAM_TOKEN_ENV, TELEGRAM_CHAT_ID_ENV, _sinais_notificados_worker)
            total_enviados.extend(enviados)
        except Exception as e:
            print(f"[worker] erro geral no par {par}: {e}")

    _ultimo_ciclo = {
        "hora": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "erro": None,
        "pares_processados": len(pares),
        "sinais_enviados": total_enviados,
    }
    if total_enviados:
        print(f"[worker] ciclo concluido, sinais enviados: {total_enviados}")


def iniciar_scheduler():
    """Inicia o APScheduler em background. Chamado uma vez na inicializacao do app.py."""
    if not WORKER_ENABLED:
        print("[worker] WORKER_ENABLED=false - worker de background desativado")
        return None
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        print("[worker] AVISO: apscheduler nao instalado - worker de background NAO vai rodar. "
              "Adicione 'apscheduler' ao requirements.txt")
        return None

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(ciclo_worker, "interval", seconds=WORKER_INTERVAL_SEGUNDOS, id="ciclo_sinais", max_instances=1)
    scheduler.start()
    print(f"[worker] scheduler iniciado, intervalo={WORKER_INTERVAL_SEGUNDOS}s, pares={_pares_do_worker()}")
    return scheduler
