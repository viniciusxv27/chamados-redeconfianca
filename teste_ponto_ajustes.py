"""Ajuste de ponto pelo portal (23/10/2026).

- /ponto/configuracao/: o SUPERADMIN escolhe QUALQUER setor para controlar os ajustes e liga a função
  (nasce desligada); quem não é SUPERADMIN não muda;
- /ponto: o colaborador pede alterar / incluir / remover → pedido pendente + aviso ao setor;
- o setor (setor principal) aprova → grava na Sólides; recusa com motivo; edita direto o ponto de
  qualquer pessoa, inclusive o próprio; erro da Sólides fica visível para tentar de novo;
- o que vai para a API de ponto (Swagger v2): POST /modify/punch/1.1, POST /register/late/1.1,
  DELETE /punches/{id}/employee/{id}, GET / (para achar o punchId) — com a rede bloqueada.

Transação desfeita no fim; nada sai para a rede. (Roda também em SQLite descartável:
DJANGO_SETTINGS_MODULE=sqlite_settings com o scratchpad no PYTHONPATH.)
"""
import json
import os
import sys
from datetime import datetime, timedelta
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-pa'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-pa2'}}
django.setup()
from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import requests
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.test.utils import override_settings
from django.utils import timezone

from core.models import Notification
from tangerino import ajustes as aj
from tangerino import client as tc
from tangerino.models import ConfiguracaoTangerino, SolicitacaoAjustePonto as Sol
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


class Resp:
    def __init__(self, status=200, corpo=None):
        self.status_code, self._corpo = status, corpo
        self.content = b'' if corpo is None else json.dumps(corpo).encode()
        self.text = self.content.decode()

    def json(self):
        return self._corpo


ontem = timezone.localdate() - timedelta(days=1)
def hora(h, m, dia=ontem):
    return timezone.make_aware(datetime(dia.year, dia.month, dia.day, h, m))


marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede), \
            mock.patch.object(requests, 'request', sem_rede), \
            mock.patch('tangerino.views.justificativas_edicao', lambda: [{'id': 7, 'description': 'Esqueci de bater'}]), \
            mock.patch('tangerino.views.listar_marcacoes', lambda *a, **k: []), \
            mock.patch('tangerino.ajustes._ressincronizar', lambda sol: None), \
            mock.patch('tangerino.views.integracao_ativa', lambda: True), \
            mock.patch('tangerino.views._jornada_do_usuario', lambda u: None), \
            mock.patch('tangerino.views._resumo_da_jornada', lambda u: None):  # telas sem consultar a Sólides
        rh = Sector.objects.create(name='ZZ RH Ponto')
        loja = Sector.objects.create(name='ZZ Loja Ponto')
        chefe = User.objects.create_user(username='zz.pa.chefe', email='zz.pa.chefe@exemplo-teste.local', password='x',
                                         first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True)
        gestor = User.objects.create_user(username='zz.pa.gestor', email='zz.pa.gestor@exemplo-teste.local', password='x',
                                          first_name='ZZ', last_name='Gestor', hierarchy='ADMINISTRACAO')
        aprovadora = User.objects.create_user(username='zz.pa.rh', email='zz.pa.rh@exemplo-teste.local', password='x',
                                              first_name='ZZ', last_name='Do RH', sector=rh, tangerino_employee_id=901)
        vend = User.objects.create_user(username='zz.pa.vend', email='zz.pa.vend@exemplo-teste.local', password='x',
                                        first_name='ZZ', last_name='Vendedora', sector=loja, tangerino_employee_id=902)
        cc, ca, cv = Client(), Client(), Client()
        cc.force_login(chefe)
        ca.force_login(aprovadora)
        cv.force_login(vend)
        config = ConfiguracaoTangerino.get()
        config.ativo, config.restrito_ao_grupo = True, False  # o módulo de ponto aberto para a vendedora de teste
        config.save()

        print('— Configuração')
        t('nasce desligado', config.ajustes_ativo is False and ConfiguracaoTangerino._meta.get_field('ajustes_ativo').default is False)
        h = cc.get('/ponto/configuracao/').content.decode()
        t('a tela lista os setores para escolher', 'name="ajustes_setor"' in h and f'value="{rh.id}"' in h and f'value="{loja.id}"' in h)
        cc.post('/ponto/configuracao/', {'secao': 'ajustes', 'ajustes_setor': '', 'ajustes_ativo': 'on'})
        config.refresh_from_db()
        t('ligar sem setor é recusado', config.ajustes_ativo is False)
        cg = Client()
        cg.force_login(gestor)
        cg.post('/ponto/configuracao/', {'secao': 'ajustes', 'ajustes_setor': str(loja.id), 'ajustes_ativo': 'on'})
        config.refresh_from_db()
        t('quem administra o ponto mas não é SUPERADMIN não define o setor', config.ajustes_setor_id is None
          and config.ajustes_ativo is False)
        cc.post('/ponto/configuracao/', {'secao': 'ajustes', 'ajustes_setor': str(rh.id), 'ajustes_ativo': 'on'})
        config.refresh_from_db()
        t('SUPERADMIN escolhe qualquer setor e liga', config.ajustes_ativo and config.ajustes_setor_id == rh.id)
        t('o setor principal manda: a pessoa do RH é responsável, a vendedora não',
          aj.e_responsavel(aprovadora) and not aj.e_responsavel(vend))

        print('— Pedido do colaborador (/ponto)')
        h = cv.get('/ponto/').content.decode()
        t('Meu ponto mostra o pedido de ajuste', 'Meus pedidos de ajuste' in h and 'id="tgAjuste"' in h)
        url = '/ponto/ajustes/solicitar/'
        r = cv.post(url, data=json.dumps({'tipo': 'ALTERAR', 'dia': ontem.isoformat(), 'antigo': hora(8, 15).isoformat(),
                                          'novo': hora(8, 0).isoformat(), 'motivo': 'x'}), content_type='application/json')
        t('motivo curto é recusado', r.status_code == 400 and 'motivo' in r.json()['erro'])
        r = cv.post(url, data=json.dumps({'tipo': 'ALTERAR', 'dia': ontem.isoformat(), 'antigo': hora(8, 15).isoformat(),
                                          'novo': (timezone.localtime() + timedelta(hours=2)).isoformat(),
                                          'motivo': 'cheguei antes'}), content_type='application/json')
        t('horário no futuro é recusado', r.status_code == 400)
        antes = Notification.objects.filter(user=aprovadora).count()
        r = cv.post(url, data=json.dumps({'tipo': 'ALTERAR', 'dia': ontem.isoformat(), 'antigo': hora(8, 15).isoformat(),
                                          'novo': hora(8, 0).isoformat(), 'motivo': 'O relógio travou, cheguei às 8h'}),
                    content_type='application/json')
        s1 = Sol.objects.get(usuario=vend, tipo=Sol.ALTERAR)
        t('pedido de alterar fica pendente', r.json()['ok'] and s1.status == Sol.PENDENTE and s1.employee_id == 902)
        t('o setor responsável é avisado', Notification.objects.filter(user=aprovadora).count() == antes + 1)
        r = cv.post(url, data=json.dumps({'tipo': 'ALTERAR', 'dia': ontem.isoformat(), 'antigo': hora(8, 15).isoformat(),
                                          'novo': hora(8, 5).isoformat(), 'motivo': 'de novo a mesma batida'}),
                    content_type='application/json')
        t('não duplica pedido da mesma batida', r.status_code == 400)
        r = cv.post(url, data=json.dumps({'tipo': 'INCLUIR', 'dia': ontem.isoformat(), 'novo': hora(13, 0).isoformat(),
                                          'motivo': 'Esqueci a volta do almoço'}), content_type='application/json')
        t('incluir exige justificativa', r.status_code == 400 and 'justificativa' in r.json()['erro'])
        cv.post(url, data=json.dumps({'tipo': 'INCLUIR', 'dia': ontem.isoformat(), 'novo': hora(13, 0).isoformat(),
                                      'justificativa_id': 7, 'justificativa_texto': 'Esqueci de bater',
                                      'motivo': 'Esqueci a volta do almoço'}), content_type='application/json')
        s2 = Sol.objects.get(usuario=vend, tipo=Sol.INCLUIR)
        cv.post(url, data=json.dumps({'tipo': 'EXCLUIR', 'dia': ontem.isoformat(), 'antigo': hora(18, 0).isoformat(),
                                      'motivo': 'Bati duas vezes'}), content_type='application/json')
        s3 = Sol.objects.get(usuario=vend, tipo=Sol.EXCLUIR)
        r = cv.post(url, data=json.dumps({'tipo': 'EXCLUIR', 'dia': ontem.isoformat(), 'antigo': hora(9, 0).isoformat(),
                                          'motivo': 'tentando o de outra pessoa', 'direto': '1', 'usuario_id': aprovadora.id}),
                    content_type='application/json')
        t('quem não é do setor não edita direto nem o ponto dos outros', r.status_code == 400
          and not Sol.objects.filter(usuario=aprovadora).exists())
        t('a vendedora não abre a tela do setor', cv.get('/ponto/ajustes/').status_code == 302)
        r = cv.post(f'/ponto/ajustes/{s1.id}/decidir/', {'acao': 'aprovar'})
        s1.refresh_from_db()
        t('e não aprova o próprio pedido', s1.status == Sol.PENDENTE)

        print('— Setor responsável')
        h = ca.get('/ponto/ajustes/').content.decode()
        t('aba e tela de ajustes para o setor', 'Ajustes de ponto' in h and 'Aguardando decisão (3)' in h and 'O relógio travou' in h)
        chamadas = []

        def falso_achar(eid, quando):
            chamadas.append(('achar', eid, timezone.localtime(quando).strftime('%H:%M')))
            return 5551, quando

        with mock.patch('tangerino.ajustes.achar_marcacao', falso_achar), \
                mock.patch('tangerino.ajustes.alterar_marcacao', lambda *a, **k: chamadas.append(('alterar',) + a) or {'success': True, 'punchId': 5551}), \
                mock.patch('tangerino.ajustes.registrar_ponto_atrasado', lambda *a, **k: chamadas.append(('incluir',) + a + (k.get('aprovada'),)) or {'success': True}), \
                mock.patch('tangerino.ajustes.excluir_marcacao', lambda *a, **k: chamadas.append(('excluir',) + a) or {}):
            antes = Notification.objects.filter(user=vend).count()
            ca.post(f'/ponto/ajustes/{s1.id}/decidir/', {'acao': 'aprovar'})
            s1.refresh_from_db()
            t('aprovar grava na Sólides (alterar com o punchId achado pelo horário antigo)', s1.status == Sol.APLICADA
              and s1.punch_id == 5551 and ('achar', 902, '08:15') in chamadas
              and any(c[0] == 'alterar' and c[1] == 902 and c[2] == 5551 for c in chamadas))
            t('quem pediu é avisado', Notification.objects.filter(user=vend).count() == antes + 1)
            ca.post(f'/ponto/ajustes/{s2.id}/decidir/', {'acao': 'aprovar'})
            s2.refresh_from_db()
            t('incluir vai como marcação retroativa aprovada', s2.status == Sol.APLICADA
              and any(c[0] == 'incluir' and c[1] == 902 and c[3] == 7 and c[-1] is True for c in chamadas))
            ca.post(f'/ponto/ajustes/{s3.id}/decidir/', {'acao': 'recusar', 'resposta': ''})
            s3.refresh_from_db()
            t('recusar exige motivo', s3.status == Sol.PENDENTE)
            ca.post(f'/ponto/ajustes/{s3.id}/decidir/', {'acao': 'recusar', 'resposta': 'A segunda batida é a correta.'})
            s3.refresh_from_db()
            t('recusado com motivo, sem tocar na Sólides', s3.status == Sol.RECUSADA and not any(c[0] == 'excluir' for c in chamadas))
            r = ca.post(url, data=json.dumps({'tipo': 'EXCLUIR', 'dia': ontem.isoformat(), 'antigo': hora(18, 0).isoformat(),
                                              'motivo': 'Par duplicado no sistema', 'direto': '1', 'usuario_id': vend.id}),
                        content_type='application/json')
            d1 = Sol.objects.filter(usuario=vend, direto=True).first()
            t('edição direta do ponto de outra pessoa grava na hora', r.json()['ok'] and d1 and d1.status == Sol.APLICADA
              and d1.decidido_por == aprovadora and ('excluir', 902, 5551) in chamadas)
            r = ca.post(url, data=json.dumps({'tipo': 'ALTERAR', 'dia': ontem.isoformat(), 'antigo': hora(17, 2).isoformat(),
                                              'novo': hora(17, 0).isoformat(), 'motivo': 'Ajuste do meu próprio ponto',
                                              'direto': '1', 'usuario_id': aprovadora.id}), content_type='application/json')
            t('inclusive o próprio ponto', r.json()['ok'] and Sol.objects.filter(usuario=aprovadora, status=Sol.APLICADA).exists())

        print('— Sólides recusou')
        with mock.patch('tangerino.ajustes.achar_marcacao', lambda *a: (_ for _ in ()).throw(tc.TangerinoError('Tangerino respondeu 400: punch fechado'))):
            r = ca.post(url, data=json.dumps({'tipo': 'ALTERAR', 'dia': ontem.isoformat(), 'antigo': hora(12, 0).isoformat(),
                                              'novo': hora(12, 5).isoformat(), 'motivo': 'Ajuste que vai falhar',
                                              'direto': '1', 'usuario_id': vend.id}), content_type='application/json')
        e1 = Sol.objects.get(usuario=vend, horario_antigo=hora(12, 0))
        t('erro da Sólides vira ERRO com a mensagem (nada some)', not r.json()['ok'] and e1.status == Sol.ERRO
          and 'punch fechado' in e1.retorno_solides)
        h = ca.get('/ponto/ajustes/').content.decode()
        t('e aparece para tentar de novo', 'Tentar de novo' in h)

        print('— O que vai para a API de ponto')
        enviados = []

        def falso_request(metodo, url_, headers=None, timeout=None, **kw):
            enviados.append((metodo, url_, kw.get('json'), kw.get('params')))
            if metodo == 'GET':
                return Resp(200, {'content': [{'id': 777, 'dateIn': tc.para_millis(hora(8, 15)), 'dateOut': tc.para_millis(hora(12, 0))}]})
            return Resp(200, {'success': True})

        with override_settings(TANGERINO_ENABLED=True, TANGERINO_TOKEN='Basic zz-teste'), \
                mock.patch.object(tc.requests, 'request', falso_request):
            pid, _ = tc.achar_marcacao(902, hora(12, 0))
            t('acha o punchId pela saída (GET /punch/ do dia)', pid == 777 and enviados[0][0] == 'GET'
              and enviados[0][1].endswith('/punch/') and enviados[0][3]['employeeId'] == 902)
            tc.alterar_marcacao(902, 777, hora(12, 0), hora(12, 10), observacao='zz')
            m, u, corpo, _ = enviados[-1]
            t('alterar: POST /modify/punch/1.1 com os campos do Swagger', m == 'POST' and u.endswith('/punch/modify/punch/1.1')
              and corpo == {'employeeId': 902, 'punchId': 777, 'punchOldDateHour': tc.para_millis(hora(12, 0)),
                            'punchNewDateHour': tc.para_millis(hora(12, 10)), 'observation': 'zz'}, corpo)
            tc.excluir_marcacao(902, 777)
            t('remover: DELETE /punches/{punchId}/employee/{employeeId}', enviados[-1][0] == 'DELETE'
              and enviados[-1][1].endswith('/punch/punches/777/employee/902'))
            tc.registrar_ponto_atrasado(902, hora(13, 0), 7, observacao='zz', aprovada=True)
            corpo = enviados[-1][2]
            t('incluir: POST /register/late/1.1 aprovado, com justificativa e comentário',
              enviados[-1][1].endswith('/punch/register/late/1.1') and corpo['status'] == 'APPROVED'
              and corpo['manualEditingJustificationId'] == 7 and corpo['comments'] == 'zz')
            try:
                tc.achar_marcacao(902, hora(9, 33))
                t('batida que não existe mais na Sólides dá erro claro', False)
            except tc.TangerinoError as exc:
                t('batida que não existe mais na Sólides dá erro claro', 'Não achei' in str(exc))

        print('— Desligado')
        config.ajustes_ativo = False
        config.save()
        r = cv.post(url, data=json.dumps({'tipo': 'INCLUIR', 'dia': ontem.isoformat(), 'novo': hora(14, 0).isoformat(),
                                          'justificativa_id': 7, 'motivo': 'com a função desligada'}), content_type='application/json')
        t('desligado: não aceita pedido', r.status_code == 400 and 'desligado' in r.json()['erro'])
        t('desligado: some de Meu ponto e da aba', 'Meus pedidos de ajuste' not in cv.get('/ponto/').content.decode()
          and ca.get('/ponto/ajustes/').status_code == 302)
finally:
    marcador.__exit__(Exception, Exception('desfaz'), None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
