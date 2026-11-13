# -*- coding: utf-8 -*-
"""Formatos do VivoGo (modelo_importacao_ged.csv) para nº de acesso, CPF/CNPJ e valores.

No banco a auditoria grava como sempre gravou (linha e documento corretos só com dígitos, receita como decimal),
porque o Power BI e o sistema antigo leem essas colunas. O formato do VivoGo é só o que sai na tela, no Exportar e
na planilha:
  Nº Acesso  "11 99999-9999" (celular) · "11 3333-4444" (fixo) — DDD, espaço, número com traço
  CPF/CNPJ   "999.999.999-99" · "99.999.999/9999-99"
  Valor      "100,75" (vírgula decimal, sem separador de milhar)
"""
import re

# DDDs que existem no Brasil (Anatel)
DDDS = frozenset(
    "11 12 13 14 15 16 17 18 19 21 22 24 27 28 31 32 33 34 35 37 38 41 42 43 44 45 46 47 48 49 51 53 54 55 "
    "61 62 63 64 65 66 67 68 69 71 73 74 75 77 79 81 82 83 84 85 86 87 88 89 91 92 93 94 95 96 97 98 99".split())


def digitos(v):
    return re.sub(r"\D", "", str(v or ""))


def _so_numero(v):
    """True se o valor só tem dígitos e pontuação de máscara. Com letra ('S0 00007-1168', '27 99377-191a') a origem
    está errada: reformatar só com os dígitos inventaria outro número, então esses saem como vieram."""
    return not re.search(r"[^\d\s+().\-/]", str(v or ""))


def normalizar_linha(v):
    """Só os dígitos de DDD + número: tira o +55 do país e o 0 da operadora quando vierem colados."""
    d = digitos(v)
    if len(d) in (12, 13) and d.startswith("55"):
        d = d[2:]
    if len(d) in (11, 12) and d.startswith("0"):
        d = d[1:]
    return d


def erro_linha(v):
    """None se a linha serve (DDD válido + 8 dígitos de fixo ou 9 de celular); senão o motivo."""
    d = normalizar_linha(v)
    if len(d) not in (10, 11):
        return "Informe a linha com DDD: 10 dígitos (fixo) ou 11 (celular)."
    if d[:2] not in DDDS:
        return f"DDD {d[:2]} não existe."
    if len(d) == 11 and d[2] != "9":
        return "Celular com 11 dígitos começa com 9 depois do DDD."
    if len(d) == 10 and d[2] not in "2345":
        return "Fixo com 10 dígitos começa com 2, 3, 4 ou 5 depois do DDD (celular tem o 9)."
    return None


def linha_vivogo(v):
    """'27999824388' → '27 99982-4388'; fixo '2733334444' → '27 3333-4444'. Fora disso (ou com letra), devolve como
    veio."""
    if not _so_numero(v):
        return str(v or "").strip()
    d = normalizar_linha(v)
    if len(d) == 11:
        return f"{d[:2]} {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"{d[:2]} {d[2:6]}-{d[6:]}"
    return str(v or "").strip()


def doc_vivogo(v):
    """CPF '999.999.999-99' e CNPJ '99.999.999/9999-99'. Fora disso (ou com letra), devolve como veio."""
    if not _so_numero(v):
        return str(v or "").strip()
    d = digitos(v)
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return str(v or "").strip()


def valor_vivogo(v):
    """100.75 → '100,75' (o VivoGo lê vírgula decimal, sem ponto de milhar). Vazio fica vazio."""
    return "" if v is None or v == "" else f"{float(v):.2f}".replace(".", ",")
