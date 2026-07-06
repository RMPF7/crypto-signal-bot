"""
Configuracoes e constantes do Crypto Signal Bot.
"""

PARES_DEFAULT = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT"]

# Timeframes que geram SINAIS de entrada (15m e 1h).
# 4h e 1d sao usados apenas como referencia de tendencia (macro/filtro).
TIMEFRAMES = ["15m", "1h"]

LIMITE_CANDLES = 100

# [FIX 23/06] Worker de background: antes, sinais so eram calculados e
# notificados via Telegram quando o painel web estava aberto (polling do
# frontend a cada 60s via setInterval). Se o app/navegador fosse fechado,
# nada era calculado nem enviado - mesmo com o servidor Flask rodando.
# Essas variaveis de ambiente permitem que o worker em background rode
# de forma totalmente independente do navegador, usando as credenciais
# configuradas direto no Railway (Settings > Variables).
import os
TELEGRAM_TOKEN_ENV   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID_ENV  = os.environ.get("TELEGRAM_CHAT_ID", "")
WORKER_PARES_ENV      = os.environ.get("WORKER_PARES", "")  # ex: "BTCUSDT,ETHUSDT,SOLUSDT,HYPEUSDT"
WORKER_INTERVAL_SEGUNDOS = int(os.environ.get("WORKER_INTERVAL_SEGUNDOS", "60"))
WORKER_ENABLED = os.environ.get("WORKER_ENABLED", "true").lower() in ("1", "true", "yes")

RSI_SOBREVENDIDO  = 40
RSI_SOBRECOMPRADO = 60
VOLUME_MULT       = 1.4
MINIMO_CONF       = 4  # 4/7 confirmacoes para gerar sinal (ARRISCADO=4-5, SEGURO=6-7)

ALAVANCAGEM     = 10
RISCO_ARRISCADO = 0.05
RISCO_SEGURO    = 0.10
TP_RATIO        = 3.0
MAX_TRADES      = 2

MEXC_BASE      = "https://contract.mexc.com"
MEXC_SPOT_BASE = "https://api.mexc.com"

# Mapeamento timeframe interno -> string aceita pela MEXC klines API
TF_MAP = {"1h": "60m", "4h": "4h", "15m": "15m", "1d": "1d"}

# [FIX] Alguns pares na MEXC (geralmente acoes tokenizadas / pre-IPO) nao
# seguem o padrao XXXUSDT - alguns so existem na API de contratos/futuros,
# com simbolo no formato BASE_USDT (com underscore).
# Mapeamento: base digitada pelo usuario -> simbolo EXATO de contrato na MEXC.
PARES_ESPECIAIS = {
    "SPCX": "SPCXSTOCK_USDT",
}
