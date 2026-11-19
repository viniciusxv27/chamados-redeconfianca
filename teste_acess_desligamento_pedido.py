"""Acess: decidir o pedido de desligamento (/acess/desligamentos/pedido/<id>/) no Postgres.

O bug: decidir_pedido_desligamento travava o pedido com select_for_update() + select_related('solicitante').
solicitante é null=True → LEFT OUTER JOIN, e o Postgres recusa FOR UPDATE no lado anulável de um outer
join (FeatureNotSupported → django.db.NotSupportedError). O teste antigo (teste_lote_melhorias) roda em
sqlite, que ignora select_for_update, por isso não pegava.

Transação desfeita no fim; nada sai para a rede.
"""
import os
import sys
from datetime import timedelta
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
import redeconfianca.settings as _cfg

_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-{n}'}
               for n in _cfg.CACHES}
django.setup()
from django.conf import settings
from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import requests
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import Client
from django.utils import timezone

from acess import servicos as sv
from core.models import Notification
from acess.models import AcessoAcess, Colaborador, PedidoDesligamento
from users.models import Sector

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


def post(cliente, url, dados):
    """POST num savepoint: erro de banco não estraga a transação do teste. Devolve (resposta, exceção)."""
    try:
        with transaction.atomic():
            return cliente.post(url, dados), None
    except Exception as exc:  # noqa: BLE001
        return None, exc


print('banco:', connection.vendor)
marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede), \
            mock.patch.object(requests, 'request', sem_rede):
        hoje = timezone.localdate()
        loja = Sector.objects.create(name='Loja ZZ Pedido Deslig', adabas='ZZP0001-001')
        op = User.objects.create_user(username='zz.pd.op', email='zz.pd.op@exemplo-teste.local', password='x',
                                      first_name='ZZ', last_name='Operadora', hierarchy='ADMIN')
        AcessoAcess.objects.create(user=op, permissoes=['desligamentos.gerenciar'])
        rh = User.objects.create_user(username='zz.pd.rh', email='zz.pd.rh@exemplo-teste.local', password='x',
                                      first_name='ZZ', last_name='RH')
        co = Client()
        co.force_login(op)

        def colaborador(n, cpf):
            return sv.criar_colaborador({'nome': f'ZZ Pedido {n}', 'cpf': cpf}, loja, usuario=op)

        saida, execucao = hoje + timedelta(days=3), hoje + timedelta(days=10)

        print('— Agendar')
        c1 = colaborador('um', '52998224725')
        p1 = sv.criar_pedido_desligamento(c1, PedidoDesligamento.DESLIGAMENTO, saida, execucao, 'pedido do RH', rh)
        r, exc = post(co, f'/acess/desligamentos/pedido/{p1.id}/', {'decisao': 'agendar', 'execucao': execucao.isoformat()})
        t('agendar não estoura erro (NotSupportedError)', exc is None, repr(exc))
        t('responde com redirecionamento', r is not None and r.status_code == 302, getattr(r, 'status_code', None))
        p1.refresh_from_db()
        c1.refresh_from_db()
        t('pedido fica Agendado e com quem tratou', p1.status == PedidoDesligamento.AGENDADO and p1.tratado_por == op
          and p1.tratado_em is not None)
        t('ficha recebe o agendamento', c1.desligamento_em == execucao and c1.saida_empresa == saida
          and c1.status == Colaborador.ATIVO)
        t('solicitante é avisado', Notification.objects.filter(user=rh, title__icontains='ZZ Pedido um').exists())
        r, exc = post(co, f'/acess/desligamentos/pedido/{p1.id}/', {'decisao': 'recusar', 'resposta': 'x'})
        p1.refresh_from_db()
        t('pedido já tratado não muda de novo', exc is None and p1.status == PedidoDesligamento.AGENDADO, repr(exc))

        print('— Recusar')
        c2 = colaborador('dois', '11144477735')
        p2 = sv.criar_pedido_desligamento(c2, PedidoDesligamento.DESLIGAMENTO, saida, execucao, '', rh)
        r, exc = post(co, f'/acess/desligamentos/pedido/{p2.id}/', {'decisao': 'recusar', 'resposta': ''})
        p2.refresh_from_db()
        t('recusar sem motivo é barrado (sem erro de banco)', exc is None and p2.status == PedidoDesligamento.ABERTO,
          repr(exc))
        r, exc = post(co, f'/acess/desligamentos/pedido/{p2.id}/', {'decisao': 'recusar', 'resposta': 'pessoa fica'})
        p2.refresh_from_db()
        c2.refresh_from_db()
        t('recusar com motivo encerra o pedido', exc is None and p2.status == PedidoDesligamento.RECUSADO
          and p2.resposta == 'pessoa fica' and c2.desligamento_em is None, repr(exc))

        print('— Solicitante apagado (SET_NULL)')
        c3 = colaborador('tres', '39053344705')
        p3 = sv.criar_pedido_desligamento(c3, PedidoDesligamento.DESLIGAMENTO, saida, execucao, '', rh)
        PedidoDesligamento.objects.filter(pk=p3.pk).update(solicitante=None)
        r, exc = post(co, f'/acess/desligamentos/pedido/{p3.id}/', {'decisao': 'agendar', 'execucao': ''})
        p3.refresh_from_db()
        t('pedido sem solicitante também agenda (usa a execução sugerida)',
          exc is None and p3.status == PedidoDesligamento.AGENDADO, repr(exc))

        print('— Permissão')
        c4 = colaborador('quatro', '86288366757')
        p4 = sv.criar_pedido_desligamento(c4, PedidoDesligamento.DESLIGAMENTO, saida, execucao, '', rh)
        cr = Client()
        cr.force_login(rh)
        r, exc = post(cr, f'/acess/desligamentos/pedido/{p4.id}/', {'decisao': 'agendar'})
        p4.refresh_from_db()
        t('quem não gerencia desligamentos não decide', exc is None and p4.status == PedidoDesligamento.ABERTO)
        t('GET na rota não é aceito (só POST)', co.get(f'/acess/desligamentos/pedido/{p4.id}/').status_code == 405)
        h = co.get('/acess/desligamentos/').content.decode()
        t('tela de Desligamentos abre com pedido aberto', 'id="pedidos"' in h and 'ZZ PEDIDO QUATRO' in h.upper())
finally:
    marcador.__exit__(Exception, Exception('desfaz'), None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
