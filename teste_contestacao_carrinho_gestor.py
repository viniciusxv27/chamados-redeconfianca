"""Contestação: carrinho do gestor em /contestacao/gerenciar/ (26/11/2026).

Pedido: "Deve criar meio que um carrinho pro gestor pra ele poder dar de acordo ou negar
várias de vez e ficar salvo".

Cobre: pôr várias pendentes no carrinho (de acordo / aprovar e contestar / negar), o carrinho
ficar salvo no servidor por gestor, trocar decisão e parecer, tirar, aplicar tudo (ou só
algumas) fazendo o mesmo que os botões de cada contestação, a contestação decidida por outra
pessoa no meio do caminho, o escopo por loja e quem não gerencia.

Transação desfeita no fim; nada sai para a rede.
"""
import json
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cg'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cg2'}}
django.setup()
from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import requests
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from contestacao.models import (Contestation, ContestationHistory, DecisaoNoCarrinho, ExclusionRecord,
                                ExclusionSyncBatch, TipoBase)
from users.models import Sector

User = get_user_model()
ok = fail = 0
BASE = '/contestacao/gerenciar/carrinho/'


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


def post(cliente, caminho, corpo):
    return cliente.post(BASE + caminho, data=json.dumps(corpo), content_type='application/json')


marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede):
        loja_a = Sector.objects.create(name='ZZ LOJA CG A')
        Sector.objects.create(name='ZZ LOJA CG B')
        chefe = User.objects.create_user(username='zzcg.chefe', email='zzcg.chefe@exemplo-teste.local', password='x',
                                         first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True)
        gestor = User.objects.create_user(username='zzcg.gestor', email='zzcg.gestor@exemplo-teste.local', password='x',
                                          first_name='ZZ', last_name='Gestor', hierarchy='ADMIN', sector=loja_a)
        comum = User.objects.create_user(username='zzcg.comum', email='zzcg.comum@exemplo-teste.local', password='x',
                                         first_name='ZZ', hierarchy='PADRAO', sector=loja_a)
        lote = ExclusionSyncBatch.objects.create(record_type=TipoBase.EXCLUSAO, record_count=6, created_by=chefe)
        ContestationHistory.objects.create(action='synced', user=chefe, notes='ZZ sync do teste do carrinho do gestor')

        def contestacao(n, filial='ZZ LOJA CG A', status='pending'):
            e = ExclusionRecord.objects.create(sync_batch=lote, filial=filial, vendedor=f'ZZ Vendedor {n}', receita=100 + n,
                                               pilar='ZZ', numero_venda=f'ZZCG{n}', data_venda='01/11/2026',
                                               nome_cliente=f'ZZ Cliente {n}', record_type=TipoBase.EXCLUSAO)
            return Contestation.objects.create(exclusion=e, requester=comum, reason=f'ZZ motivo {n}', status=status)

        c1, c2, c3, c6 = contestacao(1), contestacao(2), contestacao(3), contestacao(6)
        c4 = contestacao(4, filial='ZZ LOJA CG B')
        c5 = contestacao(5, status='confirmed')

        cc = Client()
        cc.force_login(chefe)
        cg = Client()
        cg.force_login(gestor)
        cx = Client()
        cx.force_login(comum)

        print('— Tela')
        r = cc.get('/contestacao/gerenciar/?status=pending&setor=ZZ LOJA CG A')
        h = r.content.decode()
        t('gerenciar abre com o carrinho', r.status_code == 200 and 'id="cgInicial"' in h and 'id="cgFlutuante"' in h)
        t('pendentes têm marcador e "pôr no carrinho"', f'class="cg-marca' in h and f'data-cg-por="{c1.pk}"' in h
          and 'data-decisao="rejected"' in h and 'id="cgBarraLote"' in h)
        t('botões antigos (aprovar/rejeitar uma) continuam', f'/contestacao/{c1.pk}/aprovar/' in h and f'/contestacao/{c1.pk}/rejeitar/' in h)

        print('— Pôr no carrinho')
        d = post(cc, 'salvar/', {'ids': [c1.pk, c2.pk, c5.pk], 'decisao': 'approved'}).json()
        t('várias de uma vez como "de acordo"; a já decidida fica de fora', d['ok'] and {i['id'] for i in d['itens']} == {c1.pk, c2.pk}
          and d['ignoradas'] == [c5.pk], d)
        d = post(cc, 'salvar/', {'ids': [c3.pk], 'decisao': 'rejected', 'observacao': 'Venda não confere com a Vivo'}).json()
        t('negar com parecer', d['conta'] == {'approved': 2, 'approved_and_contested': 0, 'rejected': 1}
          and next(i for i in d['itens'] if i['id'] == c3.pk)['observacao'] == 'Venda não confere com a Vivo', d)
        t('decisão inválida recusada', post(cc, 'salvar/', {'ids': [c1.pk], 'decisao': 'pago'}).status_code == 400)
        nova_sessao = Client()
        nova_sessao.force_login(chefe)
        d = nova_sessao.get(BASE).json()
        t('fica salvo (outra sessão vê o mesmo carrinho)', d['total'] == 3)
        h = nova_sessao.get('/contestacao/gerenciar/?status=pending&setor=ZZ LOJA CG A').content.decode()
        inicial = json.loads(h.split('id="cgInicial" type="application/json">')[1].split('</script>')[0])
        t('a tela já abre com o carrinho salvo', {i['id'] for i in inicial} == {c1.pk, c2.pk, c3.pk})
        d = post(cc, 'salvar/', {'ids': [c2.pk], 'decisao': 'approved_and_contested'}).json()
        t('trocar a decisão de um item', next(i for i in d['itens'] if i['id'] == c2.pk)['decisao'] == 'approved_and_contested')
        d = post(cc, 'observacao/', {'id': c1.pk, 'observacao': 'Conferido'}).json()
        t('mudar só o parecer', next(i for i in d['itens'] if i['id'] == c1.pk)['observacao'] == 'Conferido')
        post(cc, 'salvar/', {'ids': [c6.pk], 'decisao': 'approved'})
        d = post(cc, 'remover/', {'ids': [c6.pk]}).json()
        t('tirar do carrinho', c6.pk not in {i['id'] for i in d['itens']} and Contestation.objects.get(pk=c6.pk).status == 'pending')
        t('quem não gerencia não usa o carrinho', post(cx, 'salvar/', {'ids': [c1.pk], 'decisao': 'approved'}).status_code == 403
          and cx.get(BASE).status_code == 403)

        print('— Escopo e carrinho por gestor')
        d = post(cg, 'salvar/', {'ids': [c4.pk, c6.pk], 'decisao': 'rejected'}).json()
        t('gestor só põe contestação da loja dele', {i['id'] for i in d['itens']} == {c6.pk} and d['ignoradas'] == [c4.pk], d)
        t('cada gestor tem o seu carrinho', cc.get(BASE).json()['total'] == 3 and cg.get(BASE).json()['total'] == 1)
        DecisaoNoCarrinho.objects.create(gestor=gestor, contestation=c4, decisao='approved')
        d = post(cg, 'aplicar/', {'ids': [c4.pk]}).json()
        c4.refresh_from_db()
        t('aplicar não passa por cima do escopo', c4.status == 'pending' and d['falhas'][0]['id'] == c4.pk
          and 'escopo' in d['falhas'][0]['motivo'], d)

        print('— Aplicar')
        Contestation.objects.filter(pk=c2.pk).update(status='rejected', reviewed_by=gestor)
        d = post(cc, 'aplicar/', {'observacao_padrao': 'Lote do dia', 'observacoes': {str(c3.pk): 'Venda cancelada pelo cliente'}}).json()
        for c in (c1, c2, c3):
            c.refresh_from_db()
        c3.exclusion.refresh_from_db()
        t('aplicadas as que seguiam pendentes', {a['id'] for a in d['aplicadas']} == {c1.pk, c3.pk}, d)
        t('de acordo = aprovada e para pagamento (como o botão Aprovar)', (c1.status, c1.payment_status, c1.approval_mode,
          c1.reviewed_by_id, c1.review_notes) == ('confirmed', 'pending_payment', 'approved', chefe.pk, 'Conferido'))
        t('negar = rejeitada, aguardando o gerente, com o parecer na venda', c3.status == 'rejected' and c3.confirmed_by_id is None
          and c3.review_notes == 'Venda cancelada pelo cliente'
          and c3.exclusion.observacao == 'Parecer da rejeição: Venda cancelada pelo cliente')
        t('decidida no meio do caminho por outra pessoa não é tocada e sai do carrinho', c2.status == 'rejected'
          and any(f['id'] == c2.pk and 'Já estava' in f['motivo'] for f in d['falhas']))
        t('histórico de cada decisão, marcado como pelo carrinho', ContestationHistory.objects.filter(
          contestation=c1, action='approved', user=chefe, extra_data__pelo_carrinho=True).exists()
          and ContestationHistory.objects.filter(contestation=c3, action='rejected', extra_data__pelo_carrinho=True).exists())
        t('carrinho vazio depois de aplicar', d['total'] == 0 and not DecisaoNoCarrinho.objects.filter(gestor=chefe).exists())

        c7, c8 = contestacao(7), contestacao(8)
        post(cc, 'salvar/', {'ids': [c7.pk, c8.pk], 'decisao': 'approved_and_contested'})
        d = post(cc, 'aplicar/', {'ids': [c7.pk], 'observacao_padrao': 'Lote parcial'}).json()
        c7.refresh_from_db()
        c8.refresh_from_db()
        t('aplicar só as escolhidas; o resto continua no carrinho', c7.status == 'confirmed' and c7.approval_mode ==
          'approved_and_contested' and c7.review_notes == 'Lote parcial' and c8.status == 'pending'
          and [i['id'] for i in d['itens']] == [c8.pk])
        d = post(cc, 'remover/', {'todos': True}).json()
        t('esvaziar o carrinho não decide nada', d['total'] == 0 and Contestation.objects.get(pk=c8.pk).status == 'pending')
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
