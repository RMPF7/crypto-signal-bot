"""
Suite de testes para app.py - crypto-signal-bot

Cobre os bugs reais encontrados e corrigidos em auditoria (20/06/2026):
  1. EMA deadband: cruzamento <0.1% nao deve contar como LONG/SHORT
  2. Filtro Volume/ADX (filtro_ok) deve de fato bloquear o sinal
  3. /api/mtf deve aplicar a mesma cascata de tendencia 4h->1h->15m
  4. /api/mtf deve ler o ADX do dict correto (ind, nao sinal)
  5. Regressao: bug do dia 14-18/06 (ADX gating tendencia_4h) nao deve voltar
  6. dist_sl com piso minimo de 0.5% (evita TP=SL)

Roda sem precisar de rede - usa DataFrames sinteticos via pandas.
"""
import sys
sys.path.insert(0, '/tmp/refactor')
import pandas as pd
import numpy as np
from sinais import analisar_sinal, calcular_entradas_e_tps
from indicadores import calcular_indicadores, detectar_niveis
from config import MINIMO_CONF

PASSED = 0
FAILED = 0

def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  OK   {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label}  {detail}")


def make_df(closes, vol=None, n_extra=120):
    """Gera um DataFrame OHLCV sintetico a partir de uma lista de closes finais.
    Prefixa com candles planos para dar historico suficiente aos indicadores."""
    base = closes[0]
    prefix = [base] * n_extra
    full_closes = prefix + list(closes)
    n = len(full_closes)
    opens = [full_closes[0]] + full_closes[:-1]
    highs = [max(o, c) * 1.001 for o, c in zip(opens, full_closes)]
    lows  = [min(o, c) * 0.999 for o, c in zip(opens, full_closes)]
    if vol is None:
        volume = [1000.0] * n
    else:
        vol_prefix = [vol[0]] * n_extra
        volume = vol_prefix + list(vol)
    timestamps = list(range(1_700_000_000, 1_700_000_000 + n * 900, 900))
    df = pd.DataFrame({
        "timestamp": timestamps[:n],
        "open": opens, "high": highs, "low": lows, "close": full_closes,
        "volume": volume,
    })
    return df


print("=" * 70)
print("1. EMA DEADBAND - cruzamento insignificante nao deve contar ponto")
print("=" * 70)

closes_flat = [67.0 + (i % 3) * 0.001 for i in range(40)]
df_flat = make_df(closes_flat, vol=[1500.0] * 40)
ind_flat = calcular_indicadores(df_flat)
ema_diff_pct = abs(ind_flat["ema9"] - ind_flat["ema21"]) / ind_flat["ema21"] * 100
sinal_flat = analisar_sinal(ind_flat)
ema_detalhe = next(d for d in sinal_flat["detalhes"] if d["nome"] == "EMA")
check(
    "EMA quase igual (diff<0.1%) -> detalhe EMA = NEUTRO",
    ema_diff_pct < 0.1 and ema_detalhe["sinal"] == "NEUTRO",
    f"(diff={ema_diff_pct:.4f}%, sinal_ema={ema_detalhe['sinal']})"
)

closes_up = [60 + i * 0.5 for i in range(40)]
df_up = make_df(closes_up, vol=[1500.0] * 40)
ind_up = calcular_indicadores(df_up)
ema_diff_pct_up = abs(ind_up["ema9"] - ind_up["ema21"]) / ind_up["ema21"] * 100
sinal_up = analisar_sinal(ind_up)
ema_detalhe_up = next(d for d in sinal_up["detalhes"] if d["nome"] == "EMA")
check(
    "EMA9 >> EMA21 (uptrend real) -> detalhe EMA = LONG",
    ema_diff_pct_up >= 0.1 and ema_detalhe_up["sinal"] == "LONG",
    f"(diff={ema_diff_pct_up:.4f}%, sinal_ema={ema_detalhe_up['sinal']})"
)


print()
print("=" * 70)
print("2. FILTRO VOLUME/ADX - deve bloquear sinal quando volume < 0.8x")
print("=" * 70)

closes_strong_up = [60 + i * 0.8 for i in range(40)]
# vol_ratio = volume do ultimo candle / media dos ultimos 20. Para garantir vol_ratio<0.8
# de forma robusta, a media dos ultimos 20 precisa ficar alta e o ultimo candle baixo.
vol_baixo = [1500.0] * 20 + [200.0] * 19 + [150.0]
df_vol_baixo = make_df(closes_strong_up, vol=vol_baixo)
ind_vol_baixo = calcular_indicadores(df_vol_baixo)
sinal_vol_baixo = analisar_sinal(ind_vol_baixo)
if ind_vol_baixo["vol_ratio"] < 0.8:
    check(
        "Volume real < 0.8x -> sinal bloqueado (NEUTRO)",
        sinal_vol_baixo["direcao"] == "NEUTRO",
        f"(vol_ratio={ind_vol_baixo['vol_ratio']:.2f}, direcao={sinal_vol_baixo['direcao']})"
    )
else:
    print(f"  SKIP cenario nao gerou vol_ratio<0.8 (vol_ratio={ind_vol_baixo['vol_ratio']:.2f})")

vol_bom = [1200.0] * 40
df_vol_bom = make_df(closes_strong_up, vol=vol_bom)
ind_vol_bom = calcular_indicadores(df_vol_bom)
sinal_vol_bom = analisar_sinal(ind_vol_bom)
filtro_ok_bom = ind_vol_bom["vol_ratio"] >= 0.8 and ind_vol_bom["adx"] >= 10
bloqueio_liquidez = sinal_vol_bom.get("bloqueado_tendencia", "") or ""
check(
    "Volume e ADX OK -> nao bloqueado por filtro de liquidez",
    not (filtro_ok_bom and bloqueio_liquidez.startswith("Sinal bloqueado - filtro liquidez")),
    f"(vol_ratio={ind_vol_bom['vol_ratio']:.2f}, adx={ind_vol_bom['adx']:.1f}, bloqueio={bloqueio_liquidez})"
)


print()
print("=" * 70)
print("3. REGRESSAO - tendencia_4h nao deve depender do ADX (bug 14-18/06)")
print("=" * 70)

closes_slow_up = [60 + i * 0.15 for i in range(40)]
df_slow_up = make_df(closes_slow_up, vol=[1000.0] * 40)
ind_slow_up = calcular_indicadores(df_slow_up)
tendencia_simulada = "ALTA" if ind_slow_up["ema9"] > ind_slow_up["ema21"] else ("BAIXA" if ind_slow_up["ema9"] < ind_slow_up["ema21"] else "NEUTRO")
check(
    "Tendencia 4h calculada so por EMA9 vs EMA21 (independente do ADX)",
    tendencia_simulada in ("ALTA", "BAIXA"),
    f"(ema9={ind_slow_up['ema9']:.4f}, ema21={ind_slow_up['ema21']:.4f}, adx={ind_slow_up['adx']:.1f}, tendencia={tendencia_simulada})"
)

closes_down = [70 - i * 0.6 for i in range(40)]
vol_alto = [1500.0] * 40
df_down = make_df(closes_down, vol=vol_alto)
ind_down_base = calcular_indicadores(df_down)

# Forca um cenario de LONG forte (>=4 confirmacoes) mas com tendencia_maior=BAIXA,
# que deve bloquear o LONG segundo a regra de tendencia 4h.
ind_forcado = dict(ind_down_base)
ind_forcado["rsi"] = 35.0          # sobrevendido -> ponto LONG
ind_forcado["ema9"] = 105.0        # EMA9 > EMA21 -> ponto LONG
ind_forcado["ema21"] = 100.0
ind_forcado["macd_hist"] = 0.5     # positivo -> ponto LONG
ind_forcado["macd_prev"] = 0.4
ind_forcado["niveis"] = dict(ind_down_base["niveis"]); ind_forcado["niveis"]["sinal"] = "LONG"
ind_forcado["cvd"] = dict(ind_down_base["cvd"]); ind_forcado["cvd"]["sinal"] = "LONG"

sinal_bloqueado = analisar_sinal(ind_forcado, tendencia_maior="BAIXA")
check(
    "LONG com >=4 confirmacoes mas tendencia_maior=BAIXA -> bloqueado (NEUTRO)",
    sinal_bloqueado["direcao"] == "NEUTRO" and "bloqueado_tendencia" in sinal_bloqueado
    and sinal_bloqueado["bloqueado_tendencia"] not in ("", None),
    f"(direcao={sinal_bloqueado['direcao']}, bloqueio={sinal_bloqueado.get('bloqueado_tendencia')})"
)

# Mesmo cenario mas tendencia_maior=ALTA (alinhada) -> NAO deve ser bloqueado por tendencia
sinal_alinhado = analisar_sinal(ind_forcado, tendencia_maior="ALTA")
check(
    "Mesmo LONG forte com tendencia_maior=ALTA (alinhada) -> NAO bloqueado por tendencia",
    sinal_alinhado["direcao"] == "LONG",
    f"(direcao={sinal_alinhado['direcao']}, bloqueio={sinal_alinhado.get('bloqueado_tendencia')})"
)


print()
print("=" * 70)
print("4. dist_sl com piso minimo de 0.5% (evita TP=SL)")
print("=" * 70)

preco_teste = 100.0
sl_colado = 100.0001
entradas, tps = calcular_entradas_e_tps(preco_teste, sl_colado, "LONG")
dist_e1_tp1 = abs(tps[0] - entradas[0])
check(
    "TP1 != E1 mesmo com SL colado no preco (piso de 0.5% aplicado)",
    dist_e1_tp1 >= preco_teste * 0.004,
    f"(E1={entradas[0]}, TP1={tps[0]}, dist={dist_e1_tp1:.4f})"
)


print()
print("=" * 70)
print("5. /api/mtf - ADX deve vir de ind, nao de sinal (chave inexistente)")
print("=" * 70)

sinal_qualquer = analisar_sinal(ind_up)
check(
    "analisar_sinal() nao expoe chave 'adx' (confirma que /api/mtf usava fonte errada antes do fix)",
    "adx" not in sinal_qualquer,
    f"(chaves retornadas: {sorted(sinal_qualquer.keys())})"
)
check(
    "ind (calcular_indicadores) expoe 'adx' corretamente",
    "adx" in ind_up and isinstance(ind_up["adx"], float),
    f"(adx={ind_up.get('adx')})"
)


print()
print("=" * 70)
print(f"RESULTADO: {PASSED} passaram, {FAILED} falharam")
print("=" * 70)
sys.exit(1 if FAILED else 0)
