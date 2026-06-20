"""
Envio e formatacao de alertas via Telegram.
"""
import requests
from datetime import datetime


def enviar_telegram(token, chat_id, mensagem):
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": mensagem, "parse_mode": "HTML"}
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram erro: {e}")
        return False


def formatar_alerta_telegram(par, tf, direcao, preco, forca, sl, tp, classificacao, detalhes,
                              entradas=None, tps=None, trailing=None, mtf_alinhado=False):
    emoji = "🟢" if direcao == "LONG" else "🔴"
    cls_txt = "✅ SEGURO (10%)" if classificacao == "SEGURO" else "⚠️ ARRISCADO (5%)"
    estrelas = "⭐" * forca + "☆" * (7 - forca)

    msg = f"{emoji} <b>SINAL {direcao} - {par.replace('USDT', '/USDT')}</b>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"⏱ Timeframe: <b>{tf}</b>\n"

    # [#2] Indica se o sinal foi confirmado pelo timeframe maior
    if mtf_alinhado:
        msg += "🔗 Multi-TF: <b>✅ 1h alinhado</b>\n"

    msg += f"💰 Preço atual: <b>${preco:,.4f}</b>\n"
    msg += f"📊 Força: {estrelas} ({forca}/7)\n"
    msg += f"🏷 Classificação: {cls_txt}\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"

    # 3 Entradas parciais
    if entradas and len(entradas) == 3:
        msg += "📥 <b>Entradas parciais:</b>\n"
        pcts = ["33%", "33%", "34%"]
        labels = ["agora", "se cair", "se cair mais"] if direcao == "LONG" else ["agora", "se subir", "se subir mais"]
        for i, (e, pct, lbl) in enumerate(zip(entradas, pcts, labels), 1):
            msg += f"  • E{i}: <b>${e:,.4f}</b> ({pct}) - {lbl}\n"

    # Stop Loss
    if sl:
        msg += f"\n🛑 Stop Loss: <b>${sl:,.4f}</b>\n"

    # 3 Take Profits
    if tps and len(tps) == 3:
        msg += "\n📤 <b>Take Profits:</b>\n"
        pcts = ["33%", "33%", "34%"]
        ratios = ["1:1", "1:2", "1:3"]
        for i, (t, pct, ratio) in enumerate(zip(tps, pcts, ratios), 1):
            msg += f"  • TP{i}: <b>${t:,.4f}</b> ({pct}) - R/R {ratio}\n"

    # [#3] Trailing Stop - instruções para gestão manual
    if trailing:
        msg += "\n📐 <b>Trailing Stop (gestão manual):</b>\n"
        msg += f"  • Após TP1: mova SL para <b>${trailing['breakeven']:,.4f}</b> (breakeven)\n"
        msg += f"  • Após TP2: mova SL para <b>${trailing['apos_tp2']:,.4f}</b> (+{trailing['apos_tp2_pct']:.1f}%)\n"
        msg += f"  • Trailing de <b>{trailing['step_pct']:.1f}%</b> a cada movimento a favor\n"

    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += "📋 <b>Indicadores:</b>\n"
    for d in detalhes:
        ic = "🟢" if d["sinal"] == "LONG" else "🔴" if d["sinal"] == "SHORT" else "⚪"
        msg += f"{ic} {d['nome']}: {d['desc']}\n"

    msg += f"\n🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
    msg += "<i>⚠️ Sinal de análise - decisão é sempre sua.</i>"
    return msg.strip()
