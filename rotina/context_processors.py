"""Diz ao menu e ao base.html se a Rotina Gerencial vale para quem está olhando.

- `rotina_no_menu`: a pessoa tem rotina ativa (mesmo vazia). Adicionada na
  gestão, o módulo já aparece no menu dela — antes da primeira atividade a tela
  diz que a semana ainda está vazia.
- `rotina_liberada`: rotina ativa com pelo menos uma atividade. Decide a
  inclusão do notificador de avisos e o cartão da home — sem atividade não há
  o que avisar nem mostrar.
- `rotina_admin`: SUPERADMIN, que enxerga o menu mesmo sem rotina própria,
  para gerir a dos outros.

Roda em toda página: no máximo uma consulta, guardada no request, e
nunca derruba a renderização do portal.
"""
import logging

from .permissoes import e_superadmin

logger = logging.getLogger(__name__)


def rotina_liberada(user):
    """A regra de quem tem a rotina "ligada": ativa e com pelo menos uma atividade."""
    from .models import RotinaGerencial

    return RotinaGerencial.objects.filter(user=user, ativa=True, atividades__isnull=False).exists()


def _situacao(user):
    """(tem rotina ativa, tem atividade) numa consulta só."""
    from django.db.models import Exists, OuterRef

    from .models import AtividadeRotina, RotinaGerencial

    linha = (RotinaGerencial.objects.filter(user=user, ativa=True)
             .annotate(com_atividade=Exists(AtividadeRotina.objects.filter(rotina=OuterRef('pk'))))
             .values_list('com_atividade', flat=True).first())
    return linha is not None, bool(linha)


def rotina_menu(request):
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {'rotina_liberada': False, 'rotina_no_menu': False, 'rotina_admin': False}

    ja_calculado = getattr(request, '_rotina_menu', None)
    if ja_calculado is not None:
        return ja_calculado

    try:
        no_menu, liberada = _situacao(user)
    except Exception as exc:                                    # noqa: BLE001
        logger.warning('Menu da rotina gerencial indisponível: %s', exc)
        no_menu = liberada = False

    resultado = {'rotina_liberada': liberada, 'rotina_no_menu': no_menu, 'rotina_admin': e_superadmin(user)}
    try:
        request._rotina_menu = resultado
    except Exception:                                           # noqa: BLE001
        pass
    return resultado
