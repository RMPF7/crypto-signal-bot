# ⚡ Crypto Signal Bot — MEXC Futures

Web App de sinais Long/Short para MEXC com gestão de risco automática.

---

## 🔑 Como gerar sua API Key na MEXC

1. Acesse **mexc.com** e faça login
2. Vá em **Perfil → Gerenciamento de API**
3. Clique em **Criar API**
4. Nome: `signal-bot` (qualquer nome)
5. Permissões necessárias:
   - ✅ **Leitura de conta** (obrigatório)
   - ✅ **Futuros — leitura** (para ver posições e saldo)
   - ❌ Não marque permissão de trading por enquanto
6. Salve a **API Key** e o **Secret Key** em local seguro

> ⚠️ A Secret Key aparece apenas uma vez. Anote imediatamente.

---

## 📊 Lógica de sinais

| Confirmações | Classificação | Risco | Alavancagem |
|---|---|---|---|
| 4/5 indicadores | ⚠️ ARRISCADO | 5% do saldo | 10x |
| 5/5 indicadores | ✅ SEGURO | 10% do saldo | 10x |

**Indicadores usados:**
1. RSI (14) — sobrecomprado/sobrevendido
2. EMA 9/21 — cruzamento de tendência
3. MACD — momentum e cruzamentos
4. Volume — confirmação por volume acima da média
5. Topos e Fundos — suporte e resistência estrutural

**Gestão de risco:**
- Stop Loss: baseado no topo/fundo mais próximo
- Take Profit: sempre 3x a distância do Stop Loss (R:R 1:3)
- Tamanho da posição calculado automaticamente pelo saldo real
- Máximo 2 trades simultâneos

---

## 🚀 Deploy no Railway

### Passo 1 — GitHub
1. Crie conta em **github.com**
2. Crie repositório público chamado `crypto-signal-bot`
3. Faça upload de todos os arquivos deste projeto

### Passo 2 — Railway
1. Acesse **railway.app**
2. Clique **Start a New Project** → **Login with GitHub**
3. Selecione **Deploy from GitHub repo** → escolha `crypto-signal-bot`
4. Aguarde ~2 minutos

### Passo 3 — URL
1. No Railway: **Settings → Domains → Generate Domain**
2. Você receberá algo como: `https://signal-bot-xxxx.up.railway.app`

### Passo 4 — iPhone
1. Abra a URL no **Safari**
2. Toque em **Compartilhar → Adicionar à Tela de Início**
3. Vira um app no iPhone 📱

---

## 🧪 Testar localmente

```bash
pip install -r requirements.txt
python app.py
# Abra: http://localhost:5000
```

---

## 📁 Estrutura

```
crypto-signal-bot/
├── app.py              ← Backend Flask + indicadores + gestão de risco
├── requirements.txt    ← Dependências Python
├── Procfile            ← Config Railway (gunicorn)
├── templates/
│   └── index.html      ← Web App iOS
└── README.md
```

---

## 🔮 Próximos passos (planejado)

- [ ] Execução automática de ordens na MEXC
- [ ] Alertas push no iPhone
- [ ] Histórico de sinais
- [ ] Backtesting básico

---

> ⚠️ Este bot é uma ferramenta de análise técnica. Sempre gerencie seu próprio risco. Nunca opere com valores que não pode perder.
