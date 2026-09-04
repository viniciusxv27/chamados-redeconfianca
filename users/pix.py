"""Chave PIX: normalização e identificação do tipo.

O Banco Central aceita cinco formatos de chave (CPF, CNPJ, celular, e-mail e
chave aleatória) e cada um tem uma forma canônica. Guardar a chave "como o
colaborador digitou" dá trabalho ao RH depois: `(27) 99999-9999` não é o que se
cola no internet banking, e `123.456.789-09` com pontuação nem sempre é aceito.
Então normalizamos na entrada e guardamos a forma canônica.

A única ambiguidade real do PIX é 11 dígitos: pode ser CPF ou celular. A regra
do BC resolve isso pelo prefixo — celular é sempre +55DDD9XXXXXXXX. Sem o +55,
11 dígitos é CPF.
"""
import re

TIPO_CPF = 'CPF'
TIPO_CNPJ = 'CNPJ'
TIPO_CELULAR = 'CELULAR'
TIPO_EMAIL = 'EMAIL'
TIPO_ALEATORIA = 'ALEATORIA'

ROTULOS = {
    TIPO_CPF: 'CPF',
    TIPO_CNPJ: 'CNPJ',
    TIPO_CELULAR: 'Celular',
    TIPO_EMAIL: 'E-mail',
    TIPO_ALEATORIA: 'Chave aleatória',
}

_EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[a-z]{2,}$', re.IGNORECASE)
_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
_DDDS = {
    11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 24, 27, 28, 31, 32, 33, 34, 35,
    37, 38, 41, 42, 43, 44, 45, 46, 47, 48, 49, 51, 53, 54, 55, 61, 62, 63, 64,
    65, 66, 67, 68, 69, 71, 73, 74, 75, 77, 79, 81, 82, 83, 84, 85, 86, 87, 88,
    89, 91, 92, 93, 94, 95, 96, 97, 98, 99,
}


def _digitos(texto):
    return ''.join(c for c in texto if c.isdigit())


def _celular(d):
    """Celular brasileiro: DDD válido + 9 dígitos começando por 9 (ou 8).

    O PIX só aceita celular como chave de telefone — fixo não entra. E uma
    sequência de dígitos repetidos é sempre erro de digitação, nunca chave.
    """
    return (len(d) == 11 and d != d[0] * 11
            and int(d[:2]) in _DDDS and d[2] in '89')


def _cpf_valido(d):
    if len(d) != 11 or d == d[0] * 11:
        return False
    for tamanho in (9, 10):
        soma = sum(int(d[i]) * (tamanho + 1 - i) for i in range(tamanho))
        digito = (soma * 10) % 11 % 10
        if digito != int(d[tamanho]):
            return False
    return True


def _cnpj_valido(d):
    if len(d) != 14 or d == d[0] * 14:
        return False
    for pesos in ([5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2],
                  [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]):
        soma = sum(int(d[i]) * p for i, p in enumerate(pesos))
        resto = soma % 11
        digito = 0 if resto < 2 else 11 - resto
        if digito != int(d[len(pesos)]):
            return False
    return True


def identificar(valor):
    """(chave_canonica, tipo) ou ('', None) quando não é uma chave válida.

    Aceita a chave com ou sem máscara; devolve sempre a forma canônica:
    CPF/CNPJ só dígitos, celular como +55DDDNXXXXXXX, e-mail e chave aleatória
    em minúsculas.
    """
    texto = (valor or '').strip()
    if not texto:
        return '', None
    if len(texto) > 140:
        # Nenhum formato de chave chega perto disso (o e-mail, o mais longo,
        # para em 77 pelo BC). Cortar aqui evita estourar a coluna no banco.
        return '', None

    if _EMAIL.match(texto):
        return ('', None) if len(texto) > 77 else (texto.lower(), TIPO_EMAIL)

    minusculo = texto.lower()
    if _UUID.match(minusculo):
        return minusculo, TIPO_ALEATORIA

    # Daqui para baixo só sobram os formatos numéricos. Qualquer letra
    # remanescente significa que não é chave nenhuma.
    if any(c.isalpha() for c in texto):
        return '', None

    d = _digitos(texto)
    tem_mais = texto.lstrip().startswith('+')

    # Celular: +55 explícito, ou 13 dígitos começando por 55.
    if (tem_mais or len(d) == 13) and d.startswith('55'):
        nacional = d[2:]
        if _celular(nacional):
            return '+55' + nacional, TIPO_CELULAR
        return '', None

    if len(d) == 11 and _cpf_valido(d):
        return d, TIPO_CPF
    if len(d) == 14 and _cnpj_valido(d):
        return d, TIPO_CNPJ

    # 11 dígitos que não passam no CPF ainda podem ser um celular que a pessoa
    # digitou sem o +55 — só aceitamos se for um celular de verdade.
    if _celular(d):
        return '+55' + d, TIPO_CELULAR

    return '', None


def formatar(chave, tipo=None):
    """A chave com máscara, para exibição (não é o que fica no banco)."""
    if not chave:
        return ''
    if tipo is None:
        _, tipo = identificar(chave)
    if tipo == TIPO_CPF and len(chave) == 11:
        return f'{chave[:3]}.{chave[3:6]}.{chave[6:9]}-{chave[9:]}'
    if tipo == TIPO_CNPJ and len(chave) == 14:
        return f'{chave[:2]}.{chave[2:5]}.{chave[5:8]}/{chave[8:12]}-{chave[12:]}'
    if tipo == TIPO_CELULAR and chave.startswith('+55'):
        n = chave[3:]
        return f'({n[:2]}) {n[2:-4]}-{n[-4:]}'
    return chave
