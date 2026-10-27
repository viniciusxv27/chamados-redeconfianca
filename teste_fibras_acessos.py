"""/fibras (27/10/2026): menu para o SUPERADMIN e acesso parte por parte em /fibras/acessos/.

Pedido: "Libere no Menu para SUPERADMIN; permita superadmin selecionar quem poderá mexer em
cada detalhe para acesso do usuario, e aparecer no menu também".

Cobre:
- sem liberação, ninguém entra (nem vê o item do menu); o SUPERADMIN entra e vê "Acessos";
- a tela de acessos lista todo mundo, só o SUPERADMIN mexe, e grava histórico;
- cada função libera a sua parte: ver (só as vendas dela / da loja / da rede), mudar status,
  importar, exportar, validação, tratativa — e o botão some de quem não tem;
- função leva junto o que ela exige; acesso suspenso não entra;
- a ficha de uma venda que a pessoa não enxerga dá 404.

Roda num sqlite descartável; nada sai para a rede.
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

# Banco e arquivos descartáveis: a tabela das entregas e a coluna
# `exige_entrega` só existem no Postgres compartilhado depois do migrate, e o
# migrate é dele. O schema aqui sai direto dos models (`--run-syncdb`), como em
# qualquer outra sessão — nada toca o banco de verdade nem o MinIO.
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-fibras-acessos-'))
(PASTA / 'zz_fibras_acessos_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fba'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fba-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_fibras_acessos_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

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


from unittest import mock as _mock

import requests
from django.utils import timezone

from fibras.models import AcessoFibras, Fibra, HistoricoAcessoFibras
from fibras.permissoes import completar


def sem_rede(*a, **k):
    raise AssertionError('teste tentou sair para a rede')


_mock.patch.object(requests, 'post', sem_rede).start()
_mock.patch.object(requests, 'get', sem_rede).start()

loja = Sector.objects.create(name='ZZ LOJA FIBRA')
outra = Sector.objects.create(name='ZZ OUTRA LOJA')
chefe = User.objects.create_user(username='zzf.super', email='zzf.super@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZF', last_name='Super', sector=loja, hierarchy='SUPERADMIN')
vend = User.objects.create_user(username='zzf.vend', email='zzf.vend@exemplo-teste.local', password='S3nha!teste',
                                first_name='Zé', last_name='Vendedor', sector=loja, hierarchy='PADRAO')
sup = User.objects.create_user(username='zzf.sup', email='zzf.sup@exemplo-teste.local', password='S3nha!teste',
                               first_name='ZZF', last_name='Supervisor', sector=outra, hierarchy='SUPERVISOR')


def fibra(venda, pdv, vendedor):
    return Fibra.objects.create(numero_da_venda=venda, numero_protocolo='71' + venda[-4:], pdv=pdv, vendedor=vendedor,
                                data_da_venda=timezone.localdate(), cliente=f'Cliente {venda}', pilar='FIXA',
                                valor=100, status=Fibra.STATUS_PENDENTE)


f_dele = fibra('ZZF0001', 'ZZ LOJA FIBRA', 'ZE VENDEDOR')          # sem acento/caixa: é o Zé
f_loja = fibra('ZZF0002', 'ZZ LOJA FIBRA', 'OUTRA PESSOA')
f_fora = fibra('ZZF0003', 'ZZ OUTRA LOJA', 'MAIS ALGUEM')

cs, cv, cp = Client(), Client(), Client()
cs.force_login(chefe)
cv.force_login(vend)
cp.force_login(sup)
MENU = 'href="/fibras/" data-rota="/fibras/"'

print('Sem liberação')
r = Client().get('/fibras/')
t('anônimo vai para o login', r.status_code == 302 and 'login' in r['Location'], r.get('Location'))
r = cp.get('/fibras/')
t('supervisor sem liberação não entra (antes a hierarquia bastava)', r.status_code == 302 and '/fibras' not in r['Location'],
  r.get('Location'))
t('e não vê Fibras no menu', MENU not in cp.get('/').content.decode())
t('não abre a tela de acessos', cp.get('/fibras/acessos/').status_code == 302)
r = cv.post(f'/fibras/{f_dele.pk}/status/ajax/', {'status': 'agendado'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
t('mudar status por fetch sem liberação: 403 em JSON', r.status_code == 403 and r.json()['ok'] is False, r.status_code)

print('SUPERADMIN')
h = cs.get('/').content.decode()
t('vê Fibras no menu', MENU in h)
r = cs.get('/fibras/?periodo=todos')
h = r.content.decode()
t('entra no kanban e vê a rede', r.status_code == 200 and all(v in h for v in ('ZZF0001', 'ZZF0002', 'ZZF0003')))
t('vê a aba Acessos, importar, exportar e validação', all(x in h for x in ('/fibras/acessos/', 'fbImportForm',
                                                                         'export/canceladas-csv', '/fibras/validacao/')))
t('botões Cruzar e Importar no cabeçalho', 'id="fbValCruzar"' in h and 'id="fbImportar"' in h)
hoje = timezone.localdate()
t('Cruzar usa o mês do quadro', f'data-ini="{hoje.replace(day=1).isoformat()}"' in cs.get('/fibras/').content.decode())
t('sem Esteira importada o Cruzar fica desligado', 'title="Importe a Esteira primeiro"' in h)
r = cs.get('/fibras/acessos/')
h = r.content.decode()
t('tela de acessos lista todos os usuários', r.status_code == 200 and 'Zé Vendedor' in h and 'ZZF Supervisor' in h)

print('Liberar só "Ver módulo"')
r = cs.post('/fibras/acessos/', {'user': vend.pk, 'permissoes': ['modulo.ver'], 'ativo': 'on', 'observacao': 'teste'})
a = AcessoFibras.objects.get(user=vend)
t('grava a liberação', r.status_code == 302 and a.permissoes == ['modulo.ver'] and a.concedido_por == chefe)
t('grava o histórico', HistoricoAcessoFibras.objects.filter(pessoa=vend, antes='(sem acesso)', depois='Ver módulo').exists())
t('passa a ver Fibras no menu', MENU in cv.get('/').content.decode())
h = cv.get('/fibras/?periodo=todos').content.decode()
t('vê só a venda dela (nome sem acento e caixa)', 'ZZF0001' in h and 'ZZF0002' not in h and 'ZZF0003' not in h)
t('sem os botões que não tem', not any(x in h for x in ('id="fbImportForm"', 'export/canceladas-csv', '/fibras/validacao/',
                                                         '/fibras/acessos/', 'Sortable.min.js')))
t('sem Cruzar nem Importar', 'id="fbValCruzar"' not in h and 'id="fbImportar"' not in h)
t('abre a ficha da venda dela', cv.get(f'/fibras/{f_dele.pk}/').status_code == 200)
t('ficha de venda de outro: 404', cv.get(f'/fibras/{f_loja.pk}/').status_code == 404)
h = cv.get(f'/fibras/{f_dele.pk}/').content.decode()
t('ficha sem mudar status nem tratativa', 'id="fbFormStatus"' not in h and 'id="fbFormIncidente"' not in h)
r = cv.get('/fibras/export/excel/')
t('exportar barrado', r.status_code == 302)
t('validação barrada', cv.get('/fibras/validacao/').status_code == 302)
r = cv.post('/fibras/validacao/api/cruzar/', data='{}', content_type='application/json')
t('API da validação barrada (JSON)', r.status_code == 403, r.status_code)
r = cv.post('/fibras/acessos/', {'user': vend.pk, 'permissoes': list(completar(['rede.ver']))})
t('quem não é SUPERADMIN não se libera', AcessoFibras.objects.get(user=vend).permissoes == ['modulo.ver'])

print('Loja, status, tratativa e exportar')
cs.post('/fibras/acessos/', {'user': vend.pk, 'permissoes': ['loja.ver', 'status.alterar', 'tratativa.abrir', 'exportar'],
                             'ativo': 'on'})
a.refresh_from_db()
t('função leva "Ver módulo" junto', 'modulo.ver' in a.permissoes, a.permissoes)
h = cv.get('/fibras/?periodo=todos').content.decode()
t('"Ver a loja" mostra a loja dela, não a outra', 'ZZF0002' in h and 'ZZF0003' not in h)
t('com mudar status o kanban arrasta', 'Sortable.min.js' in h)
r = cv.post(f'/fibras/{f_loja.pk}/status/ajax/', {'status': 'agendado'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
t('muda status de venda da loja', r.status_code == 200 and r.json()['status'] == 'agendado', r.content[:200])
r = cv.post(f'/fibras/{f_fora.pk}/status/ajax/', {'status': 'agendado'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
t('não muda status de venda que não enxerga', r.status_code == 404, r.status_code)
h = cv.get(f'/fibras/{f_dele.pk}/').content.decode()
t('ficha com mudar status e tratativa', 'id="fbFormStatus"' in h and 'id="fbFormIncidente"' in h)
t('exporta', cv.get('/fibras/export/canceladas-csv/').status_code == 200)
t('ainda sem validação', cv.get('/fibras/validacao/').status_code == 302)

print('Validação leva a rede')
t('completar: validação exige rede e módulo', completar(['validacao.usar']) == ['modulo.ver', 'rede.ver', 'validacao.usar'])
cs.post('/fibras/acessos/', {'user': sup.pk, 'permissoes': ['validacao.usar'], 'ativo': 'on'})
t('supervisor liberado abre a validação', cp.get('/fibras/validacao/').status_code == 200)
h = cp.get('/fibras/?periodo=todos').content.decode()
t('e vê a rede inteira', all(v in h for v in ('ZZF0001', 'ZZF0002', 'ZZF0003')))

print('Suspender e tirar')
cs.post('/fibras/acessos/', {'user': sup.pk, 'permissoes': ['validacao.usar'], 'ativo': 'off'})
t('suspenso não entra', cp.get('/fibras/').status_code == 302)
t('histórico marca suspenso', HistoricoAcessoFibras.objects.filter(pessoa=sup, depois__endswith='(suspenso)').exists())
cs.post('/fibras/acessos/', {'user': vend.pk, 'remover': '1'})
t('tirar acesso apaga a liberação', not AcessoFibras.objects.filter(user=vend).exists())
t('e some do menu', MENU not in cv.get('/').content.decode())
r = cs.post('/fibras/acessos/', {'user': chefe.pk, 'permissoes': ['modulo.ver']}, follow=True)
t('SUPERADMIN não precisa de liberação', not AcessoFibras.objects.filter(user=chefe).exists())

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
