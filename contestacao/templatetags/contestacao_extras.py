from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def digits_only(value):
    """Return only numeric characters from a value."""
    if value is None:
        return ''
    return ''.join(ch for ch in str(value) if ch.isdigit())


@register.filter
def moeda(value):
    """Valor em reais no formato brasileiro: R$ 1.250,00.

    O portal não liga USE_THOUSAND_SEPARATOR (mudaria todo número de todas as
    telas), então a separação de milhar é feita aqui, onde só afeta dinheiro.

    ``None`` devolve travessão, não "R$ 0,00": não ter valor informado é
    diferente de o valor ser zero.
    """
    if value is None or value == '':
        return '—'
    try:
        numero = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return '—'

    sinal = '-' if numero < 0 else ''
    inteiro, _, centavos = f'{abs(numero):.2f}'.partition('.')

    grupos = []
    while len(inteiro) > 3:
        grupos.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    grupos.insert(0, inteiro)

    return f'{sinal}R$ {".".join(grupos)},{centavos}'
