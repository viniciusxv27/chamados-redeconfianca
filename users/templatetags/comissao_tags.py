"""O informativo do comissionamento, para as telas do módulo.

Fica numa tag (e não num context processor) de propósito: só as telas de
comissionamento precisam disto, e um context processor rodaria em toda página
do portal para nada. O resultado fica 60 s em cache — é um registro só, lido
por muita gente ao mesmo tempo no dia do pagamento.
"""
from django import template
from django.core.cache import caches

register = template.Library()

CHAVE = 'users:informativo_comissao'
SEGUNDOS = 60


@register.simple_tag
def informativo_comissao():
    """O informativo no ar, ou None. Nunca levanta."""
    cache = caches['local']
    guardado = cache.get(CHAVE)
    if guardado is None:
        from users.models import InformativoComissao
        informativo = InformativoComissao.vigente()
        guardado = {
            'titulo': informativo.titulo if informativo else '',
            'descricao': informativo.descricao if informativo else '',
            'destino': informativo.destino if informativo else '',
            'tipo': informativo.tipo if informativo else '',
        } if informativo and informativo.destino else {}
        cache.set(CHAVE, guardado, SEGUNDOS)
    return guardado or None


def limpar_cache():
    """Chamado ao salvar o informativo, para a troca aparecer na hora."""
    caches['local'].delete(CHAVE)
