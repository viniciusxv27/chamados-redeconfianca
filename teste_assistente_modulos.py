"""Assistente: Ponto, Simulador, Vendas e Resultados (09/11/2026).

Pedido: o /assistente/ precisa de acesso completo a /ponto/, /simulator/ e
/vendas/; aos resultados só do próprio usuário para quem não é SUPERADMIN, e
completo para SUPERADMIN. Aqui se confere, sobretudo, o recorte: quem não pode
ver o dado de outra pessoa na tela também não o vê pelo assistente.

Banco descartável (sqlite, schema direto dos models) e nada sai para a rede: o
Tangerino, o MySQL de vendas, o motor do simulador e o painel Parciais são
dublês; qualquer requests.get/post de verdade derruba o teste.
"""
import os
import pathlib
import sys
import tempfile
import types
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-assist-modulos-'))
(PASTA / 'zz_assist_modulos_settings.py').write_text(f"""
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
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
CACHES = {{
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-assist-mod'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-assist-mod-2'}},
}}
TANGERINO_ENABLED = True
TANGERINO_TOKEN = 'zz-token-ficticio'
PARCIAIS_URL = 'https://parciais.exemplo-teste.local'
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_assist_modulos_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')


class _Anthropic:                       # o pacote não está no venv local; nada aqui fala com o Claude
    def __init__(self, *a, **k):
        raise AssertionError('teste tentou falar com o Claude')


sys.modules.setdefault('anthropic', types.SimpleNamespace(Anthropic=_Anthropic))

django.setup()

from django.core.management import call_command
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

import datetime as dt
from decimal import Decimal

import requests
from django.core.cache import cache
from django.utils import timezone

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
    raise AssertionError('teste tentou sair para a rede')


for nome in ('get', 'post', 'request', 'put', 'delete', 'patch'):
    mock.patch.object(requests, nome, sem_rede).start()
mock.patch('requests.sessions.Session.request', sem_rede).start()

from assistente import ferramentas  # noqa: E402
from communications.models import CommunicationGroup  # noqa: E402
from users.models import Sector  # noqa: E402


def roda(nome, args, user):
    return ferramentas.executar(nome, args, user, turno='turno-1')


SENHA = 'S3nha!teste'


def novo(apelido, primeiro, ultimo, **extra):
    return User.objects.create_user(username=f'zzam.{apelido}', email=f'zzam.{apelido}@exemplo-teste.local',
                                    password=SENHA, first_name=primeiro, last_name=ultimo, **extra)


centro = Sector.objects.create(name='LOJA ZZ CENTRO')
norte = Sector.objects.create(name='LOJA ZZ NORTE')
admin = novo('admin', 'Zélia', 'Administradora', hierarchy='SUPERADMIN')
rh = novo('rh', 'Rui', 'Recursos', hierarchy='ADMIN', sector=centro, tangerino_employee_id=900)
ana = novo('ana', 'Ana Lúcia', 'Souza', hierarchy='PADRAO', sector=centro, tangerino_employee_id=101)
beto = novo('beto', 'Beto', 'Norte', hierarchy='PADRAO', sector=norte, tangerino_employee_id=102)
carla = novo('carla', 'Carla', 'Centro', hierarchy='PADRAO', sector=centro, tangerino_employee_id=103)
gerente = novo('gerente', 'Gerson', 'Centro', hierarchy='PADRAO', sector=centro)
coord = novo('coord', 'Cora', 'Coordenadora', hierarchy='PADRAO', sector=norte)
CommunicationGroup.objects.create(name='GERENTES', created_by=admin).members.add(gerente)
CommunicationGroup.objects.create(name='COORDENADORES', created_by=admin).members.add(coord)
from simulator.models import CoordinatorStoreAccess  # noqa: E402

CoordinatorStoreAccess.objects.create(coordinator=coord).sectors.set([norte])

print('== AS FERRAMENTAS ==')
esquema = {x['name'] for x in ferramentas.tools_schema()}
t('ponto, simulador e vendas estão no assistente',
  {'ponto_consultar', 'simulador_consultar', 'vendas_consultar', 'resultados_comerciais'} <= esquema, esquema)
t('nenhuma das novas é ação (só leitura)',
  not any(ferramentas.TOOLS[n].get('acao') for n in ('ponto_consultar', 'simulador_consultar', 'vendas_consultar')))
t('o_que inválido explica as opções', 'o_que inválido' in roda('ponto_consultar', {'o_que': 'xyz'}, ana))


# ─── PONTO ───────────────────────────────────────────────────────────────────
print('\n== PONTO ==')
from tangerino import client as tclient  # noqa: E402
from tangerino import ferias as tferias  # noqa: E402
from tangerino import ponto as tponto  # noqa: E402
from tangerino import views as tviews  # noqa: E402
from tangerino.models import ConfiguracaoTangerino, SaldoHoras  # noqa: E402

config = ConfiguracaoTangerino.get()
config.restrito_ao_grupo = False
config.save()

hoje = timezone.localdate()


def ms(dia, h, m=0):
    return tclient.para_millis(timezone.make_aware(dt.datetime.combine(dia, dt.time(h, m))))


ontem = hoje - dt.timedelta(days=1)
PARES = [
    {'id': 1, 'employeeId': 101, 'dateIn': ms(hoje, 8), 'dateOut': ms(hoje, 12)},
    {'id': 2, 'employeeId': 101, 'dateIn': ms(ontem, 9), 'dateOut': None},   # ponto em aberto ontem
    {'id': 3, 'employeeId': 102, 'dateIn': ms(hoje, 10), 'dateOut': None},
]
FUNCIONARIOS = [{'id': 101, 'name': 'ANA LUCIA SOUZA'}, {'id': 102, 'name': 'BETO NORTE'},
                {'id': 103, 'name': 'CARLA CENTRO'}, {'id': 900, 'name': 'RUI RECURSOS'}]
chamadas_api = []


def falso_marcacoes(inicio, fim, employee_id=None, employee_ids=None, **k):
    chamadas_api.append(('marcacoes', employee_id))
    return [p for p in PARES if employee_id in (None, p['employeeId'])]


def falso_funcionarios(*a, **k):
    return FUNCIONARIOS


for modulo in (tclient, tviews, tponto, tferias):
    if hasattr(modulo, 'listar_marcacoes'):
        mock.patch.object(modulo, 'listar_marcacoes', falso_marcacoes).start()
    if hasattr(modulo, 'listar_funcionarios'):
        mock.patch.object(modulo, 'listar_funcionarios', falso_funcionarios).start()
    if hasattr(modulo, 'listar_ferias'):
        mock.patch.object(modulo, 'listar_ferias', lambda *a, **k: []).start()
for perigoso in ('registrar_ponto', 'registrar_ponto_atrasado', 'alterar_marcacao', 'excluir_marcacao', '_post'):
    mock.patch.object(tclient, perigoso, sem_rede).start()

r = roda('ponto_consultar', {'o_que': 'hoje'}, ana)
t('colaborador vê o próprio ponto de hoje', 'Ponto de hoje — Você' in r and 'entrada 08:00' in r, r)
t('e o ponto em aberto de ontem', 'Ponto em aberto' in r and ontem.strftime('%d/%m/%Y') in r, r)
r = roda('ponto_consultar', {'o_que': 'hoje', 'usuario_id': beto.pk}, ana)
t('colaborador NÃO vê o ponto de outra pessoa', 'só pode consultar' in r and 'entrada 10:00' not in r, r)
r = roda('ponto_consultar', {'o_que': 'folha', 'usuario_id': beto.pk}, ana)
t('nem a folha de outra pessoa', 'só pode consultar' in r, r)
t('nem o painel da equipe', 'só para quem administra' in roda('ponto_consultar', {'o_que': 'equipe'}, ana))
t('nem o relatório', 'só para quem administra' in roda('ponto_consultar', {'o_que': 'relatorio'}, ana))
t('nem o banco de horas de todos', 'só para quem administra' in roda(
    'ponto_consultar', {'o_que': 'banco_horas', 'todos': True}, ana))
t('nem as férias da equipe', 'só para quem administra' in roda('ponto_consultar', {'o_que': 'ferias', 'equipe': True}, ana))
t('nem a caixa do DP', 'caixa do DP' in roda('ponto_consultar', {'o_que': 'solicitacoes', 'fila': True}, ana))
r = roda('ponto_consultar', {'o_que': 'pessoas'}, ana)
t('a lista de pessoas do colaborador é só ele', 'só o próprio ponto' in r and 'Beto' not in r, r)

r = roda('ponto_consultar', {'o_que': 'periodo', 'de': (hoje - dt.timedelta(days=3)).isoformat()}, ana)
t('período dia a dia com totais', 'Totais: trabalhado' in r and 'PONTO EM ABERTO' in r, r)
r = roda('ponto_consultar', {'o_que': 'folha'}, ana)
t('folha do mês ao vivo', 'Folha de ponto' in r and 'Dias trabalhados' in r, r)
SaldoHoras.objects.create(employee_id=101, usuario=ana, nome='ANA LUCIA SOUZA', saldo_minutos=-95,
                          periodo_inicio=hoje.replace(day=1), periodo_fim=hoje, sincronizado_em=timezone.now())
SaldoHoras.objects.create(employee_id=102, usuario=beto, nome='BETO NORTE', saldo_minutos=300,
                          periodo_inicio=hoje.replace(day=1), periodo_fim=hoje, sincronizado_em=timezone.now())
r = roda('ponto_consultar', {'o_que': 'banco_horas'}, ana)
t('banco de horas próprio', '-1:35' in r and 'devendo' in r and 'BETO' not in r, r)
r = roda('ponto_consultar', {'o_que': 'ferias'}, ana)
t('férias próprias respondem', 'Férias de Você' in r or 'saldo' in r, r)
r = roda('ponto_consultar', {'o_que': 'solicitacoes'}, ana)
t('solicitações próprias ao DP', 'não tem solicitações' in r, r)
r = roda('ponto_consultar', {'o_que': 'escala'}, ana)
t('escala própria', 'Escala da semana' in r, r)

r = roda('ponto_consultar', {'o_que': 'hoje', 'usuario_id': beto.pk}, rh)
t('ADMINISTRAÇÃO vê o ponto de outra pessoa', 'Beto Norte' in r and 'entrada 10:00' in r, r)
r = roda('ponto_consultar', {'o_que': 'equipe'}, rh)
t('e o painel da equipe', 'Ponto da equipe' in r and 'ANA LUCIA SOUZA' in r and 'Sem marcação' in r, r)
r = roda('ponto_consultar', {'o_que': 'banco_horas', 'todos': True}, rh)
t('e o banco de horas de todos', 'ANA LUCIA SOUZA' in r and 'BETO NORTE' in r, r)
r = roda('ponto_consultar', {'o_que': 'relatorio'}, rh)
t('e o relatório de pendências', 'Relatório de ponto' in r, r)
r = roda('ponto_consultar', {'o_que': 'pessoas', 'busca': 'beto'}, rh)
t('e a lista de pessoas com id', f'(id {beto.pk})' in r, r)

config.ativo = False
config.save()
t('ponto desligado: colaborador recebe a recusa', 'não está liberado' in roda('ponto_consultar', {'o_que': 'hoje'}, ana))
config.ativo = True
config.save()
t('nenhuma chamada de escrita ao Tangerino', all(c[0] == 'marcacoes' for c in chamadas_api))


# ─── SIMULADOR ───────────────────────────────────────────────────────────────
print('\n== SIMULADOR ==')
from simulator import services as sv  # noqa: E402
from simulator import sql_realizado  # noqa: E402
from simulator.tabela import DATASET_KEY  # noqa: E402

calculados = []


def falso_sim(papel):
    def calc(user, dados, hunter=None, view_mode='projecao', **k):
        calculados.append((papel, user.pk, view_mode))
        return {'user_name': user.get_full_name(), 'pdv': 'ZZ', 'coordinator': '', 'view_mode': view_mode,
                'rows': [{'key': 'movel', 'label': 'Móvel', 'meta': 1000.0, 'proj': 800.0, 'attainment': 0.8,
                          'total_with_pdv': 120.0},
                         {'key': 'fixa', 'label': 'Fixa', 'meta': 10.0, 'proj': 0.0, 'quantity': 4.0, 'attainment': 0.4,
                          'total_with_pdv': 0.0}],
                'totals': {'total_with_pdv': 120.0, 'hunter2': 0.0, 'hunter3': 0.0, 'bonus_6_7': 0.0,
                           'ganho_total': 120.0}}
    return calc


class _Prefetch:
    def __enter__(self):
        return {}

    def __exit__(self, *a):
        return False


mock.patch.object(sql_realizado, 'realized_prefetch', lambda *a, **k: _Prefetch()).start()
mock.patch.object(sv, 'get_factor_set', lambda papel, *a, **k: types.SimpleNamespace(data={})).start()
mock.patch.object(sv, 'compute_consultor_simulation', falso_sim('consultor')).start()
mock.patch.object(sv, 'compute_gerente_simulation', falso_sim('gerente')).start()
mock.patch.object(sv, 'compute_coordenador_simulation', falso_sim('coordenador')).start()
mock.patch.object(sv, 'get_business_days_info', lambda *a, **k: (5, 20)).start()

r = roda('simulador_consultar', {'o_que': 'comissao'}, ana)
t('consultor vê a própria comissão', 'Ana Lúcia Souza' in r and 'GANHO TOTAL: R$ 120,00' in r
  and calculados[-1] == ('consultor', ana.pk, 'projecao'), r)
t('com meta diária que falta', 'falta' in r and '/dia útil' in r, r)
r = roda('simulador_consultar', {'o_que': 'comissao', 'usuario_id': beto.pk}, ana)
t('consultor NÃO vê alguém de outra loja', 'não está entre as que você pode ver' in r and calculados[-1][1] != beto.pk, r)
from simulator import views as simviews  # noqa: E402

r = roda('simulador_consultar', {'o_que': 'pessoas'}, ana)
da_tela = {u.get_full_name() for u in simviews.get_sector_consultors(ana)}
t('a lista do consultor é exatamente a do seletor da tela (PADRÃO da loja dele)',
  all(n in r for n in da_tela) and 'Beto' not in r and 'Cora' not in r, (da_tela, r))
r = roda('simulador_consultar', {'o_que': 'comissao', 'visao': 'realizado'}, ana)
t('visão realizado', calculados[-1] == ('consultor', ana.pk, 'realizado') and 'REALIZADO' in r, r)
t('modo simulador com valores digitados fica na tela', 'só na tela' in roda(
    'simulador_consultar', {'o_que': 'comissao', 'visao': 'simulador'}, ana))

r = roda('simulador_consultar', {'o_que': 'comissao', 'usuario_id': ana.pk}, gerente)
t('gerente vê quem é da loja dele', 'Ana Lúcia Souza' in r and calculados[-1][1] == ana.pk, r)
r = roda('simulador_consultar', {'o_que': 'comissao', 'usuario_id': beto.pk}, gerente)
t('mas não de outra loja', 'não está entre' in r, r)
r = roda('simulador_consultar', {'o_que': 'comissao', 'usuario_id': beto.pk}, coord)
t('coordenador vê a carteira dele', 'Beto Norte' in r and calculados[-1][1] == beto.pk, r)
r = roda('simulador_consultar', {'o_que': 'comissao', 'usuario_id': ana.pk}, coord)
t('mas não loja fora da carteira', 'não está entre' in r, r)
r = roda('simulador_consultar', {'o_que': 'comissao'}, admin)
t('SUPERADMIN precisa dizer de quem', 'escolha de quem' in r, r)
r = roda('simulador_consultar', {'o_que': 'comissao', 'busca': 'beto'}, admin)
t('SUPERADMIN vê qualquer um (por nome)', 'Beto Norte' in r and calculados[-1][1] == beto.pk, r)

cache.set(DATASET_KEY, {'generated_at': timezone.now(), 'built_on': hoje, 'rows': [
    {'id': u.pk, 'name': u.get_full_name(), 'role_key': 'consultor', 'role_label': 'Consultor',
     'sector': u.sector.name, 'sector_id': u.sector_id, 'coordinator': '', 'realizado': 100.0, 'projecao': 150.0,
     'diferenca': 50.0, 'fatores': [], 'motivo': 'Móvel cresce', 'has_data': True, 'erro': ''}
    for u in (ana, beto, carla)]}, 600)
r = roda('simulador_consultar', {'o_que': 'tabela'}, admin)
t('SUPERADMIN vê a tabela da rede inteira', all(n in r for n in ('Ana Lúcia', 'Beto Norte', 'Carla Centro')), r)
r = roda('simulador_consultar', {'o_que': 'tabela'}, gerente)
t('gerente vê a tabela só da loja', 'Ana Lúcia' in r and 'Carla Centro' in r and 'Beto Norte' not in r, r)
r = roda('simulador_consultar', {'o_que': 'tabela'}, ana)
t('consultor sem comissionamento liberado não abre a tabela', 'fica com o comissionamento' in r, r)


# ─── VENDAS ──────────────────────────────────────────────────────────────────
print('\n== VENDAS ==')
from vendas.models import AcessoVendas, ItemPreco, Venda, VendaProduto  # noqa: E402

agora = timezone.now()
va = Venda.objects.create(loja=centro, vendedor=ana, created_by=ana, cliente_nome='CLIENTE DA ANA',
                          cliente_cpf='11122233344', data_venda=agora)
VendaProduto.objects.create(venda=va, nome_produto='Galaxy ZZ', sku='SKUZZ1', qtde=1, valor_venda=Decimal('1500'))
vb = Venda.objects.create(loja=norte, vendedor=beto, created_by=beto, cliente_nome='CLIENTE DO BETO',
                          cliente_cpf='99988877766', data_venda=agora)
VendaProduto.objects.create(venda=vb, nome_produto='iPhone ZZ', qtde=2, valor_venda=Decimal('5000'))
ItemPreco.objects.create(categoria='SMARTPHONES', nome='GALAXY ZZ 128GB', cod_sap='777', valor=Decimal('1999'),
                         extra={'skus': [{'sku': 'SKUZZ1', 'cor': 'Preto'}, {'sku': 'SKUZZ2', 'cor': 'Azul'}],
                                'tabela': {'PÓS': {'Preço': '1999,00', 'PIX e Vivo Pay': '1899,00'}},
                                'grupos': ['PÓS']})

r = roda('vendas_consultar', {'o_que': 'painel'}, ana)
t('sem AcessoVendas: recusa', 'não está liberado' in r and 'CLIENTE' not in r, r)
AcessoVendas.objects.create(user=ana, permissoes=['modulo.ver'])
User.objects.filter(pk=ana.pk).update()   # o acesso fica guardado na instância: recarrega
ana = User.objects.get(pk=ana.pk)
r = roda('vendas_consultar', {'o_que': 'painel'}, ana)
t('com "Ver módulo": painel só das vendas dela', '1 venda(s)' in r and 'R$ 1.500,00' in r and 'iPhone' not in r, r)
t('sem ranking da rede', 'Ranking por loja' not in r, r)
r = roda('vendas_consultar', {'o_que': 'lista', 'loja_id': norte.pk}, ana)
t('filtro de outra loja não abre venda dos outros', 'CLIENTE DO BETO' not in r and 'Vendas (0)' in r, r)
r = roda('vendas_consultar', {'o_que': 'venda', 'venda_id': vb.pk}, ana)
t('nem pelo número da venda', 'não encontrada' in r and 'CLIENTE DO BETO' not in r, r)
r = roda('vendas_consultar', {'o_que': 'venda', 'venda_id': va.pk}, ana)
t('detalhe da própria venda com SKU', f'Venda #{va.pk}' in r and 'SKU SKUZZ1' in r, r)
r = roda('vendas_consultar', {'o_que': 'precos', 'busca': 'galaxy'}, ana)
t('tabela de preços com SKUs e PIX', 'GALAXY ZZ 128GB' in r and 'SKUZZ2 (Azul)' in r and 'PIX R$ 1.899,00' in r, r)
item = ItemPreco.objects.get(nome='GALAXY ZZ 128GB')
r = roda('vendas_consultar', {'o_que': 'produto', 'item_id': item.pk}, ana)
t('produto com a matriz grupo × condição', 'PÓS: R$ 1.999,00 | R$ 1.899,00' in r, r)
t('clientes exigem "Consultar clientes"', 'não está liberada' in roda('vendas_consultar', {'o_que': 'clientes'}, ana))
r = roda('vendas_consultar', {'o_que': 'acessos'}, ana)
t('acessos dizem o que ele pode', '✓ Ver módulo' in r and '✗ Ver a rede inteira' in r, r)

AcessoVendas.objects.create(user=beto, permissoes=['rede.ver', 'clientes.ver'])
beto = User.objects.get(pk=beto.pk)
r = roda('vendas_consultar', {'o_que': 'painel'}, beto)
t('"Ver a rede inteira" vê todas, com ranking', '2 venda(s)' in r and 'Ranking por loja' in r, r)
r = roda('vendas_consultar', {'o_que': 'lista'}, beto)
t('e lista as duas', 'CLIENTE DA ANA' in r and 'CLIENTE DO BETO' in r, r)
r = roda('vendas_consultar', {'o_que': 'clientes'}, beto)
t('clientes liberados respondem', 'Clientes — recorte: a rede inteira' in r, r)
r = roda('vendas_consultar', {'o_que': 'painel'}, admin)
t('SUPERADMIN vê tudo', '2 venda(s)' in r, r)


# ─── RESULTADOS ──────────────────────────────────────────────────────────────
print('\n== RESULTADOS ==')
from power_bi.models import GoalEntry, GoalUpload  # noqa: E402

pedidos_parciais = []


def falso_parciais(path, params, espera_json=True):
    pedidos_parciais.append(path)
    if 'microindicadores-cn' in path:
        return {'success': True, 'periodo': '01/11 a 08/11', 'data': [
            {'cn': 'ANA LUCIA SOUZA', 'loja': 'CENTRO', 'movel_b2b': '2', 'vale_saude': '1'},
            {'cn': 'BETO NORTE', 'loja': 'NORTE', 'movel_b2b': '7', 'vale_saude': '3'}]}
    if 'microindicadores' in path:
        return {'data': [{'loja': 'NORTE', 'movel_b2b': '7'}, {'loja': 'CENTRO', 'movel_b2b': '2'}]}
    if 'meta-dia' in path:
        return {'scope_label': 'Rede', 'data': '09/11', 'atingidos': 1, 'total_pilares': 7,
                'linhas': [{'pilar': 'MÓVEL', 'realizado': '9.999', 'meta': '10.000', 'atingimento': '99%',
                            'faltando': '1', 'atingido': False}]}
    return {'ok': True}


mock.patch.object(ferramentas, '_parciais_get', falso_parciais).start()
REALIZADO = {'ANA LUCIA SOUZA': {'movel': 800.0, 'fixa': 300.0, 'fixa_qty': 3.0, 'seguros': 50.0}}


def falso_realizado(*, vendor='', **k):
    nome = ferramentas._nome_painel(vendor)
    base = dict(sql_realizado.EMPTY_RESULT)
    base.update(REALIZADO.get(nome, {}))
    return base


mock.patch.object(sql_realizado, 'get_realized_sales_from_mysql', falso_realizado).start()
up = GoalUpload.objects.create(year=hoje.year, month=hoje.month, source_file_name='ZZ.xlsx', uploaded_by=admin)
CN, PDV = GoalEntry.SHEET_CN_REAL, GoalEntry.SHEET_PDV_REAL
for sheet, loja, pessoa, pilar, valor in ((CN, 'LOJA ZZ CENTRO', 'ANA LUCIA SOUZA', 'MOVEL', '1000'),
                                          (CN, 'LOJA ZZ CENTRO', 'ANA LUCIA SOUZA', 'FIXA', '6'),
                                          (CN, 'LOJA ZZ NORTE', 'BETO NORTE', 'MOVEL', '4321'),
                                          (PDV, 'LOJA ZZ CENTRO', '', 'MOVEL', '98765')):
    GoalEntry.objects.create(upload=up, sheet_type=sheet, store_name=loja, user_name=pessoa, pilar=pilar,
                             goal_value=Decimal(valor))

r = roda('resultados_comerciais', {}, ana)
t('não-SUPERADMIN recebe só os próprios resultados', 'SEUS RESULTADOS' in r and 'movel b2b 2' in r, r)
t('com o realizado e a meta dele por pilar', 'Móvel: R$ 800,00 · meta R$ 1.000,00 · 80,0%' in r
  and 'Fixa: 3 venda(s)' in r and 'meta 6' in r, r)
t('sem nada de colega, loja ou rede', 'BETO' not in r.upper() and '4.321' not in r and '98.765' not in r
  and 'movel b2b 7' not in r, r)
r = roda('resultados_comerciais', {'aba': 'resultados', 'scope': 'rede'}, ana)
t('aba e scope não abrem a rede para ele', 'SEUS RESULTADOS' in r and 'BETO' not in r.upper()
  and not any('resultados-fragment' in p or 'meta-dia' in p for p in pedidos_parciais), r)
r = roda('resultados_comerciais', {'aba': 'microindicadores', 'incluir_cn': True}, gerente)
t('gerente também só vê o dele (não está na lista: diz isso)', 'não aparece' in r and 'BETO' not in r.upper()
  and 'ANA LUCIA' not in r.upper(), r)
r = roda('resultados_comerciais', {}, carla)
t('nome não encontrado é dito com clareza', 'não aparece na lista' in r and 'Não encontrei resultados' in r, r)

r = roda('resultados_comerciais', {}, admin)
t('SUPERADMIN tem o resumo da rede', 'RESULTADOS DO DIA' in r and 'MÓVEL' in r, r)
r = roda('resultados_comerciais', {'aba': 'microindicadores', 'incluir_cn': True}, admin)
t('e todos os consultores nos microindicadores', 'BETO NORTE' in r and 'ANA LUCIA SOUZA' in r, r)


# ─── SYSTEM ──────────────────────────────────────────────────────────────────
print('\n== PROMPT ==')
from assistente import claude_client  # noqa: E402

t('o SYSTEM fala das ferramentas novas', all(n in claude_client.SYSTEM for n in (
    'ponto_consultar', 'simulador_consultar', 'vendas_consultar', 'resultados_comerciais')))

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
