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
        liberado = config.libera_ponto(usuario)
        ferias = config.libera_ferias(usuario)
        return {
            'tangerino_liberado': liberado,                 # ponto
            'tangerino_ferias_liberado': ferias,
            'tangerino_widget_home': liberado and config.mostrar_widget_home,
            'tangerino_popup_ferias': ferias and config.mostrar_popup_ferias,
            # Aba "Ajustes de ponto": o setor que o SUPERADMIN escolheu (só com a função ligada).
            'tangerino_ajustes_responsavel': bool(config.ajustes_ativo and config.ajustes_setor_id and (
                usuario.is_superuser or getattr(usuario, 'hierarchy', '') == 'SUPERADMIN'
                or (usuario.sector_id and usuario.sector_id == config.ajustes_setor_id))),
        }
    except Exception as exc:            # nunca derruba a renderização do portal
        logger.warning('Gate do Tangerino indisponível: %s', exc)
        return {}
