"""Bloco "Uniformes" das fichas do colaborador (perfil, ficha impressa e Meu perfil)."""
import logging

from django import template
from django.db import DatabaseError, transaction

from ..permissoes import pode_gerir
from ..servicos import uniformes_da_ficha

register = template.Library()
logger = logging.getLogger(__name__)


def _dados(colaborador):
    try:
        # Savepoint: banco que ainda não rodou o migrate não derruba a ficha.
        with transaction.atomic():
            return uniformes_da_ficha(colaborador)
    except DatabaseError:
        logger.warning('Uniformes da ficha indisponíveis', exc_info=True)
        return None


@register.inclusion_tag('uniformes/_ficha.html', takes_context=True)
def uniformes_na_ficha(context, colaborador, modo='tela'):
    """`modo`: 'tela' (perfil na gestão), 'proprio' (Meu perfil) ou 'impressao'."""
    request = context.get('request')
    return {
        'dados': _dados(colaborador),
        'colaborador': colaborador,
        'modo': modo,
        'pode_gerir': bool(request) and modo == 'tela' and pode_gerir(request.user),
    }
