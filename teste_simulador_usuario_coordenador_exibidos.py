"""Simulador (17/11/2026): os cards "Usuário" e "Coordenador" em todas as visões.

- "Usuário" é sempre a pessoa do ?user_id= — o gerente operacional (a conta é a do gerente de
  vendas da loja) mostrava o nome do gerente de vendas.
- "Coordenador" é quem tem cargo COORDENADOR DE VENDAS, ativo, na loja da pessoa: setor principal
  → M2M + carteira → só carteira → só M2M; empate no grupo = todos, em ordem alfabética.
  Sem ninguém, fica a coordenação do cálculo (reserva). Coordenador e sniper não mudam.

Banco sqlite descartável (syncdb, sem migrations); planilha e MySQL dublados; rede bloqueada.
"""
import contextlib
import os
import pathlib
import re
import sys
import tempfile
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-sim-usr-coord-'))
(PASTA / 'zz_sim_usr_coord_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-suc'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-suc-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_sim_usr_coord_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import requests  # noqa: E402

from communications.models import CommunicationGroup  # noqa: E402
from simulator import services as sv  # noqa: E402
from simulator.models import CoordinatorStoreAccess  # noqa: E402
from users.models import Sector, User  # noqa: E402

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


requests.Session.request = sem_rede
requests.request = sem_rede

seq = [0]


def pessoa(nome, sobrenome, cargo='', setor=None, m2m=(), ativo=True, hierarquia='PADRAO'):
    seq[0] += 1
    u = User.objects.create_user(
        username=f'zz.suc{seq[0]}', email=f'zz.suc{seq[0]}@exemplo-teste.local', password='S3nha!teste',
        first_name=nome, last_name=sobrenome, job_title=cargo, sector=setor, is_active=ativo,
        hierarchy=hierarquia)
    if m2m:
        u.sectors.set(m2m)
    return u


def carteira(coord, lojas):
    acesso = CoordinatorStoreAccess.objects.create(coordinator=coord)
    acesso.sectors.set(lojas)
    return acesso


def nomes(lista):
    return [u.first_name for u in lista]


montserrat = Sector.objects.create(name='Loja ZZ Montserrat')
glória = Sector.objects.create(name='Loja ZZ Glória')
anchieta = Sector.objects.create(name='Loja ZZ Anchieta')
camburi = Sector.objects.create(name='Loja ZZ Camburi')
sp = Sector.objects.create(name='Loja ZZ Itaim')
comercial = Sector.objects.create(name='Comercial')
escritorio = Sector.objects.create(name='Escritório')

chefe = pessoa('ZZCHEFE', 'Super', setor=escritorio, hierarquia='SUPERADMIN')
# Coordenação: ninguém tem a loja como setor principal; o M2M do escritório tem quase tudo.
joao = pessoa('JOÃO', 'Carlos', 'COORDENADOR DE VENDAS', comercial, [montserrat, camburi])
thay = pessoa('THAYANDRA', 'Toffoli', 'Coordenador  de vendas', comercial, [montserrat, camburi, anchieta])
igor = pessoa('IGOR', 'Lins', 'COORDENADOR DE VENDAS', escritorio, [montserrat, glória, anchieta, camburi])
luiz = pessoa('LUIZ', 'Ribeiro', 'COORDENADOR DE VENDAS', comercial, [glória])
pedro = pessoa('PEDRO', 'Correia', 'COORDENADOR DE VENDAS II', glória, [])
pessoa('ZZINATIVO', 'Coord', 'COORDENADOR DE VENDAS', montserrat, [montserrat], ativo=False)
pessoa('ZZMELHORIA', 'Coord', 'COORDENADOR DE MELHORIA E INOVAÇÃO', montserrat, [montserrat])
vinicius = pessoa('VINICIUS', 'Sniper', 'SNIPER II', comercial, [])
carteira(vinicius, [montserrat, camburi])     # carteira de sniper: não é cargo de coordenador
carteira(joao, [montserrat])
carteira(luiz, [anchieta])
carteira(thay, [sp])

bruna = pessoa('BRUNA', 'Negris', 'GERENTE OPERACIONAL', montserrat)
ariel = pessoa('ARIEL', 'Santos', 'GERENTE DE VENDAS', montserrat)
ana = pessoa('ANA', 'Consultora', 'CONSULTOR DE VENDAS', montserrat)
caio = pessoa('CAIO', 'Consultor', 'CONSULTOR DE VENDAS', sp)
duda = pessoa('DUDA', 'SemLoja', 'CONSULTOR DE VENDAS', comercial)
grupo_coord = CommunicationGroup.objects.create(name='COORDENADORES', created_by=chefe)
grupo_coord.members.add(joao)

print('== QUEM É O COORDENADOR DA LOJA ==')
t('setor principal ganha de M2M e carteira (Glória → PEDRO, cargo com nível II)',
  nomes(sv.coordenadores_de_vendas_da_loja(glória)) == ['PEDRO'], nomes(sv.coordenadores_de_vendas_da_loja(glória)))
t('sem setor principal: M2M + carteira (Montserrat → JOÃO; inativo, outro cargo e sniper ficam fora)',
  nomes(sv.coordenadores_de_vendas_da_loja(montserrat)) == ['JOÃO'], nomes(sv.coordenadores_de_vendas_da_loja(montserrat)))
t('só carteira ganha de só M2M (Anchieta → LUIZ, não IGOR/THAYANDRA)',
  nomes(sv.coordenadores_de_vendas_da_loja(anchieta)) == ['LUIZ'], nomes(sv.coordenadores_de_vendas_da_loja(anchieta)))
t('só M2M, mais de um: todos em ordem alfabética (Camburi → IGOR, JOÃO, THAYANDRA)',
  nomes(sv.coordenadores_de_vendas_da_loja(camburi)) == ['IGOR', 'JOÃO', 'THAYANDRA'],
  nomes(sv.coordenadores_de_vendas_da_loja(camburi)))
t('carteira de coordenador sem M2M vale (Itaim → THAYANDRA)',
  nomes(sv.coordenadores_de_vendas_da_loja(sp)) == ['THAYANDRA'], nomes(sv.coordenadores_de_vendas_da_loja(sp)))
t('setor que não é loja não tem coordenador (Comercial)', sv.coordenadores_de_vendas_da_loja(comercial) == [])
t('sem setor: vazio', sv.coordenadores_de_vendas_da_loja(None) == [])
t('para exibir: primeiro nome e nome completo',
  sv.coordenador_para_exibir(ana) == ('JOÃO', 'JOÃO Carlos'), sv.coordenador_para_exibir(ana))
t('para exibir com empate: separados por vírgula',
  sv.coordenador_para_exibir(pessoa('EVA', 'Camburi', 'CONSULTOR DE VENDAS', camburi))[0] == 'IGOR, JOÃO, THAYANDRA')

print('\n== GERENTE OPERACIONAL: O NOME É O DELA ==')


def base_falsa(user, *a, **k):
    # O cálculo de verdade usa o gerente de vendas como base — o nome que volta é o dele.
    return {'user_name': user.get_full_name(), 'first_name': user.first_name, 'pdv': 'MONTSERRAT',
            'coordinator': 'VINICIUS', 'view_mode': a[2] if len(a) > 2 else '', 'rows': [],
            'totals': {'ganho_total': 1000.0}}


with mock.patch.object(sv, '_compute_gerente_simulation_base', side_effect=base_falsa) as base:
    s = sv.compute_gerente_simulation(bruna, {}, {}, view_mode=sv.VIEW_PROJECAO, simulator_inputs={})
t('a conta usa o gerente de vendas da loja', base.call_args[0][0] == ariel, base.call_args)
t('mas user_name/first_name são da gerente operacional',
  (s.get('user_name'), s.get('first_name')) == ('BRUNA Negris', 'BRUNA'), (s.get('user_name'), s.get('first_name')))
t('e a base continua informada', 'ARIEL' in (s.get('gerente_de_vendas_base') or ''))

print('\n== A TELA, EM TODAS AS VISÕES ==')
cliente = Client()
cliente.force_login(chefe)


def card(html, rotulo):
    m = re.search(rf'>{rotulo}</p>\s*<p([^>]*)>([^<]*)</p>', html)
    if not m:
        return None, None
    titulo = re.search(r'title="([^"]*)"', m.group(1))
    return m.group(2).strip(), titulo.group(1) if titulo else ''


def falso_consultor(user, *a, **k):
    # Simulação que veio com o nome de outra pessoa e a coordenação do cálculo (carteira).
    return {'user_name': 'OUTRA PESSOA', 'first_name': 'OUTRA', 'pdv': 'X', 'coordinator': 'LUIZ',
            'rows': [], 'totals': {}}


def falso_coordenador(user, *a, **k):
    return {'user_name': user.get_full_name(), 'first_name': user.first_name, 'coordinator': 'JOÃO',
            'coord_pdvs': [], 'carteira': True, 'rows': [], 'is_sniper': False, 'totals': {}}


def tela(alvo, visao):
    with mock.patch('simulator.views.realized_prefetch', lambda *a, **k: contextlib.nullcontext()), \
            mock.patch.object(sv, '_compute_gerente_simulation_base', side_effect=base_falsa), \
            mock.patch('simulator.views.compute_consultor_simulation', side_effect=falso_consultor), \
            mock.patch('simulator.views.compute_coordenador_simulation', side_effect=falso_coordenador), \
            mock.patch('simulator.views.get_business_days_info', return_value=(10, 20)):
        r = cliente.get(f'/simulator/?user_id={alvo.id}&view={visao}&fragment=results')
    return r.status_code, r.content.decode()


for visao in (sv.VIEW_PROJECAO, sv.VIEW_REALIZADO, sv.VIEW_SIMULADOR):
    st, html = tela(bruna, visao)
    t(f'[{visao}] gerente operacional abre (200)', st == 200, st)
    t(f'[{visao}] Usuário = BRUNA (não o gerente de vendas)', card(html, 'Usuário')[0] == 'BRUNA', card(html, 'Usuário'))
    t(f'[{visao}] Coordenador = JOÃO (cargo na loja), não VINICIUS (carteira do sniper)',
      card(html, 'Coordenador') == ('JOÃO', 'JOÃO Carlos'), card(html, 'Coordenador'))

    st, html = tela(ana, visao)
    t(f'[{visao}] consultora: Usuário = ANA, mesmo com a simulação trazendo outro nome',
      card(html, 'Usuário')[0] == 'ANA', card(html, 'Usuário'))
    t(f'[{visao}] consultora: Coordenador = JOÃO', card(html, 'Coordenador')[0] == 'JOÃO', card(html, 'Coordenador'))

    st, html = tela(duda, visao)
    t(f'[{visao}] sem loja: Coordenador cai na reserva (coordenação do cálculo)',
      card(html, 'Coordenador')[0] == 'LUIZ', card(html, 'Coordenador'))

    st, html = tela(joao, visao)
    t(f'[{visao}] coordenador selecionado: segue mostrando a própria coordenação',
      card(html, 'Usuário')[0] == 'JOÃO' and card(html, 'Coordenador')[0] == 'JOÃO',
      (card(html, 'Usuário'), card(html, 'Coordenador')))

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
