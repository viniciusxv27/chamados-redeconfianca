"""Quadro de HC sem rolagem lateral e /fibras/lista com status editável e a coluna Ordem (22/10/2026).

- /quadro-hc/: a tabela de cada loja se ajusta à largura da loja (container query) e fica fora do
  embrulho global com rolagem lateral (data-rc-wrapped); colunas extras só no espaço largo;
- /fibras/lista: coluna "Ordem" (ORDEM da Esteira vinculada + protocolo) e o status muda na própria
  linha (Ilha), pelo mesmo endpoint do kanban.

Transação desfeita no fim; nada sai para a rede.
"""
import os
import sys
from datetime import date
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-qf'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-qf2'}}
django.setup()
from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import re

import requests
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from fibras import services
from fibras.models import Fibra, FibraStatusHistory

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


def sem_rede(*a, **k):
    raise AssertionError('o teste tentou sair para a rede')


marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede), \
            mock.patch.object(services, '_notify_vendor', lambda *a, **k: None):
        chefe = User.objects.create_user(username='zz.qf.chefe', email='zz.qf.chefe@exemplo-teste.local', password='x',
                                         first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN')
        ilha = User.objects.create_user(username='zz.qf.ilha', email='zz.qf.ilha@exemplo-teste.local', password='x',
                                        first_name='ZZ', last_name='Ilha', hierarchy='SUPERVISOR')
        vend = User.objects.create_user(username='zz.qf.vend', email='zz.qf.vend@exemplo-teste.local', password='x',
                                        first_name='ZZ', last_name='VENDEDOR QF', hierarchy='PADRAO')
        cc, ci, cv = Client(), Client(), Client()
        cc.force_login(chefe)
        ci.force_login(ilha)
        cv.force_login(vend)

        print('— Quadro de HC')
        h = cc.get('/quadro-hc/').content.decode()
        t('tabela fora do embrulho global com rolagem lateral', '<table class="pessoas" data-rc-wrapped="1">' in h)
        t('bloco da tabela sem overflow-x', 'class="so-tabela border-t"' in h and 'so-tabela overflow-x-auto' not in h)
        t('largura pela loja (container query) e colunas extras só no espaço largo',
          'container-type: inline-size' in h and '@container (min-width: 1240px)' in h and '@container (max-width: 760px)' in h
          and 'hidden 2xl:table-cell' not in h and h.count('class="col-extra"') >= 3)
        t('abas e carteiras quebram linha em vez de rolar', '.hc .rolagem-x { flex-wrap: wrap; }' in h
          and '.hc .abas { display: inline-flex; flex-wrap: wrap;' in h)

        print('— /fibras/lista')
        D = date(2031, 4, 2)
        f1 = Fibra.objects.create(numero_da_venda='ZZQF1', numero_protocolo='7111111111', ordem_vinculada='7111111111',
                                  pdv='ZZ LOJA', data_da_venda=D, cpf='12345678909', cliente='ZZ Cliente 1',
                                  vendedor='ZZ VENDEDOR QF', pilar='FIXA', valor=100, status=Fibra.STATUS_PENDENTE)
        f2 = Fibra.objects.create(numero_da_venda='ZZQF2', numero_protocolo="'7222A", pdv='ZZ LOJA', data_da_venda=D,
                                  cpf='12345678909', cliente='ZZ Cliente 2', vendedor='ZZ VENDEDOR QF', pilar='FIXA',
                                  valor=100, status=Fibra.STATUS_PENDENTE)
        url = '/fibras/lista/?de=2031-04-01&ate=2031-04-30'
        h = ci.get(url).content.decode()
        t('coluna Ordem no cabeçalho', 'data-ord="ordem"' in h and '>Ordem <i' in h)
        t('a ORDEM vinculada na linha', re.search(r'data-label="Ordem" class="mono cel-ordem">7111111111<', h) is not None)
        t('sem ORDEM: traço e o protocolo embaixo', 'prot. &#x27;7222A' in h or "prot. '7222A" in h)
        t('a Ilha muda o status na linha', h.count('<select class="fb-st-select"') == 2 and 'id="fbUrlStatus"' in h)
        r = ci.post(f'/fibras/{f1.pk}/status/ajax/', {'status': Fibra.STATUS_AGENDADO}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        f1.refresh_from_db()
        t('salva o status e o histórico', r.status_code == 200 and r.json()['status'] == 'agendado'
          and f1.status == Fibra.STATUS_AGENDADO and FibraStatusHistory.objects.filter(fibra=f1, alterado_por=ilha).exists())
        h = ci.get(url).content.decode()
        t('a lista recarregada mostra o novo status selecionado', re.search(r'<option value="agendado" selected>', h) is not None)
        h = cv.get(url).content.decode()
        t('o consultor vê a coluna Ordem e a pílula (sem seletor)', 'cel-ordem' in h and '<select class="fb-st-select"' not in h
          and 'ZZQF1' in h)
        r = cv.post(f'/fibras/{f2.pk}/status/ajax/', {'status': Fibra.STATUS_INSTALADO})
        f2.refresh_from_db()
        t('e o servidor recusa se ele tentar', r.status_code == 403 and f2.status == Fibra.STATUS_PENDENTE)
finally:
    marcador.__exit__(Exception, Exception('desfaz'), None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
