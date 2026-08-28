"""Lembrete de reunião na página inicial.

Mostra o que começa nas próximas horas e o que já está rolando — com horário e
tema, que é o que a pessoa precisa para decidir se entra agora.
"""
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

JANELA_HORAS = 12          # o que aparece como "hoje ainda"
TOLERANCIA_MINUTOS = 90    # tempo em que a reunião segue aparecendo depois de começar


def reunioes_lembrete(request):
    user = getattr(request, 'user', None)
    vazio = {'reunioes_agora': [], 'reunioes_proximas': []}
    if not (user and user.is_authenticated):
        return vazio
    try:
        from django.db.models import Q

        from .models import Reuniao

        agora = timezone.now()
        limite = agora + timezone.timedelta(hours=JANELA_HORAS)
        inicio_min = agora - timezone.timedelta(minutes=TOLERANCIA_MINUTOS)

        qs = (Reuniao.objects
              .filter(Q(organizador=user) | Q(participantes__user=user))
              .filter(inicio__gte=inicio_min, inicio__lte=limite)
              .exclude(status__in=(Reuniao.CANCELADA, Reuniao.ENCERRADA))
              .select_related('organizador').distinct().order_by('inicio')[:5])

        agora_lista, proximas = [], []
        for r in qs:
            (agora_lista if r.inicio <= agora else proximas).append(r)
        return {'reunioes_agora': agora_lista, 'reunioes_proximas': proximas}
    except Exception as exc:                                    # noqa: BLE001
        logger.warning('Lembrete de reunião não carregou: %s', exc)
        return vazio
