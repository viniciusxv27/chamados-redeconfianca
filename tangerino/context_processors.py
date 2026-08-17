"""Expõe para os templates se o módulo está liberado para quem está olhando.

Usado pelo base.html para decidir se mostra o menu, o cartão de ponto na home
e o popup de férias. Sem isto, um usuário fora do grupo veria os itens de menu
e só descobriria que não tem acesso ao clicar.
"""
import logging

logger = logging.getLogger(__name__)


def tangerino_gate(request):
    usuario = getattr(request, 'user', None)
    if usuario is None or not usuario.is_authenticated:
        return {}
    try:
        from .models import ConfiguracaoTangerino
        config = ConfiguracaoTangerino.get()
        liberado = config.libera(usuario)
        return {
            'tangerino_liberado': liberado,
            'tangerino_widget_home': liberado and config.mostrar_widget_home,
            'tangerino_popup_ferias': liberado and config.mostrar_popup_ferias,
        }
    except Exception as exc:            # nunca derruba a renderização do portal
        logger.warning('Gate do Tangerino indisponível: %s', exc)
        return {}
