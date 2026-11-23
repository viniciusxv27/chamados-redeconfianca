"""/communications/<id>/: o SUPERADMIN vê todos os que marcaram "Estou ciente", detalhados (24/11/2026).

O painel "Status dos destinatários" existia no template, mas a view nunca mandava os dados.
Agora SUPERADMIN (e quem enviou) vê cada destinatário: ciente, com dúvida, abriu sem marcar,
não abriu — com cargo, setor e quando —, filtra, busca e exporta em CSV. Colaborador comum não vê.

Postgres de dev numa transação desfeita; cache em memória; rede bloqueada.
"""
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
import redeconfianca.settings as _cfg  # noqa: E402
_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-{n}'} for n in _cfg.CACHES}
django.setup()

from django.conf import settings  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import requests  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

setup_test_environment()

from communications.models import Communication, CommunicationRead  # noqa: E402
from users.models import Sector  # noqa: E402

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


for nome in ('get', 'post', 'request'):
    mock.patch.object(requests, nome, side_effect=AssertionError('teste tentou sair para a rede')).start()
mock.patch('communications.whatsapp.enviar_whatsapp_comunicado').start()

marcador = transaction.atomic()
marcador.__enter__()
try:
    loja = Sector.objects.create(name='ZZ Loja Status Comunicado')

    def pessoa(nome, **extra):
        dados = dict(email=f'zzst.{nome}@exemplo-teste.local', password='S3nha!teste', first_name=f'Zz{nome}',
                     last_name='Status', sector=loja, hierarchy='PADRAO', job_title=f'Cargo {nome}')
        dados.update(extra)
        return User.objects.create_user(username=f'zzst.{nome}', **dados)

    autor = pessoa('autor', hierarchy='ADMINISTRATIVO')
    chefe = pessoa('chefe', hierarchy='SUPERADMIN')
    ana, bia, caio, duda = pessoa('ana'), pessoa('bia'), pessoa('caio'), pessoa('duda')
    extra = pessoa('extra')
    com = Communication.objects.create(title='ZZ Comunicado do status', message='<p>x</p>', sender=autor)
    com.recipients.add(ana, bia, caio, duda)
    CommunicationRead.objects.create(communication=com, user=ana, status='ESTOU_CIENTE')
    CommunicationRead.objects.create(communication=com, user=bia, status='ESTOU_COM_DUVIDA')
    CommunicationRead.objects.create(communication=com, user=caio, status='NAO_VISUALIZADO')
    CommunicationRead.objects.create(communication=com, user=extra, status='ESTOU_CIENTE')  # fora da lista, mas respondeu

    url = f'/communications/{com.id}/'
    cs = Client(); cs.force_login(chefe)
    r = cs.get(url)
    html = r.content.decode()
    linhas = {x['user'].username: x for x in r.context['users_status']} if r.context else {}

    print('== SUPERADMIN ==')
    t('vê o painel "Status dos destinatários"', r.status_code == 200 and 'id="status-destinatarios"' in html, r.status_code)
    t('com todos os cientes, detalhados (cargo, setor, quando marcou)',
      linhas.get('zzst.ana', {}).get('status') == 'ESTOU_CIENTE' and 'Cargo ana · ZZ Loja Status Comunicado' in html
      and 'marcou ' in html)
    t('quem respondeu fora da lista também entra', linhas.get('zzst.extra', {}).get('status') == 'ESTOU_CIENTE')
    t('as quatro situações', linhas.get('zzst.bia', {}).get('status') == 'ESTOU_COM_DUVIDA'
      and linhas.get('zzst.caio', {}).get('status') == 'ABRIU' and linhas.get('zzst.duda', {}).get('status') == 'NAO_ABRIU', linhas)
    t('as contagens batem', r.context['status_counts'] == {'aware': 2, 'with_doubt': 1, 'opened': 1, 'not_viewed': 1, 'total': 5},
      r.context['status_counts'])
    t('abrir para conferir não põe o SUPERADMIN na lista', 'zzst.chefe' not in linhas)
    t('cientes primeiro', [x['status'] for x in r.context['users_status']][:2] == ['ESTOU_CIENTE', 'ESTOU_CIENTE'])
    r = cs.get(url + '?exportar=status')
    csv = r.content.decode('utf-8-sig')
    t('exporta CSV com todos', r['Content-Type'].startswith('text/csv') and 'Zzana Status' in csv and 'Ciente' in csv
      and 'Não abriu' in csv and csv.count('\n') == 6, csv[:300])

    print('\n== QUEM ENVIOU ==')
    ca = Client(); ca.force_login(autor)
    t('quem enviou também vê', 'id="status-destinatarios"' in ca.get(url).content.decode())

    print('\n== COLABORADOR COMUM ==')
    cc = Client(); cc.force_login(ana)
    html = cc.get(url).content.decode()
    t('não vê o painel', 'id="status-destinatarios"' not in html)
    r = cc.get(url + '?exportar=status')
    t('nem exporta', not r['Content-Type'].startswith('text/csv'))
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste ficou no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
