"""Visão Renova (/sap/renova/): ZVNB do SAP × chamado de Renova, cobrança às 15h.

Rodar: venv/bin/python teste_sap_renova.py
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

# Banco e arquivos descartáveis: nada toca o Postgres, o MySQL do SAP nem o WhatsApp.
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-renova-'))
(PASTA / 'zz_renova_settings.py').write_text(f"""
from redeconfianca.settings import *              # noqa: F401,F403

DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3',
                          'NAME': r'{PASTA}/banco.sqlite3'}}}}


class SemMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = SemMigrations()

USE_S3 = False
MEDIA_ROOT = r'{PASTA}/media'
MEDIA_URL = '/media/'
STORAGES = {{
    'default': {{'BACKEND': 'django.core.files.storage.FileSystemStorage'}},
    'staticfiles': {{'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
}}
DEBUG = False
OPENAI_API_KEY = ''
EVOLUTION_API_URL = 'http://evolution.invalido'
EVOLUTION_API_KEY = 'x'
EVOLUTION_INSTANCE = 'x'
BASE_URL = 'https://portal.exemplo'
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
CACHES = {{
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-renova'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-renova-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_renova_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from auditoria_sap import mysql, renova
from auditoria_sap.models import CobrancaRenova, ConfiguracaoRenovaSap, VendaRenova
from communications.models import CommunicationGroup
from core import agendador_diario
from cursos.models import GrupoWhatsappLoja
from tickets.models import Category, Ticket
from users.models import Sector

User = get_user_model()
OK = FALHAS = 0


def t(nome, cond, extra=''):
    global OK, FALHAS
    if cond:
        OK += 1
        print('  OK  ', nome)
    else:
        FALHAS += 1
        print('  FALHA', nome, extra)


HOJE = timezone.localdate()
D = lambda n: HOJE - timedelta(days=n)

monts = Sector.objects.create(name='Loja Montserrat')
gloria = Sector.objects.create(name='Loja Glória')
sede = Sector.objects.create(name='Sede Administrativa')
renova_setor = Sector.objects.create(name='Renova Backoffice')
cat_allied = Category.objects.create(sector=renova_setor, name='RENOVA ALLIED')
cat_outra = Category.objects.create(sector=renova_setor, name='Troca de aparelho')
GrupoWhatsappLoja.objects.create(setor=monts, grupo='120363000000000001')

boss = User.objects.create_user(username='zz.ren.boss', email='zz.ren.boss@exemplo-teste.local', password='x',
                                first_name='ZZ', last_name='Boss', hierarchy='SUPERADMIN')
comum = User.objects.create_user(username='zz.ren.comum', email='zz.ren.comum@exemplo-teste.local', password='x',
                                 first_name='ZZ', last_name='Comum', hierarchy='PADRAO', sector=monts)
ger_g = User.objects.create_user(username='zz.ren.gg', email='zz.ren.gg@exemplo-teste.local', password='x',
                                 first_name='ZZ', last_name='Gerente Gloria', hierarchy='PADRAO', sector=gloria)
ger_m = User.objects.create_user(username='zz.ren.gm', email='zz.ren.gm@exemplo-teste.local', password='x',
                                 first_name='ZZ', last_name='Gerente Monts', hierarchy='PADRAO', sector=monts)
grupo_ger = CommunicationGroup.objects.create(name='GERENTES', created_by=boss) if hasattr(CommunicationGroup, 'created_by') else CommunicationGroup.objects.create(name='GERENTES')
grupo_ger.members.add(ger_g, ger_m)


def ordem(doc, cpf, nf, escr, dias, material='SSG GALAXY A576B 256GB CZPPB/P291/14'):
    return {'id': int(doc[-4:]), 'doc_vendas': doc, 'num_fat': nf, 'cnpj_cpf': cpf, 'dt_fatura': D(dias),
            'escr_vend': escr, 'desc_material': material, 'hexadecimal': '358852343183374',
            'vl_faturado': Decimal('1599.01'), 'desconto': Decimal('-1444.32'), 'status_nf': 'COM NF'}


ORDENS = [
    ordem('2050000001', '111.111.111-11', 'NF1', 'E047', 2),   # A: chamado cita a venda do Vivo Go
    ordem('2050000002', '222.222.222-22', 'NF2', 'E047', 2),   # B: chamado com o CPF no texto
    ordem('2050000004', '444.444.444-44', 'NF4', 'E047', 1),   # D: sem chamado → cobrada
    ordem('2050000005', '555.555.555-55', 'NF5', 'E047', 3),   # E: chamado de outra categoria
    ordem('2050000006', '666.666.666-66', 'NF6', 'E065', 2),   # F: chamado velho demais
    ordem('2050000007', '777.777.777-77', '', 'E999', 2),      # G: sem loja
    ordem('2050000008', '888.888.888-88', 'NF8', 'E047', 30),  # H: fora da janela de cobrança
]
VENDAS = [
    {'ID_VENDA': '2751333-1', 'CPF_VIVOGO_NORMALIZADO': '11111111111', 'CPF_SAP_NORMALIZADO': '11111111111',
     'NUM_FAT_SAP': 'NF1', 'PDV': 'MONTSERRAT', 'NOME_CLIENTE': 'MARIA DA SILVA SOUZA'},
    {'ID_VENDA': '2751400', 'CPF_VIVOGO_NORMALIZADO': '44444444444', 'CPF_SAP_NORMALIZADO': '',
     'NUM_FAT_SAP': 'NF4', 'PDV': 'MONTSERRAT', 'NOME_CLIENTE': 'JOAO PEREIRA LIMA'},
    {'ID_VENDA': '2751500', 'CPF_VIVOGO_NORMALIZADO': '55555555555', 'CPF_SAP_NORMALIZADO': '',
     'NUM_FAT_SAP': 'NF5', 'PDV': 'SEDE ADMINISTRATIVA', 'NOME_CLIENTE': 'ANA'},
    {'ID_VENDA': '2751600', 'CPF_VIVOGO_NORMALIZADO': '66666666666', 'CPF_SAP_NORMALIZADO': '',
     'NUM_FAT_SAP': 'NF6', 'PDV': 'GLÓRIA', 'NOME_CLIENTE': 'PEDRO'},
    {'ID_VENDA': '2751800', 'CPF_VIVOGO_NORMALIZADO': '88888888888', 'CPF_SAP_NORMALIZADO': '',
     'NUM_FAT_SAP': 'NF8', 'PDV': 'MONTSERRAT', 'NOME_CLIENTE': 'LUCAS'},
]


def chamado(cat, texto, dias=0):
    tk = Ticket.objects.create(title='Celular Renova', description=texto, sector=renova_setor, category=cat,
                               created_by=comum)
    if dias:
        Ticket.objects.filter(pk=tk.pk).update(created_at=timezone.now() - timedelta(days=dias))
    return Ticket.objects.get(pk=tk.pk)


tA = chamado(cat_allied, 'Segue Renova ALLIED:\r\nVenda no Vivo Go: <span style="font-size: 12.5px;">2751333</span>\r\nIMEI: 1')
tB = chamado(cat_allied, 'Renova do cliente CPF 222.222.222-22, voucher tc1')
tE = chamado(cat_outra, 'Venda no Vivo Go: 2751500')
tF = chamado(cat_allied, 'Venda no Vivo Go: 2751600', dias=100)

print('\n== LEITURA E CASAMENTO ==')
t('extrai venda e CPF do texto do chamado',
  renova.chaves_do_chamado('Venda no Vivo Go: <span>2748287-1</span> CPF 123.456.789-09')
  == ({'2748287'}, {'12345678909'}))
with mock.patch.object(mysql, 'ler_renovas', return_value=ORDENS), \
        mock.patch.object(mysql, 'ler_vendas_vivogo', return_value=VENDAS):
    resumo = renova.ler_do_sap()
V = {v.ordem[-1]: v for v in VendaRenova.objects.all()}
t('lê uma linha por ordem ZVNB', resumo['ordens'] == 7 and VendaRenova.objects.count() == 7, resumo)
t('A: casa pela venda do Vivo Go do CPF', V['1'].chamado_id == tA.id and V['1'].chamado_como == 'venda', (V['1'].chamado_id, V['1'].chamado_como))
t('B: casa pelo CPF escrito no chamado', V['2'].chamado_id == tB.id and V['2'].chamado_como == 'cpf')
t('D: sem chamado', V['4'].chamado_id is None and V['4'].pendente)
t('E: chamado de categoria que não é Renova não conta', V['5'].chamado_id is None)
t('F: chamado muito antes da fatura não conta', V['6'].chamado_id is None)
t('loja pelo escritório de vendas (E047 → Montserrat), sem cair na Sede', V['4'].setor_id == monts.id
  and V['5'].setor_id == monts.id, (V['5'].loja,))
t('loja pela venda quando o escritório é único (E065 → Glória)', V['6'].setor_id == gloria.id, V['6'].loja)
t('G: escritório desconhecido e sem nota fica sem loja', V['7'].setor_id is None and V['7'].loja == '')
t('nome do cliente vem do Vivo Go e as vendas do CPF ficam guardadas',
  V['4'].cliente_nome == 'JOAO PEREIRA LIMA' and V['4'].vendas_vivogo == ['2751400'])
t('CPF mascarado para o grupo', V['4'].cpf_mascarado == '***.444.444-**')

# chamado aberto depois: a próxima leitura acha
tD = chamado(cat_allied, 'Venda no Vivo Go: 2751400-1')
t('casar_chamados acha o chamado novo', renova.casar_chamados() == 1
  and VendaRenova.objects.get(ordem='2050000004').chamado_id == tD.id)
Ticket.objects.filter(pk=tD.pk).delete()
VendaRenova.objects.filter(ordem='2050000004').update(chamado=None, chamado_como='')
t('chamado apagado solta a venda (volta a ser cobrada)', VendaRenova.objects.get(ordem='2050000004').pendente)

print('\n== COBRANÇA ==')
cfg = ConfiguracaoRenovaSap.get()
t('nasce desligada', not cfg.cobranca_ativa and cfg.janela_dias == 7)
t('tarefa das 15h no agendador', any(k == 'sap_renova_cobranca' and h.hour == 15 and c == 'auditoria_sap.renova.tarefa_diaria'
                                    for k, h, c, _ in agendador_diario.TAREFAS))
with mock.patch('core.evolution.enviar_texto') as env:
    t('desligada não manda', renova.tarefa_diaria() == 'Cobrança de Renova desligada.' and not env.called)
p = renova.previa()
lojas = {l['setor'].id: l for l in p['lojas']}
t('prévia: Montserrat com D e E (H fora da janela)', [v.ordem for v in lojas[monts.id]['vendas']] == ['2050000005', '2050000004'],
  [v.ordem for v in lojas.get(monts.id, {}).get('vendas', [])])
t('prévia: Glória sem grupo; G sem loja', lojas[gloria.id]['grupo'] is None and len(p['sem_loja']) == 1)
texto = lojas[monts.id]['texto']
t('texto: nome, CPF mascarado, aparelho, venda do Vivo Go e link', 'Joao Pereira' in texto and '***.444.444-**' in texto
  and '44444444444' not in texto and 'SSG GALAXY A576B 256GB CZPPB' in texto and 'Vivo Go 2751400' in texto
  and 'https://portal.exemplo/tickets/create/' in texto and '15h' in texto, texto)
ConfiguracaoRenovaSap.objects.update(cobranca_ativa=True)
with mock.patch.object(mysql, 'ler_renovas', return_value=ORDENS), \
        mock.patch.object(mysql, 'ler_vendas_vivogo', return_value=VENDAS), \
        mock.patch('core.evolution.enviar_texto', return_value=(True, 'ok')) as env:
    res = renova.tarefa_diaria()
t('ligada: manda só para quem tem grupo', env.call_count == 1 and env.call_args[0][0] == '120363000000000001@g.us', (env.call_args, res))
t('resumo da tarefa', '1 loja(s) cobradas (1 enviadas' in res and '1 sem grupo' in res and '1 venda(s) sem loja' in res, res)
t('conta a cobrança na venda', VendaRenova.objects.get(ordem='2050000004').cobrancas == 1
  and CobrancaRenova.objects.filter(setor=monts, enviado=True).count() == 1)
with mock.patch('core.evolution.enviar_texto', return_value=(True, 'ok')) as env:
    r2 = renova.cobrar(CobrancaRenova.MANUAL, user=boss)
t('não cobra a mesma loja de novo em 30 min', not env.called and r2['recentes'] == [monts])
CobrancaRenova.objects.update(criado_em=timezone.now() - timedelta(hours=2))
with mock.patch('core.evolution.enviar_texto', return_value=(False, 'grupo não existe')):
    r3 = renova.cobrar(CobrancaRenova.MANUAL, user=boss)
t('falha fica registrada e não conta cobrança', r3['resumo']['falhas'] == 1
  and VendaRenova.objects.get(ordem='2050000004').cobrancas == 1
  and CobrancaRenova.objects.filter(enviado=False, detalhe='grupo não existe').exists())

print('\n== SAIU DO SAP ==')
with mock.patch.object(mysql, 'ler_renovas', return_value=[o for o in ORDENS if o['doc_vendas'] != '2050000005']), \
        mock.patch.object(mysql, 'ler_vendas_vivogo', return_value=VENDAS):
    renova.ler_do_sap()
t('ordem que sumiu do SAP sai da cobrança', not VendaRenova.objects.get(ordem='2050000005').ativa
  and '2050000005' not in [v.ordem for v in renova.a_cobrar()])

print('\n== TELA ==')
cb = Client(); cb.force_login(boss)
h = cb.get('/sap/renova/').content.decode()
t('SUPERADMIN vê a aba, a lista sem chamado e a configuração', 'Visão Renova' in h and '2050000004' in h
  and '2050000001' not in h and 'Cobrar sozinho todo dia às 15h' in h and '***.444.444-**' in h)
h = cb.get('/sap/renova/?situacao=com_chamado').content.decode()
t('aba "com chamado" mostra o chamado e como achou', '2050000001' in h and f'#{tA.id}' in h and 'Venda do Vivo Go' in h)
print('\n== ABA CONCLUÍDOS ==')
from tickets.models import Ticket as _Ticket  # noqa: E402
_va = VendaRenova.objects.get(ordem='2050000001')
h = cb.get('/sap/renova/').content.decode()
t('abas: em andamento e Concluídos', 'Chamado em andamento' in h and '?situacao=concluidas' in h)
h = cb.get('/sap/renova/?situacao=concluidas').content.decode()
t('chamado aberto não aparece em Concluídos', '2050000001' not in h and 'Nenhum chamado de Renova concluído' in h)
_Ticket.objects.filter(pk=_va.chamado_id).update(status='RESOLVIDO', resolved_at=timezone.now() - timedelta(hours=1))
h = cb.get('/sap/renova/?situacao=concluidas').content.decode()
t('chamado resolvido vai para Concluídos, com a data', '2050000001' in h and 'Resolvido' in h and 'Concluído em' in h, h[h.find('2050000001'):h.find('2050000001') + 900])
h = cb.get('/sap/renova/?situacao=com_chamado').content.decode()
t('e sai de "em andamento"', '2050000001' not in h)
_outras = list(VendaRenova.objects.filter(chamado__isnull=False).exclude(pk=_va.pk))
if _outras:
    _Ticket.objects.filter(pk=_outras[0].chamado_id).update(status='FECHADO', closed_at=timezone.now())
    h = cb.get('/sap/renova/?situacao=concluidas').content.decode()
    t('o concluído mais recente vem primeiro', h.find(_outras[0].ordem) < h.find('2050000001') and h.find(_outras[0].ordem) > 0)
    _Ticket.objects.filter(pk=_outras[0].chamado_id).update(status='ABERTO', closed_at=None)
_Ticket.objects.filter(pk=_va.chamado_id).update(status='ENCERRADO_SEM_SOLUCAO', resolved_at=None, closed_at=timezone.now())
h = cb.get('/sap/renova/?situacao=concluidas').content.decode()
t('encerrado sem solução também é concluído (com o status)', '2050000001' in h and 'Encerrado sem solução' in h)
_Ticket.objects.filter(pk=_va.chamado_id).update(status='ABERTO', resolved_at=None, closed_at=None)
h = cb.get('/sap/renova/?situacao=com_chamado').content.decode()
t('reaberto volta para "em andamento"', '2050000001' in h)

h = cb.get('/sap/renova/?situacao=todas&q=2751400').content.decode()
t('busca pela venda do Vivo Go', '2050000004' in h and '2050000002' not in h)
cg = Client(); cg.force_login(ger_g)
h = cg.get('/sap/renova/?situacao=todas').content.decode()
t('gerente vê só a loja dele, sem configuração', '2050000006' in h and '2050000004' not in h
  and 'Cobrar sozinho' not in h, h[:300])
cc = Client(); cc.force_login(comum)
t('quem não é da Visão SAP não entra', cc.get('/sap/renova/').status_code == 302)
d = VendaRenova.objects.get(ordem='2050000004')
cg.post(f'/sap/renova/{d.id}/marcar/', {'acao': 'dispensar', 'observacao': 'x'})
t('gerente não mexe em venda de outra loja', not VendaRenova.objects.get(pk=d.pk).dispensada)
cb.post(f'/sap/renova/{d.id}/marcar/', {'acao': 'dispensar', 'observacao': ''})
t('"não precisa" exige o motivo', not VendaRenova.objects.get(pk=d.pk).dispensada)
cb.post(f'/sap/renova/{d.id}/marcar/', {'acao': 'dispensar', 'observacao': 'Renova cancelado pelo cliente'})
d.refresh_from_db()
t('"não precisa" grava quem e por quê e sai da cobrança', d.dispensada and d.dispensada_por == boss
  and d.observacao == 'Renova cancelado pelo cliente' and d not in renova.a_cobrar())
cb.post(f'/sap/renova/{d.id}/marcar/', {'acao': 'reabrir'})
d.refresh_from_db()
t('voltar a cobrar', d.pendente)
r = cb.post(f'/sap/renova/{d.id}/marcar/', {'acao': 'ligar', 'chamado': '999999'}, follow=True)
t('ligar a chamado que não existe avisa', 'Chamado não encontrado' in r.content.decode() and VendaRenova.objects.get(pk=d.pk).pendente)
cb.post(f'/sap/renova/{d.id}/marcar/', {'acao': 'ligar', 'chamado': f'#{tB.id}'})
d.refresh_from_db()
t('ligar à mão', d.chamado_id == tB.id and d.chamado_como == 'manual')
cb.post('/sap/renova/configurar/', {'acao': 'salvar', 'janela_dias': '40'})
cfg = ConfiguracaoRenovaSap.get()
t('salvar desliga (checkbox vazio) e muda a janela', not cfg.cobranca_ativa and cfg.janela_dias == 40)
cg.post('/sap/renova/configurar/', {'acao': 'salvar', 'cobranca_ativa': 'on', 'janela_dias': '3'})
t('gerente não configura', ConfiguracaoRenovaSap.get().janela_dias == 40)
with mock.patch.object(mysql, 'ler_renovas', side_effect=mysql.SapIndisponivel('fora do ar')):
    r = cb.post('/sap/renova/configurar/', {'acao': 'ler'}, follow=True)
t('SAP fora do ar vira mensagem', r.status_code == 200 and 'fora do ar' in r.content.decode())

import glob, re as _re
ruins = [f for f in ['templates/auditoria_sap/renova.html'] for m in _re.finditer(r'\{#(.*?)#\}', open(f).read(), _re.S) if '\n' in m.group(1)]
t('nenhum comentário {# #} de várias linhas', not ruins)

print(f'\n{OK} OK, {FALHAS} falha(s)')
