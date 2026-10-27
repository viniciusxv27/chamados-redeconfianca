"""/ponto/escala/ (27/10/2026): Feriado além da folga, "Replicar semana passada" e o layout novo.

- o dia da escala é Trabalho, Folga ou Feriado (tipo_<id>_<data>); feriado não conta horas nem
  vira cobrança de batida (pendências usam o previsto da escala);
- "Replicar semana passada" copia a semana anterior (tipos, horários) para a semana escolhida,
  só para quem o gestor escala e no setor filtrado; quem não tinha escala fica como está;
- a tabela sai do embrulho global com rolagem e mostra um resumo por célula.

Transação desfeita no fim; nada sai para a rede. (Roda também em SQLite descartável.)
"""
import os
import sys
from datetime import date, time, timedelta
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ef'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ef2'}}
django.setup()
from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import requests
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from communications.models import CommunicationGroup
from tangerino import pendencias
from tangerino.models import Escala, EscalaDia
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


marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede), \
            mock.patch.object(requests, 'request', sem_rede):
        loja = Sector.objects.create(name='ZZ Loja Escala')
        outra = Sector.objects.create(name='ZZ Outra Escala')
        gerentes = CommunicationGroup.objects.filter(name__iexact='GERENTES').first()
        gerente = User.objects.create_user(username='zz.ef.ger', email='zz.ef.ger@exemplo-teste.local', password='x',
                                           first_name='ZZ', last_name='Gerente', hierarchy='PADRAO', sector=loja)
        if gerentes is None:
            gerentes = CommunicationGroup.objects.create(name='GERENTES', created_by=gerente)
        gerentes.members.add(gerente)
        ana = User.objects.create_user(username='zz.ef.ana', email='zz.ef.ana@exemplo-teste.local', password='x',
                                       first_name='ZZ', last_name='Ana', hierarchy='PADRAO', sector=loja)
        bia = User.objects.create_user(username='zz.ef.bia', email='zz.ef.bia@exemplo-teste.local', password='x',
                                       first_name='ZZ', last_name='Bia', hierarchy='PADRAO', sector=loja)
        fora = User.objects.create_user(username='zz.ef.fora', email='zz.ef.fora@exemplo-teste.local', password='x',
                                        first_name='ZZ', last_name='Fora', hierarchy='PADRAO', sector=outra)
        cg = Client()
        cg.force_login(gerente)
        seg = date(2031, 3, 3)                          # segunda
        anterior = seg - timedelta(days=7)

        print('— Feriado ao salvar')
        dados = {'inicio': seg.isoformat(), 'colaborador': [str(ana.id)]}
        for i in range(5):
            iso = (seg + timedelta(days=i)).isoformat()
            dados.update({f'tipo_{ana.id}_{iso}': 'TRABALHO', f'entrada_{ana.id}_{iso}': '08:00',
                          f'saida_almoco_{ana.id}_{iso}': '12:00', f'volta_almoco_{ana.id}_{iso}': '13:00',
                          f'saida_{ana.id}_{iso}': '17:00'})
        dados[f'tipo_{ana.id}_{(seg + timedelta(days=2)).isoformat()}'] = 'FERIADO'   # quarta: feriado (com horas no POST)
        dados[f'tipo_{ana.id}_{(seg + timedelta(days=5)).isoformat()}'] = 'FOLGA'
        cg.post('/ponto/escala/salvar/', dados)
        dias = {d.data: d for d in EscalaDia.objects.filter(escala__colaborador=ana, escala__semana_inicio=seg)}
        qua = dias.get(seg + timedelta(days=2))
        t('feriado gravado, sem horários', qua and qua.feriado and not qua.folga and qua.entrada is None and qua.minutos == 0)
        t('folga continua folga', dias[seg + timedelta(days=5)].folga and not dias[seg + timedelta(days=5)].feriado)
        t('dias de trabalho com 8h', dias[seg].minutos == 480 and dias[seg].tipo == 'TRABALHO')
        prev = pendencias._escalas_do_portal([ana], seg, seg + timedelta(days=6))
        t('feriado não vira previsto (nem cobrança de batida)', prev[(ana.id, seg + timedelta(days=2))] == 0
          and prev[(ana.id, seg)] == 8 * 3600)
        dados2 = {'inicio': seg.isoformat(), 'colaborador': [str(bia.id)], f'folga_{bia.id}_{seg.isoformat()}': 'on'}
        cg.post('/ponto/escala/salvar/', dados2)
        t('o checkbox antigo de folga ainda vale', EscalaDia.objects.get(escala__colaborador=bia, data=seg).folga)

        print('— Tela')
        r = cg.get(f'/ponto/escala/?inicio={seg.isoformat()}')
        h = r.content.decode()
        t('tabela fora do embrulho global e com resumo por célula', 'class="esc-grid text-sm" data-rc-wrapped="1"' in h
          and 'class="esc-resumo"' in h and f'name="tipo_{ana.id}_{(seg + timedelta(days=2)).isoformat()}" value="FERIADO"' in h)
        t('editor do dia com Trabalho / Folga / Feriado', 'id="escEditor"' in h and 'value="FERIADO"' in h and 'feriado p/ todos' in h)
        t('botão Replicar semana passada', 'id="escReplicar"' in h and 'Replicar semana passada' in h)
        h = Client()
        h.force_login(ana)
        hh = h.get(f'/ponto/escala/?inicio={seg.isoformat()}').content.decode()
        t('o colaborador vê o feriado na própria escala', 'Feriado' in hh)

        print('— Replicar semana passada')
        prox = seg + timedelta(days=7)
        Escala.objects.filter(colaborador=bia, semana_inicio=prox).delete()
        eb = Escala.objects.create(colaborador=bia, semana_inicio=prox)
        EscalaDia.objects.create(escala=eb, data=prox + timedelta(days=6), entrada=time(9, 0), saida=time(10, 0))
        ef = Escala.objects.create(colaborador=fora, semana_inicio=seg)
        EscalaDia.objects.create(escala=ef, data=seg, folga=True)
        r = cg.post('/ponto/escala/replicar/', {'inicio': prox.isoformat()})
        t('volta para a semana escolhida', r.status_code == 302 and f'inicio={prox.isoformat()}' in r['Location'])
        novos = {d.data: d for d in EscalaDia.objects.filter(escala__colaborador=ana, escala__semana_inicio=prox)}
        t('copia dia a dia, com tipos e horários', len(novos) == 6 and novos[prox].entrada == time(8, 0)
          and novos[prox + timedelta(days=2)].feriado and novos[prox + timedelta(days=5)].folga)
        bdias = list(EscalaDia.objects.filter(escala__colaborador=bia, escala__semana_inicio=prox))
        t('substitui o que já existia na semana escolhida', len(bdias) == 1 and bdias[0].data == prox and bdias[0].folga)
        t('não mexe em quem o gerente não escala', not Escala.objects.filter(colaborador=fora, semana_inicio=prox).exists())
        t('a semana anterior fica intacta', EscalaDia.objects.filter(escala__colaborador=ana, escala__semana_inicio=seg).count() == 6)
        vazia = prox + timedelta(days=21)
        r = cg.post('/ponto/escala/replicar/', {'inicio': vazia.isoformat()}, follow=True)
        t('sem escala na semana anterior: avisa e não cria nada', 'nada foi copiado' in r.content.decode()
          and not Escala.objects.filter(semana_inicio=vazia).exists())
        cv = Client()
        cv.force_login(ana)
        cv.post('/ponto/escala/replicar/', {'inicio': (prox + timedelta(days=7)).isoformat()})
        t('quem não monta escala não replica', not Escala.objects.filter(semana_inicio=prox + timedelta(days=7)).exists())
finally:
    marcador.__exit__(Exception, Exception('desfaz'), None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
