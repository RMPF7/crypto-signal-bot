cd /home/claude/crypto-signal-bot && python3 - <<'PYEOF'
with open("app.py", "r") as f:
    c = f.read()

# Fix 1: limite máximo da MEXC é 500
old1 = 'LIMITE_CANDLES = 120'
new1 = 'LIMITE_CANDLES = 100  # MEXC max é 500, usando 100 para performance'
c = c.replace(old1, new1)

# Fix 2: garantir que 4h usa "4h" (já correto mas confirmar TF_MAP)
old2 = '    TF_MAP = {"1h": "60m", "4h": "240m", "15m": "15m"}'
new2 = '    TF_MAP = {"1h": "60m", "4h": "4h", "15m": "15m"}'
c = c.replace(old2, new2)

with open("app.py", "w") as f:
    f.write(c)
print("OK")
print("TF_MAP:", "60m" in c, "4h" in c)
PYEOF
Output

OK
TF_MAP: True True
