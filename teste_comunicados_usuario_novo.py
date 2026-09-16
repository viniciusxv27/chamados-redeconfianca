"""Comunicados obrigatórios: usuário novo só fica travado pelo que veio depois dele.

Pedido: com usuários novos, só devem ser obrigatórios os comunicados a partir da
data em que o usuário foi criado. Antes, quem entrava hoje caía no popup de "de
acordo" com todos os comunicados obrigatórios desde 27/07/2026.

NADA SAI DAQUI: comunicado é gravado com ``skip_webhooks`` (sem WhatsApp nem
webhook), o aviso do post_save é dublê, nenhum comunicado é "para todos" e tudo
roda numa transação desfeita no fim.
"""
import os
import sys
from datetime import date, datetime, time, timedelta
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from communications.models import Communication
from communications.popup_checkers import REGRA_ATIVA_DESDE, comunicados_pendentes, inicio_da_obrigatoriedade

User = get_user_model()
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


HOJE = timezone.localdate()


def momento(dias_atras, hora):
    return timezone.make_aware(datetime.combine(HOJE - timedelta(days=dias_atras), time(hora, 0)))


marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch('notifications.services.notification_service.notify_communication_created') as aviso:
        def pessoa(apelido, entrou_em):
            u = User.objects.create_user(username=f'zzcom.{apelido}', email=f'zzcom.{apelido}@exemplo-teste.local',
                                         password='S3nha!teste', first_name='ZZCom', last_name=apelido.title())
            User.objects.filter(pk=u.pk).update(date_joined=entrou_em)
            u.refresh_from_db()
            return u

        autor = pessoa('autor', momento(60, 9))
        veterano = pessoa('veterano', momento(30, 9))      # já estava no portal
        novato = pessoa('novato', momento(3, 15))          # criado há 3 dias, às 15h
        antigao = pessoa('antigao', timezone.make_aware(datetime(2025, 1, 10, 9, 0)))

        def comunicado(titulo, criado_em):
            c = Communication(title=titulo, message='Comunicado de teste.', sender=autor, obrigatorio=True)
            c.save(skip_webhooks=True)                     # sem WhatsApp e sem webhook
            Communication.objects.filter(pk=c.pk).update(created_at=criado_em)
            c.recipients.add(veterano, novato, antigao)
            return c

        antes = comunicado('ZZ antes de o novato existir', momento(10, 12))
        mesmo_dia = comunicado('ZZ no dia em que o novato foi criado, mais cedo', momento(3, 9))
        depois = comunicado('ZZ depois de o novato existir', momento(1, 12))
        do_teste = [antes.pk, mesmo_dia.pk, depois.pk]

        def pendentes(user):
            return set(comunicados_pendentes(user).filter(pk__in=do_teste).values_list('pk', flat=True))

        print('== A DATA QUE VALE PARA CADA PESSOA ==')
        t('usuário novo: conta a partir do dia em que foi criado', inicio_da_obrigatoriedade(novato) == HOJE - timedelta(days=3),
          inicio_da_obrigatoriedade(novato))
        t('quem já estava antes da regra continua a partir de 27/07/2026',
          inicio_da_obrigatoriedade(antigao) == REGRA_ATIVA_DESDE == date(2026, 7, 27))

        print('\n== QUEM FICA TRAVADO POR QUAL COMUNICADO ==')
        t('o novato não fica travado pelo comunicado de antes de ele existir', antes.pk not in pendentes(novato))
        t('mas o do próprio dia em que foi criado vale (a regra é pela data)', mesmo_dia.pk in pendentes(novato))
        t('e o de depois também', depois.pk in pendentes(novato))
        t('o veterano continua com os três', pendentes(veterano) == set(do_teste), pendentes(veterano))
        t('e quem é de antes da regra também', pendentes(antigao) == set(do_teste), pendentes(antigao))

        print('\n== COM OS COMUNICADOS DE VERDADE DO PORTAL ==')
        recem_criado = pessoa('recemcriado', timezone.now())
        antigos = [c for c in comunicados_pendentes(recem_criado)
                   if timezone.localtime(c.created_at).date() < HOJE]
        t('quem foi criado agora não cai em nenhum comunicado obrigatório de dias anteriores', not antigos,
          [c.title for c in antigos][:3])

        print('\n== NADA SAIU DAQUI ==')
        t('o aviso do comunicado foi sempre dublê (nenhum push/WhatsApp)', isinstance(aviso, mock.MagicMock))
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
