import unicodedata


def chave_nome(texto):
    """Nome comparável: sem acento, sem diferença de maiúscula e com espaços normalizados."""
    t = unicodedata.normalize('NFKD', str(texto or ''))
    return ' '.join(''.join(c for c in t if not unicodedata.combining(c)).lower().split())
