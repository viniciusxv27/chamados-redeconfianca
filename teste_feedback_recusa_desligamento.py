"""Entrevista de desligamento: lojas de SP e a recusa do colaborador.

Pedido: "coloca as lojas de SP (CARAPICUIBA, ITAIM PAULISTA, EMBU DAS ARTES) ·
cria uma função 'O colaborador não quis realizar a entrevista de desligamento'
+ um campo de observações para esse caso".

Caches em memória, transação desfeita no fim: não grava nada no banco.
"""
import os
import sys
from datetime import date

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fb-recusa'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fb-recusa-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from feedback.models import ExitInterviewParticipation, ExitInterviewResponse
from feedback.views import EXIT_INTERVIEW_KEY, EXIT_STORE_OPTIONS

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


marcador = transaction.atomic()
marcador.__enter__()
try:
    rh = User.objects.create_user(
        username='zz.fb.rh', email='zz.fb.rh@exemplo-teste.local', password='S3nha!teste',
        first_name='Zz', last_name='RH', hierarchy='SUPERADMIN', is_staff=True, is_superuser=True)
    saindo = User.objects.create_user(
        username='zz.fb.saindo', email='zz.fb.saindo@exemplo-teste.local', password='S3nha!teste',
        first_name='Zz', last_name='Saindo')
    c = Client()
    c.force_login(rh)
    url = '/feedback/entrevista-desligamento/'

    print('== LOJAS DE SP ==')
    for loja in ('CARAPICUÍBA', 'ITAIM PAULISTA', 'EMBU DAS ARTES'):
        t(f'{loja} está na lista', loja in EXIT_STORE_OPTIONS)
    html = c.get(url).content.decode()
    t('e aparece no formulário', 'value="EMBU DAS ARTES"' in html and 'value="CARAPICUÍBA"' in html)
    t('o formulário tem a opção de recusa', 'name="recusou"' in html and 'name="recusa_observacao"' in html)

    print('\n== RECUSA ==')
    antes = ExitInterviewResponse.objects.count()
    r = c.post(url, {'subject_id': saindo.id, 'dismissal_date': '2026-10-10', 'recusou': 'on',
                     'recusa_observacao': ''})
    t('sem observação: não registra',
      not ExitInterviewParticipation.objects.filter(user=saindo).exists())
    r = c.post(url, {'subject_id': saindo.id, 'dismissal_date': '2026-10-10', 'recusou': 'on',
                     'recusa_observacao': 'Convidado por telefone, preferiu não participar.'})
    t('com observação: volta para a tela', r.status_code == 302, r.status_code)
    p = ExitInterviewParticipation.objects.get(survey_key=EXIT_INTERVIEW_KEY, user=saindo)
    t('status Recusou', p.status == 'REFUSED' and p.get_status_display() == 'Recusou a entrevista', p.status)
    t('guarda a observação, o entrevistador e a data',
      p.refusal_notes.startswith('Convidado') and p.interviewer_id == rh.id and p.dismissal_date == date(2026, 10, 10))
    t('não cria resposta (fica fora das médias)', ExitInterviewResponse.objects.count() == antes)

    html = c.get(url).content.decode()
    t('entra na lista de desligamento de acesso', f'/feedback/entrevista-desligamento/{saindo.id}/desligar/' in html)
    t('com o selo Recusou', 'Recusou</span>' in html)

    print('\n== RELATÓRIO ==')
    r = c.get(url + 'relatorio/?status=refused')
    t('filtro Recusou traz o colaborador', [row['user'].id for row in r.context['detailed']] == [saindo.id],
      [row['user'].id for row in r.context['detailed']])
    t('cartão conta a recusa', r.context['totals']['refused'] >= 1)
    t('mostra a observação', 'Convidado por telefone' in r.content.decode())

    print('\n== ENTREVISTA NORMAL CONTINUA IGUAL ==')
    r = c.post(url, {'subject_id': saindo.id, 'dismissal_date': '2026-10-10', 'cargo': 'x'})
    t('sem as respostas obrigatórias, recusa o envio', ExitInterviewResponse.objects.count() == antes)
    p.refresh_from_db()
    t('e a recusa registrada continua lá', p.status == 'REFUSED')
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
