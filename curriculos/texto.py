"""Normalização de texto para busca.

O banco não tem `unaccent` nem `pg_trgm` instaladas, e instalar extensão em
banco de produção não é decisão de código. Então a normalização acontece aqui,
em Python, e o resultado é gravado numa coluna própria — assim "José" acha
"jose", e "Viana" acha "viana".
"""
import re
import unicodedata


def normalizar(texto):
    """Minúsculas, sem acento, sem pontuação, espaços colapsados."""
    if not texto:
        return ''
    t = unicodedata.normalize('NFKD', str(texto))
    t = ''.join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r'[^a-zA-Z0-9\s]', ' ', t)
    return re.sub(r'\s+', ' ', t).strip().lower()


def palavras(texto):
    return [p for p in normalizar(texto).split() if p]


# Palavras que não ajudam a achar ninguém: aparecem em toda busca e em todo
# currículo. Tirar isso evita que "para" case com tudo.
VAZIAS = {
    'a', 'o', 'as', 'os', 'de', 'da', 'do', 'das', 'dos', 'e', 'em', 'no', 'na',
    'nos', 'nas', 'para', 'pra', 'por', 'com', 'um', 'uma', 'que', 'ao', 'aos',
    'the', 'loja', 'vaga', 'candidato', 'candidata', 'preciso', 'busco',
    'procuro', 'quero', 'alguem', 'pessoa',
}


def termos_uteis(texto):
    """Palavras da busca que valem comparar."""
    return [p for p in palavras(texto) if p not in VAZIAS and len(p) > 2]
