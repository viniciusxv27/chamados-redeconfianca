"""Se o menu mostra o Comissionamento.

Deixar o item no menu para quem não pode abrir só gera clique e mensagem de
erro. A regra mora numa função só (``pode_ver_comissionamento``), aqui e na
view — duas verdades sobre quem enxerga dinheiro seria pedir divergência.
"""
import logging

logger = logging.getLogger(__name__)


def comissionamento_menu(request):
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {'pode_ver_comissionamento': False}
    try:
        from .commission_views import pode_ver_comissionamento
        return {'pode_ver_comissionamento': pode_ver_comissionamento(user)}
    except Exception as exc:                                    # noqa: BLE001
        logger.warning('Menu de comissionamento indisponível: %s', exc)
        return {'pode_ver_comissionamento': False}
