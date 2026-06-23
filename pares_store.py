"""
Persistencia simples da lista de pares que o usuario quer monitorar.

[FIX 23/06] O painel web guardava a lista de pares so no navegador
(sessionStorage), entao o worker de background nao tinha como saber quais
pares o usuario realmente acompanha - ficava restrito a uma lista fixa
(PARES_DEFAULT). Este modulo guarda a lista num arquivo JSON no servidor,
atualizado pelo painel a cada adicao/remocao de par, e lido pelo worker
a cada ciclo - assim os dois ficam sempre sincronizados.

Nota: o filesystem do Railway nao e garantido como persistente entre
deploys/restarts. Se o arquivo nao existir (ex: apos um redeploy), cai de
volta no PARES_DEFAULT automaticamente - comportamento seguro, nunca quebra.
"""
import json
import os
import threading

from config import PARES_DEFAULT

_CAMINHO_ARQUIVO = os.path.join(os.path.dirname(__file__), "pares_monitorados.json")
_lock = threading.Lock()


def ler_pares():
    """Le a lista de pares do arquivo. Se nao existir ou estiver corrompido,
    retorna PARES_DEFAULT sem lançar erro."""
    with _lock:
        try:
            if not os.path.exists(_CAMINHO_ARQUIVO):
                return list(PARES_DEFAULT)
            with open(_CAMINHO_ARQUIVO, "r") as f:
                data = json.load(f)
            pares = data.get("pares", [])
            if not pares or not isinstance(pares, list):
                return list(PARES_DEFAULT)
            return pares
        except Exception as e:
            print(f"[pares_store] erro ao ler pares, usando default: {e}")
            return list(PARES_DEFAULT)


def salvar_pares(pares):
    """Grava a lista de pares no arquivo. Chamado pelo painel sempre que
    o usuario adiciona ou remove um par."""
    pares = [p.upper().strip() for p in pares if p and isinstance(p, str)]
    pares = [p if p.endswith("USDT") else p + "USDT" for p in pares]
    pares = list(dict.fromkeys(pares))  # remove duplicatas, preserva ordem
    if not pares:
        pares = list(PARES_DEFAULT)
    with _lock:
        try:
            with open(_CAMINHO_ARQUIVO, "w") as f:
                json.dump({"pares": pares}, f)
            return pares
        except Exception as e:
            print(f"[pares_store] erro ao salvar pares: {e}")
            return pares
