"""Telefone do cadastro → número de WhatsApp (DDI 55 + DDD + número).

O campo ``User.phone`` é texto livre e chega de todo jeito: com máscara, com
+55, com 0 na frente do DDD, com código de operadora, sem o nono dígito, como
número da planilha importada ("27999998888.0"), dois números no mesmo campo...
Mandar os dígitos crus faz a mensagem ir para um número que não existe — e a
tela ainda dizia que o WhatsApp estava ligado.

``normalizar`` devolve o número pronto (``'5527999998888'``) ou ``''`` quando
não dá para saber qual é o número com segurança — nesse caso é melhor não
mandar do que mandar para um desconhecido (não se chuta DDD). ``problema``
explica o porquê, para a tela pedir a correção do cadastro.
"""
import re
from decimal import Decimal, InvalidOperation

# DDDs em uso no Brasil (a mesma lista do PIX, users/pix.py).
DDDS = {
    11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 24, 27, 28, 31, 32, 33, 34, 35,
    37, 38, 41, 42, 43, 44, 45, 46, 47, 48, 49, 51, 53, 54, 55, 61, 62, 63, 64,
    65, 66, 67, 68, 69, 71, 73, 74, 75, 77, 79, 81, 82, 83, 84, 85, 86, 87, 88,
    89, 91, 92, 93, 94, 95, 96, 97, 98, 99,
}

_SEPARADORES = re.compile(r'\s*(?:/|;|\||,|\bou\b|\be\b)\s*', re.IGNORECASE)
_NUMERO_DE_PLANILHA = re.compile(r'^\s*(\d+)[.,]0+\s*$')             # 27999998888.0
_NOTACAO_CIENTIFICA = re.compile(r'^\s*\d+(?:[.,]\d+)?[eE][+]?\d+\s*$')  # 2.7999998888E10


def _digitos(texto):
    return re.sub(r'\D', '', texto)


def _nacional(d):
    """Os 10 ou 11 dígitos nacionais (DDD + número) a partir de dígitos soltos, ou ''."""
    d = d.lstrip('0')                 # 0 de discagem: "027 99999-8888", "0055..."
    if len(d) in (12, 13):
        # 12/13 dígitos: ou é o DDI 55 na frente, ou o código de uma operadora
        # ("0 21 27 99999-8888"). O DDI ganha quando o que sobra tem DDD válido.
        if d.startswith('55') and int(d[2:4]) in DDDS:
            d = d[2:]
        elif int(d[2:4]) in DDDS:
            d = d[2:]
    return d if len(d) in (10, 11) else ''


def _valido(n):
    """(número com o nono dígito quando falta, '' se não serve)."""
    if len(n) not in (10, 11) or n == n[0] * len(n) or int(n[:2]) not in DDDS:
        return ''
    if len(n) == 11:
        return n if n[2] == '9' else ''                       # celular: 9 + 8 dígitos
    if n[2] in '6789':
        return n[:2] + '9' + n[2:]                           # celular antigo, sem o nono dígito
    return n if n[2] in '2345' else ''                       # fixo (WhatsApp Business existe)


def _um(texto):
    texto = str(texto or '').strip()
    if not texto:
        return ''
    casou = _NUMERO_DE_PLANILHA.match(texto)
    if casou:
        texto = casou.group(1)
    elif _NOTACAO_CIENTIFICA.match(texto):
        try:
            texto = str(int(Decimal(texto.replace(',', '.'))))
        except (InvalidOperation, ValueError):
            return ''
    d = _digitos(texto)
    if texto.startswith('+') and not d.startswith('55'):
        return d if 8 <= len(d) <= 15 else ''                # número de outro país, como veio
    nacional = _valido(_nacional(d))
    return '55' + nacional if nacional else ''


def normalizar(valor):
    """'5527999998888' (pronto para o WhatsApp) ou '' se o cadastro não tem um número confiável.

    Com mais de um número no campo ("27 99999-8888 / 27 3333-4444"), vale o
    primeiro que for válido.
    """
    texto = str(valor or '').strip()
    if not texto:
        return ''
    numero = _um(texto)
    if numero:
        return numero
    for parte in _SEPARADORES.split(texto):
        numero = _um(parte)
        if numero:
            return numero
    return ''


def problema(valor):
    """Por que o telefone do cadastro não serve — '' quando serve. Para a tela pedir a correção."""
    texto = str(valor or '').strip()
    if not texto:
        return 'sem telefone no cadastro'
    if normalizar(texto):
        return ''
    d = _digitos(texto)
    if len(d) in (8, 9):
        return 'falta o DDD'
    if len(d) < 8:
        return 'poucos dígitos'
    nacional = _nacional(d)
    if nacional and int(nacional[:2]) not in DDDS:
        return f'DDD {nacional[:2]} não existe'
    if nacional and len(nacional) == 11 and nacional[2] != '9':
        return 'celular sem o 9 na frente'
    return 'formato não reconhecido'
