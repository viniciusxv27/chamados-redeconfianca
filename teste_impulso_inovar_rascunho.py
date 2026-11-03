"""/impulso/inovar/ (03/11/2026): o lote não pode travar nem perder decisão.

O envio morria no navegador: ideia aprovada traz prazo antigo num campo escondido com min=hoje, e o
formulário inteiro ficava inválido, sem aviso. Agora o form é novalidate, o servidor confere, e cada
escolha fica guardada no banco até ser aplicada.

Roda num sqlite descartável.
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-inovar-rasc-'))
(PASTA / 'zz_inovar_rasc_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-irasc'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-irasc-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_inovar_rasc_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

import datetime as dt
import json
import re

import requests
from django.utils import timezone

from impulso import views as impulso_views
from impulso.models import Ideia, Meta, RascunhoDecisaoIdeia
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
    raise AssertionError('teste tentou sair para a rede')


for nome in ('get', 'post', 'request'):
    mock.patch.object(requests, nome, sem_rede).start()

setor = Sector.objects.create(name='ZZ Setor')


def usuario(nome, hierarquia='PADRAO'):
    u = User.objects.create_user(username=f'zzi.{nome}', email=f'zzi.{nome}@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZI', last_name=nome, sector=setor, hierarchy=hierarquia,
                                 is_superuser=hierarquia == 'SUPERADMIN')
    c = Client(); c.force_login(u)
    return u, c


chefe, cs = usuario('chefe', 'SUPERADMIN')
outro, co = usuario('outro', 'SUPERADMIN')
autor, ca = usuario('autor')
from communications.models import CommunicationGroup
adm_grupo, _ = CommunicationGroup.objects.get_or_create(name='ESCRITÓRIO (ADM)', defaults={'created_by': chefe})
execu, _c = usuario('executor')
for u in (chefe, outro, autor, execu):
    u.communication_groups.add(adm_grupo)
hoje = timezone.localdate()
futuro = hoje + dt.timedelta(days=10)


def ideia(texto, **extra):
    campos = {f.name: 'x' for f in Ideia._meta.concrete_fields
              if f.get_internal_type() in ('CharField', 'TextField') and not f.blank and not f.choices and f.name != 'status'}
    campos.update(autor=autor, setor_impacto=texto, **extra)
    return Ideia.objects.create(**campos)


a1, a2, a3, a4 = ideia('ZZ um'), ideia('ZZ dois'), ideia('ZZ tres'), ideia('ZZ quatro')
velha = ideia('ZZ aprovada antiga', status='APROVADA', executor=execu, prazo=hoje - dt.timedelta(days=30))
URL, R, L = '/impulso/inovar/', '/impulso/inovar/rascunho/', '/impulso/inovar/decidir-lote/'


def guardar(cliente=cs, **d):
    return cliente.post(R, json.dumps(d), content_type='application/json')


print('A causa')
h = cs.get(URL).content.decode()
form = re.search(r'<form[^>]*id="formLote"[^>]*>', h).group(0)
t('o formulário não usa a validação que travava', 'novalidate' in form, form)
t('o caso que travava está na página (prazo antigo abaixo do min)', re.search(rf'name="prazo_{velha.id}" min="{hoje:%Y-%m-%d}"\s+value="{velha.prazo:%Y-%m-%d}"', h) is not None)

print('Guardar a cada escolha')
r = guardar(ideia=a1.id, decisao='segue', resposta='Boa', executor=execu.id, prazo=futuro.isoformat())
x = RascunhoDecisaoIdeia.objects.get(usuario=chefe, ideia=a1)
t('grava decisão, retorno, ponto focal e prazo', r.json()['ok'] and (x.decisao, x.resposta, x.executor_id, x.prazo) == ('segue', 'Boa', execu.id, futuro))
guardar(ideia=a1.id, decisao='segue', resposta='Boa ideia', executor=execu.id, prazo=futuro.isoformat())
t('mudar atualiza, sem duplicar', RascunhoDecisaoIdeia.objects.filter(ideia=a1).count() == 1 and RascunhoDecisaoIdeia.objects.get(ideia=a1).resposta == 'Boa ideia')
guardar(ideia=a2.id, decisao='nao', resposta='Agora não')
guardar(ideia=a3.id, decisao='segue')
t('nada mudou na ideia ainda', Ideia.objects.get(pk=a1.id).status == 'NOVA' and not Meta.objects.exists())
guardar(ideia=a4.id, decisao='nao'); guardar(ideia=a4.id, decisao='')
t('voltar para "decidir depois" tira', not RascunhoDecisaoIdeia.objects.filter(ideia=a4).exists())
t('quem não é gestor não grava', guardar(ca, ideia=a1.id, decisao='nao').status_code in (302, 403) and RascunhoDecisaoIdeia.objects.filter(usuario=autor).count() == 0)
t('ideia que não existe', guardar(ideia=999999, decisao='nao').status_code == 404)
t('envio quebrado', cs.post(R, 'xx', content_type='application/json').status_code == 400)
guardar(co, ideia=a1.id, decisao='nao')
t('cada gestor tem os seus', RascunhoDecisaoIdeia.objects.get(usuario=chefe, ideia=a1).decisao == 'segue' and RascunhoDecisaoIdeia.objects.get(usuario=outro, ideia=a1).decisao == 'nao')

print('Voltar para a tela')
h = cs.get(URL).content.decode()
d = json.loads(re.search(r'<script id="inovarRascunhos" type="application/json">(.*?)</script>', h, re.S).group(1))
t('a página traz o que foi escolhido', d[str(a1.id)] == {'decisao': 'segue', 'resposta': 'Boa ideia', 'executor': execu.id, 'prazo': futuro.isoformat()} and d[str(a2.id)]['decisao'] == 'nao' and len(d) == 3, d)
h = cs.get(URL, {'status': 'APROVADA'}).content.decode()
t('fora do filtro: avisa que tem guardado', 'Você tem 3 decisão(ões) guardada(s)' in h)
t('quem não é gestor não vê rascunho', 'inovarRascunhos' not in ca.get(URL).content.decode())

print('Aplicar')
def post(*ideias, **extra):
    d = {'ideia': [i.id for i in ideias]}; d.update(extra)
    return cs.post(L, d, follow=True)


r = post(a1, a2, a3, velha, **{f'decisao_{a1.id}': 'segue', f'executor_{a1.id}': execu.id, f'prazo_{a1.id}': futuro.isoformat(), f'resposta_{a1.id}': 'Boa ideia',
                               f'decisao_{a2.id}': 'nao', f'resposta_{a2.id}': 'Agora não',
                               f'decisao_{a3.id}': 'segue',
                               f'prazo_{velha.id}': velha.prazo.isoformat()})
h = r.content.decode()
a1.refresh_from_db(); a2.refresh_from_db(); a3.refresh_from_db(); velha.refresh_from_db()
t('aprova, com a atividade aberta', a1.status == 'APROVADA' and a1.executor_id == execu.id and a1.prazo == futuro and a1.meta_gerada_id, (a1.status, a1.meta_gerada_id))
t('arquiva', a2.status == 'ARQUIVADA' and a2.resposta_gestor == 'Agora não')
t('o que foi aplicado sai dos guardados', not RascunhoDecisaoIdeia.objects.filter(usuario=chefe, ideia__in=[a1, a2]).exists())
t('faltou ponto focal: avisa e continua guardado', a3.status == 'NOVA' and RascunhoDecisaoIdeia.objects.filter(usuario=chefe, ideia=a3).exists() and 'continuam guardadas' in h)
t('prazo antigo escondido não atrapalha nem muda nada', velha.status == 'APROVADA' and velha.prazo < hoje)
d = json.loads(re.search(r'<script id="inovarRascunhos" type="application/json">(.*?)</script>', h, re.S).group(1))
t('a que falhou volta preenchida', list(d) == [str(a3.id)], d)

print('Enviar sem ter passado pelo salvamento automático')
r = post(a4, **{f'decisao_{a4.id}': 'segue', f'executor_{a4.id}': execu.id, f'prazo_{a4.id}': (hoje - dt.timedelta(days=1)).isoformat()})
t('o envio também guarda (prazo vencido fica para corrigir)', RascunhoDecisaoIdeia.objects.filter(usuario=chefe, ideia=a4, decisao='segue').exists() and 'prazo já passou' in r.content.decode())
with mock.patch.object(impulso_views, '_aplicar_decisao_da_ideia', side_effect=RuntimeError('caiu')):
    r = post(a4, **{f'decisao_{a4.id}': 'segue', f'executor_{a4.id}': execu.id, f'prazo_{a4.id}': futuro.isoformat()})
a4.refresh_from_db()
t('falha ao abrir a atividade: não fica aprovada pela metade e segue guardada', a4.status == 'NOVA' and RascunhoDecisaoIdeia.objects.filter(usuario=chefe, ideia=a4).exists() and 'tente de novo' in r.content.decode(), a4.status)

print('Rascunho vencido')
guardar(co, ideia=a3.id, decisao='nao')
Ideia.objects.filter(pk=a3.id).update(status='ARQUIVADA')
h = co.get(URL).content.decode()
d = json.loads(re.search(r'<script id="inovarRascunhos" type="application/json">(.*?)</script>', h, re.S).group(1))
t('outro gestor decidiu: o rascunho antigo cai', str(a3.id) not in d and not RascunhoDecisaoIdeia.objects.filter(usuario=outro, ideia=a3).exists())

print(f'\n{ok} OK, {fail} falha(s)')
