"""Formatação de dinheiro do Contas a Pagar (R$ 1.234,56 e o compacto "R$ 12,3 mil")."""
from decimal import ROUND_HALF_UP, Decimal

from django import template

register = template.Library()


def _br(v, casas=2, minimo=None):
    q = Decimal(1).scaleb(-casas)
    v = Decimal(str(v or 0)).quantize(q, rounding=ROUND_HALF_UP)
    inteiro, _, frac = f'{abs(v):,.{casas}f}'.partition('.')
    inteiro = inteiro.replace(',', '.')
    if minimo is not None:  # até `casas` casas, tirando zeros à direita (como o maximumFractionDigits)
        frac = frac.rstrip('0')
    return ('-' if v < 0 else '') + inteiro + (',' + frac if frac else '')


@register.filter
def brl(v):
    v = Decimal(str(v or 0))
    s = _br(v)
    return ('-R$ ' + s[1:]) if s.startswith('-') else 'R$ ' + s


@register.filter
def brl_curto(v):
    v = Decimal(str(v or 0))
    a = abs(v)
    sinal = '-' if v < 0 else ''
    if a >= Decimal('1e6'):
        return f'{sinal}R$ {_br(a / Decimal("1e6"), 2, minimo=0)} mi'
    if a >= Decimal('1e4'):
        return f'{sinal}R$ {_br(a / Decimal("1e3"), 1, minimo=0)} mil'
    return brl(v)


@register.filter
def moeda_campo(v):
    """1.234,56 para os campos de saldo ('' quando zero)."""
    return _br(v) if v else ''
