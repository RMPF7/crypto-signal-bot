"""
Configuracoes e constantes do Crypto Signal Bot.
"""

PARES_DEFAULT = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT"]
TIMEFRAMES = ["15m", "1h", "4h"]
LIMITE_CANDLES = 100

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

TF_MAP = {"1h": "60m", "4h": "4h", "15m": "15m"}

# [FIX] Alguns pares na MEXC (geralmente acoes tokenizadas / pre-IPO) nao
# seguem o padrao XXXUSDT - usam um sufixo extra antes do USDT.
# Mapeamento: nome curto digitado pelo usuario -> ticker real na MEXC.
# Ex: usuario digita "SPCX" -> precisa virar "SPCXSTOCKUSDT" na chamada da API.
PARES_ESPECIAIS = {
    "SPCX": "SPCXSTOCKUSDT",
}
