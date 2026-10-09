"""Formatação de dinheiro do módulo de Vendas, no padrão brasileiro."""
from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def _decimal(valor):
    if valor is None or valor == '':
        return None
    if isinstance(valor, Decimal):
        return valor
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _br(valor, casas=2):
    texto = f'{valor:,.{casas}f}'
    return texto.replace(',', '_').replace('.', ',').replace('_', '.')


@register.filter
def brl(valor):
    """'R$ 1.234,56'. Vazio e zero viram 'R$ 0,00' — em venda, zero é número."""
    numero = _decimal(valor)
    return f'R$ {_br(numero if numero is not None else Decimal("0"))}'


@register.filter
def brl_curto(valor):
    """Versão compacta para legenda: 'R$ 12,3 mil'."""
    numero = _decimal(valor) or Decimal('0')
    if abs(numero) >= 1000:
        return f'R$ {_br(numero / 1000, 1)} mil'
    return f'R$ {_br(numero)}'


@register.filter
def last_dia(serie):
    """Data do último ponto da série — o template não indexa lista pelo fim."""
    if not serie:
        return ''
    return serie[-1]['dia'].strftime('%d/%m')


@register.filter
def get_item(dicionario, chave):
    """Item de dicionário pela chave (o template não indexa dict por variável)."""
    try:
        return dicionario.get(chave)
    except AttributeError:
        return None


@register.filter
def brl_ou_traco(valor):
    """Como brl, mas vazio vira "—" (célula sem preço na tabela não é R$ 0,00)."""
    numero = _decimal(valor)
    return '—' if numero is None else f'R$ {_br(numero)}'
