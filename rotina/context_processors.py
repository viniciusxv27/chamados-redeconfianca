"""Diz ao menu e ao base.html se a Rotina Gerencial vale para quem está olhando.

- `rotina_liberada`: a pessoa tem rotina ativa com pelo menos uma atividade.
  Decide o item de menu, a inclusão do notificador de avisos e o cartão da
  home — sem atividade não há o que avisar nem mostrar.
- `rotina_admin`: SUPERADMIN, que enxerga o menu mesmo sem rotina própria,
  para gerir a dos outros.

Roda em toda página: no máximo uma consulta (`exists`), guardada no request, e
nunca derruba a renderização do portal.
"""
import logging

from .permissoes import e_superadmin

logger = logging.getLogger(__name__)


def rotina_liberada(user):
    """A regra de quem tem a rotina "ligada": ativa e com pelo menos uma atividade."""
    from .models import RotinaGerencial

    return RotinaGerencial.objects.filter(user=user, ativa=True, atividades__isnull=False).exists()


def rotina_menu(request):
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {'rotina_liberada': False, 'rotina_admin': False}

    ja_calculado = getattr(request, '_rotina_menu', None)
    if ja_calculado is not None:
        return ja_calculado

    try:
        liberada = rotina_liberada(user)
    except Exception as exc:                                    # noqa: BLE001
        logger.warning('Menu da rotina gerencial indisponível: %s', exc)
        liberada = False

    resultado = {'rotina_liberada': liberada, 'rotina_admin': e_superadmin(user)}
    try:
        request._rotina_menu = resultado
    except Exception:                                           # noqa: BLE001
        pass
    return resultado
