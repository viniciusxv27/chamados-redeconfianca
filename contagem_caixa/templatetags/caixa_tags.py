"""Formatação de dinheiro da planilha de caixa.

A tela imita a planilha do financeiro, então os valores precisam sair no padrão
brasileiro ('R$ 6.213,00'). O locale do projeto não liga o separador de milhar,
e depender dele mudaria número em todo o portal — por isso a formatação fica
aqui, restrita ao módulo.
"""
from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()
ZERO = Decimal('0.00')


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
    """1234.5 -> '1.234,50'."""
    texto = f'{valor:,.{casas}f}'
    return texto.replace(',', '_').replace('.', ',').replace('_', '.')


@register.filter
def brl(valor):
    """Dinheiro para leitura: 'R$ 6.213,00'. Zero e vazio viram travessão."""
    numero = _decimal(valor)
    if numero is None or numero == ZERO:
        return '—'
    return f'R$ {_br(numero)}'


@register.filter
def brl_zero(valor):
    """Igual ao brl, mas mostra 'R$ 0,00' em vez de travessão.

    Usado onde o zero é informação (saldo do dia, totais do período) e não
    ausência de lançamento.
    """
    numero = _decimal(valor)
    return f'R$ {_br(numero if numero is not None else ZERO)}'


@register.filter
def campo(valor):
    """Valor do jeito que vai dentro do input: '1000,50', vazio quando zerado."""
    numero = _decimal(valor)
    if numero is None or numero == ZERO:
        return ''
    return f'{numero:.2f}'.replace('.', ',')
