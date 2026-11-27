"""/fibras/lista: o que se muda à mão fica salvo e sobrevive à atualização da base (19/11/2026).

Pedido: "Salve sempre que mudar o status e essas coisas, está mudando sempre que atualiza a base".

O que este teste cobre:

- status e agendamento mudados na lista vão para o servidor e marcam "à mão";
- a importação da Esteira e a sincronização do Vivo GO não passam por cima de nenhum dos dois,
  e continuam atualizando quem ninguém mexeu;
- corrida: a pessoa muda o status/agendamento ENQUANTO a base atualiza (a importação já tinha
  carregado as vendas) e a mudança dela fica;
- o detalhe grava o retorno apagado e "Voltar a seguir a Esteira" vale por campo;
- a migração 0008 marca quem já mudou o agendamento na lista e devolve a data que a base apagou.

Roda num sqlite descartável (as colunas novas só existem no Postgres compartilhado depois do
migrate, que é dele). Nada toca o banco de verdade, o MySQL do Vivo GO, o Redis nem o MinIO.
"""
import importlib
import io
import os
import pathlib
import sys
import tempfile
from datetime import date, timedelta
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-fibras-persist-'))
(PASTA / 'zz_fibras_persist_settings.py').write_text(f"""
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
CACHES = {{n: {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fp-' + n}}
          for n in CACHES}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_fibras_persist_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

import pandas as pd
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment
from django.utils import timezone

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from fibras import services
from fibras.models import EsteiraVenda, Fibra, FibraStatusHistory
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


def planilha(linhas):
    """ANALÍTICO DE VENDAS com uma linha de título antes do cabeçalho."""
    cols = ['DATA_CRIAÇÃO', 'ETAPA INSTALACAO', 'STATUS INSTALACAO', 'ORDEM', 'PRODUTO', 'ADABAS', 'LOGRADOURO',
            'CEP', 'NUMERO', 'LOGIN', 'NOME VENDEDOR', 'VENDA COMISSIONADA', 'SLA AGENDA', 'MOTIVO', 'DATA_AGENDA']
    df = pd.DataFrame(linhas, columns=cols)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        pd.DataFrame([['RELATÓRIO ANALÍTICO DE VENDAS']]).to_excel(w, index=False, header=False, startrow=0)
        df.to_excel(w, index=False, startrow=1)
    return buf.getvalue()


def linha(ordem, status, agenda=''):
    return ['07/04/2031', 'AGENDADA', status, ordem, 'BANDA LARGA', 'ZZS001', '', '', '', 'zz', 'ZZ VENDEDOR UM',
            'SIM', '', '', agenda]


Sector.objects.create(name='ZZ LOJA PERSIST', adabas='ZZS001')
ilha = User.objects.create_user(username='zzps.ilha', email='zzps.ilha@exemplo-teste.local', password='S3nha!teste',
                                first_name='ZZ', last_name='Ilha Persist', hierarchy='SUPERADMIN')
D = date(2031, 4, 7)
AJ = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}


def fibra(venda, prot, status=Fibra.STATUS_PENDENTE, agenda=None):
    return Fibra.objects.create(numero_da_venda=venda, numero_protocolo=prot, pdv='ZZ LOJA PERSIST', data_da_venda=D,
                                cpf='090.260.267-57', cliente=f'Cliente {venda}', vendedor='ZZ VENDEDOR UM',
                                pilar='FIXA', servico_tecnico='Alta Banda Larga', valor=100, status=status,
                                data_agendamento=agenda)


def linha_go(f, **extra):
    base = {'numero_da_venda': f.numero_da_venda, 'numero_protocolo': f.numero_protocolo, 'cpf': f.cpf,
            'cliente': f.cliente, 'pdv': f.pdv, 'vendedor': f.vendedor, 'data_da_venda': D, 'pilar': 'FIXA',
            'servico_tecnico': 'Alta Banda Larga', 'valor': 100, 'venda_ativa': '1'}
    base.update(extra)
    return base


fm = fibra('ZZS1', '7411111111')                      # mexida à mão na lista
fa = fibra('ZZS2', '7422222222')                      # ninguém mexe: segue a base
fr = fibra('ZZS3', '7433333333')                      # mexida durante a importação (corrida)
fs = fibra('ZZS4', '7444444444', status=Fibra.STATUS_AGENDADO)   # mexida durante a sincronização (corrida)

ci = Client()
ci.force_login(ilha)


def importar(*linhas):
    with mock.patch.object(services, 'sync_fibras', return_value={'created': 0, 'updated': 0, 'total_in_source': 0}):
        return ci.post('/fibras/validacao/api/importar/',
                       {'arquivo': SimpleUploadedFile('ANALITICO DE VENDAS.xlsx', planilha(list(linhas)))})


def recarregar(*fs_):
    for x in fs_:
        x.refresh_from_db()


print('== A BASE CHEGA PRIMEIRO (ninguém mexeu ainda) ==')
r = importar(linha(7411111111, '3.5 AGENDADA', '09/04/2031'), linha(7422222222, '3.5 AGENDADA', '09/04/2031'),
             linha(7433333333, '3.5 AGENDADA', '09/04/2031'))
recarregar(fm, fa, fr)
t('importação roda', r.status_code == 200, r.content[:300])
t('sem mão, a base manda no status e no agendamento', fm.status == Fibra.STATUS_AGENDADO
  and fm.data_agendamento == date(2031, 4, 9) and not fm.status_manual and not fm.agendamento_manual,
  (fm.status, fm.data_agendamento))

print('\n== MUDAR NA LISTA GRAVA NO SERVIDOR ==')
r = ci.post(f'/fibras/{fm.pk}/status/ajax/', {'status': Fibra.STATUS_PROBLEMA}, **AJ)
recarregar(fm)
t('status gravado e marcado "à mão"', r.status_code == 200 and r.json()['ok'] and r.json().get('status_manual') is True
  and fm.status == Fibra.STATUS_PROBLEMA and fm.status_manual and fm.status_manual_por == ilha, r.content[:200])
r = ci.post(f'/fibras/{fm.pk}/agendamento/ajax/', {'data_agendamento': '2031-04-20'}, **AJ)
recarregar(fm)
t('agendamento gravado e marcado "à mão"', r.status_code == 200 and r.json().get('agendamento_manual') is True
  and fm.data_agendamento == date(2031, 4, 20) and fm.agendamento_manual and fm.agendamento_manual_por == ilha
  and fm.agendamento_manual_em is not None, r.content[:200])
t('fica no histórico', FibraStatusHistory.objects.filter(fibra=fm, retorno__startswith='Agendamento alterado').exists())
lista = ci.get('/fibras/?de=2031-04-01&ate=2031-04-30').content.decode()
t('lista mostra o selo "manual" no agendamento', 'data-agenda-manual' in lista)
t('lista põe o selo na hora, sem recarregar (JS)', 'fbSeloManual' in lista)

print('\n== A BASE ATUALIZA DE NOVO: O QUE FOI MEXIDO À MÃO FICA ==')
esteira_antes = FibraStatusHistory.objects.filter(fibra=fm, retorno__startswith='Esteira Vivo:').count()
r = importar(linha(7411111111, '1.1 INSTALADA', '11/04/2031'), linha(7422222222, '1.1 INSTALADA', '11/04/2031'),
             linha(7433333333, '3.5 AGENDADA', '09/04/2031'))
recarregar(fm, fa)
t('status à mão ficou', fm.status == Fibra.STATUS_PROBLEMA and fm.status_manual, fm.status)
t('agendamento à mão ficou', fm.data_agendamento == date(2031, 4, 20) and fm.agendamento_manual, fm.data_agendamento)
t('o resto da Esteira atualiza (o que ela diz fica guardado)', fm.status_planilha_raw == '1.1 INSTALADA'
  and EsteiraVenda.objects.get(ordem='7411111111').data_agenda == date(2031, 4, 11))
t('quem ninguém mexeu segue a base', fa.status == Fibra.STATUS_INSTALADO and fa.data_agendamento == date(2031, 4, 11),
  (fa.status, fa.data_agendamento))
t('nenhum "Esteira Vivo" novo no histórico da venda mexida', FibraStatusHistory.objects.filter(
    fibra=fm, retorno__startswith='Esteira Vivo:').count() == esteira_antes)

print('\n== CORRIDA: MUDA NA LISTA ENQUANTO A IMPORTAÇÃO RODA ==')
original = services._map_planilha_status
disparou = []


def durante_importacao(bruto):
    # A importação já carregou as vendas na memória; aqui a pessoa salva na lista (outra requisição).
    if not disparou:
        disparou.append(1)
        Fibra.objects.filter(pk=fr.pk).update(status=Fibra.STATUS_INSTALADO, status_manual=True,
                                              data_agendamento=date(2031, 5, 2), agendamento_manual=True)
    return original(bruto)


with mock.patch.object(services, '_map_planilha_status', durante_importacao):
    importar(linha(7411111111, '1.1 INSTALADA', '11/04/2031'), linha(7422222222, '1.1 INSTALADA', '11/04/2031'),
             linha(7433333333, '3.2 PENDENTE AGENDAMENTO', '12/04/2031'))
recarregar(fr)
t('a simulação disparou no meio da importação', bool(disparou))
t('status salvo no meio da importação não é desfeito', fr.status == Fibra.STATUS_INSTALADO, fr.status)
t('agendamento salvo no meio da importação não é desfeito', fr.data_agendamento == date(2031, 5, 2), fr.data_agendamento)
t('e não entra histórico "Esteira Vivo" falso', not FibraStatusHistory.objects.filter(
    fibra=fr, retorno__startswith='Esteira Vivo: 3.2').exists())

print('\n== SINCRONIZAÇÃO DO VIVO GO (MySQL) ==')
Fibra.objects.filter(pk=fa.pk).update(status=Fibra.STATUS_PENDENTE)
campos_orig = services._campos_do_mysql
mexeu = []


def durante_sync(r):
    if r.get('numero_da_venda') == 'ZZS4' and not mexeu:
        mexeu.append(1)
        Fibra.objects.filter(pk=fs.pk).update(status=Fibra.STATUS_PROBLEMA, status_manual=True)
    return campos_orig(r)


fonte = [linha_go(fm, valor=150, venda_ativa='0'), linha_go(fa, venda_ativa='0'), linha_go(fs, valor=175)]
with mock.patch.object(services, 'fetch_fibras_from_mysql', return_value=fonte), \
        mock.patch.object(services, '_campos_do_mysql', durante_sync):
    services.sync_fibras(year=2031, month=4)
recarregar(fm, fa, fs)
t('venda à mão: valor atualiza, status e agendamento ficam', fm.valor == 150 and fm.status == Fibra.STATUS_PROBLEMA
  and fm.data_agendamento == date(2031, 4, 20), (fm.valor, fm.status, fm.data_agendamento))
t('cancelada no Vivo GO e sem mão: vira Cancelado', fa.status == Fibra.STATUS_CANCELADO, fa.status)
t('corrida na sincronização: status salvo no meio fica (e o valor atualiza)', bool(mexeu)
  and fs.status == Fibra.STATUS_PROBLEMA and fs.valor == 175, (fs.status, fs.valor))

print('\n== DETALHE ==')
Fibra.objects.filter(pk=fa.pk).update(retorno_myrella='texto antigo')
r = ci.post(f'/fibras/{fa.pk}/status/', {'status': Fibra.STATUS_PENDENTE, 'retorno': ''})
recarregar(fa)
t('apagar o retorno no detalhe grava vazio', r.status_code == 302 and fa.retorno_myrella == '' and fa.status_manual,
  repr(fa.retorno_myrella))
ci.post(f'/fibras/{fa.pk}/status/', {'status': Fibra.STATUS_PROBLEMA, 'retorno': 'aguardando técnico'})
recarregar(fa)
t('retorno novo grava', fa.retorno_myrella == 'aguardando técnico' and fa.status == Fibra.STATUS_PROBLEMA)
ci.post(f'/fibras/{fa.pk}/status/ajax/', {'status': Fibra.STATUS_AGENDADO}, **AJ)
recarregar(fa)
t('mudar pela lista não apaga o retorno', fa.retorno_myrella == 'aguardando técnico', repr(fa.retorno_myrella))
det = ci.get(f'/fibras/{fm.pk}/').content.decode()
t('detalhe avisa do agendamento à mão e do que a Esteira diz', 'data-agendamento-manual' in det and '11/04/2031' in det)

print('\n== VOLTAR A SEGUIR A ESTEIRA, POR CAMPO ==')
r = ci.post(f'/fibras/{fm.pk}/seguir-esteira/', {'campo': 'agendamento'})
recarregar(fm)
t('só o agendamento volta para a Esteira', r.status_code == 302 and not fm.agendamento_manual
  and fm.data_agendamento == date(2031, 4, 11) and fm.status_manual and fm.status == Fibra.STATUS_PROBLEMA,
  (fm.agendamento_manual, fm.data_agendamento, fm.status))
r = ci.post(f'/fibras/{fm.pk}/seguir-esteira/', {'campo': 'status'})
recarregar(fm)
t('depois o status', not fm.status_manual and fm.status == Fibra.STATUS_INSTALADO, fm.status)
ci.post(f'/fibras/{fr.pk}/seguir-esteira/')
recarregar(fr)
t('sem campo: volta tudo', not fr.status_manual and not fr.agendamento_manual and fr.data_agendamento == date(2031, 4, 12)
  and fr.status == Fibra.STATUS_PENDENTE, (fr.status, fr.data_agendamento))

print('\n== MIGRAÇÃO 0008: QUEM JÁ MUDOU O AGENDAMENTO NA LISTA ==')
mig = importlib.import_module('fibras.migrations.0008_agendamento_manual')
g1 = fibra('ZZS5', '7455555555', agenda=date(2031, 4, 1))   # base apagou a data da pessoa
g2 = fibra('ZZS6', '7466666666', agenda=date(2031, 4, 3))   # pessoa limpou a data
g3 = fibra('ZZS7', '7477777777', agenda=date(2031, 4, 9))   # ninguém mexeu
Fibra.objects.filter(pk__in=[g1.pk, g2.pk, g3.pk]).update(agendamento_manual=False)
H = FibraStatusHistory.objects.create
h = H(fibra=g1, status_anterior='pendente', status_novo='pendente', alterado_por=ilha,
      retorno='Agendamento alterado na lista: sem data para 05/04/2031')
h2 = H(fibra=g1, status_anterior='pendente', status_novo='pendente', alterado_por=ilha,
       retorno='Agendamento alterado na lista: 05/04/2031 para 24/04/2031')
FibraStatusHistory.objects.filter(pk=h.pk).update(alterado_em=timezone.now() - timedelta(days=2))
H(fibra=g2, status_anterior='pendente', status_novo='pendente', alterado_por=ilha,
  retorno='Agendamento alterado na lista: 18/04/2031 para sem data')
mig.marcar_agendamentos_manuais(django_apps, None)
recarregar(g1, g2, g3)
t('marca e devolve a última data que a pessoa escolheu', g1.agendamento_manual and g1.data_agendamento == date(2031, 4, 24)
  and g1.agendamento_manual_por_id == ilha.pk, (g1.agendamento_manual, g1.data_agendamento))
t('"sem data" também é escolha da pessoa', g2.agendamento_manual and g2.data_agendamento is None, g2.data_agendamento)
t('quem não mexeu fica como está', not g3.agendamento_manual and g3.data_agendamento == date(2031, 4, 9))
mig.marcar_agendamentos_manuais(django_apps, None)
recarregar(g1)
t('idempotente', g1.agendamento_manual and g1.data_agendamento == date(2031, 4, 24))

import shutil  # noqa: E402

shutil.rmtree(PASTA, ignore_errors=True)
print(f'\n{ok} OK / {fail} falhas — sqlite descartável (apagado).')
sys.exit(1 if fail else 0)
