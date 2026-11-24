"""/impulso/metas/ (23/11/2026): o cartão do check-list da Rotina ADM vai para "Aguardando Aprovação".

Pedido: 'em /impulso/metas a meta "Rotina ADM · Check-list da Loja Anchieta · 23/11" deve ir
para "Aguardando Aprovação"'.

O cartão não é Meta: é o espelho das marcações do check-list (rotina_adm/espelho.py), que até
aqui ficava fixo na coluna "Concluída" — sem ninguém ter avaliado. Agora fica na coluna
"Entregue (aguardando avaliação)", ao lado das metas entregues que esperam o gestor.

Reproduz o caso real: Loja Anchieta com 6 itens (até 9h15), um ADM de fora da loja (setor
Treinamentos, gestor da Rotina ADM) marca 3 itens no fim da tarde; o SUPERADMIN e ele mesmo
abrem o Kanban.

Roda num sqlite descartável; nada sai para a rede e nenhuma mensagem é enviada.
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-impulso-checklist-aprov-'))
(PASTA / 'zz_impulso_checklist_aprov_settings.py').write_text(f"""
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
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
CACHES = {{
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-imp-ck-ap'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-imp-ck-ap-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_impulso_checklist_aprov_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
os.environ.setdefault('RC_SEM_TAREFAS_DIARIAS', '1')

django.setup()

from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import json
import re
from datetime import time

import requests
from django.contrib.auth import get_user_model
from django.utils import timezone

from communications.models import CommunicationGroup
from impulso.models import Meta
from rotina_adm import espelho
from rotina_adm.models import AcessoRotinaAdm, ItemChecklist, MarcacaoChecklist
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


for nome in ('get', 'post', 'put', 'patch', 'delete', 'request'):
    mock.patch.object(requests, nome, sem_rede).start()
mock.patch('requests.Session.request', sem_rede).start()

anchieta = Sector.objects.create(name='Loja Anchieta')
treinamentos = Sector.objects.create(name='Treinamentos')
escritorio = Sector.objects.create(name='Diretoria')


def usuario(nome, hierarquia='PADRAO', setor=anchieta, **extra):
    u = User.objects.create_user(username=f'zzca.{nome}', email=f'zzca.{nome}@exemplo-teste.local',
                                 password='S3nha!teste', first_name='ZZCA', last_name=nome, sector=setor,
                                 hierarchy=hierarquia, **extra)
    c = Client()
    c.force_login(u)
    return u, c


chefe, cs = usuario('super', 'SUPERADMIN', escritorio, is_superuser=True)
henrique, ch = usuario('henrique', 'ADMIN', treinamentos)
colega, cc = usuario('colega', setor=anchieta)

adms = CommunicationGroup.objects.create(name="ADM's LOJAS", created_by=chefe)
adms.members.add(henrique, colega)
AcessoRotinaAdm.objects.create(user=henrique, papel=AcessoRotinaAdm.GESTOR)

itens = [ItemChecklist.objects.create(loja=anchieta, titulo=f'ZZ Item {n}', horario=time(9, 15), ordem=n)
         for n in range(1, 7)]
hoje = timezone.localdate()
titulo_cartao = f'Rotina ADM · Check-list da Loja Anchieta · {hoje:%d/%m}'


def marcar(cli, item, feito=True):
    return cli.post(f'/rotina-adm/checklist/{item.id}/marcar/', json.dumps({'feito': feito}),
                    content_type='application/json').json()


def kanban(cli, **params):
    r = cli.get('/impulso/metas/', params)
    return r.status_code, r.content.decode()


def colunas(html):
    """{status: trecho do HTML da coluna}, na ordem do Kanban."""
    marcas = [(m.group(1), m.start()) for m in re.finditer(r'kanban-dropzone" data-status="([A-Z_]+)"', html)]
    saida = {}
    for n, (status, ini) in enumerate(marcas):
        fim = marcas[n + 1][1] if n + 1 < len(marcas) else len(html)
        saida[status] = html[ini:fim]
    return saida


def total_coluna(html, status):
    m = re.search(rf'data-total-coluna="{status}">(\d+)<', html)
    return int(m.group(1)) if m else None


# ─────────────────────────────────────────────────────────────────────────────
print('Cenário: 3 de 6 itens da Loja Anchieta marcados por um ADM de fora da loja')
metas_antes = Meta.objects.count()
for item in itens[:3]:
    t(f'marca {item.titulo}', marcar(ch, item).get('ok'))
t('3 marcações gravadas, de quem marcou', MarcacaoChecklist.objects.filter(feito_por=henrique, data=hoje).count() == 3)
t('a coluna do espelho é "Entregue (aguardando avaliação)"', espelho.COLUNA == Meta.Status.ENTREGUE.value
  and Meta.Status.ENTREGUE.value in [s.value for s in Meta.KANBAN_STATUSES], espelho.COLUNA)

for quem, cli in (('SUPERADMIN', cs), ('quem marcou', ch)):
    print(f'Kanban visto pelo {quem}')
    st, k = kanban(cli)
    t('Kanban abre', st == 200, st)
    cols = colunas(k)
    t('as quatro colunas estão na tela', list(cols) == ['A_FAZER', 'EM_ANDAMENTO', 'ENTREGUE', 'CONCLUIDA'], list(cols))
    t('o cartão aparece em "Aguardando avaliação" (Entregue)', titulo_cartao in cols.get('ENTREGUE', ''))
    t('o cartão NÃO aparece em "Concluída"', titulo_cartao not in cols.get('CONCLUIDA', '')
      and 'data-espelho-rotina-adm' not in cols.get('CONCLUIDA', ''))
    t('nem em A Fazer / Em Andamento', all(titulo_cartao not in cols.get(s, '') for s in ('A_FAZER', 'EM_ANDAMENTO')))
    t('um cartão só, com os 3 itens e a loja 3/6', k.count('data-espelho-cartao=') == 1 and '3 itens marcados' in k
      and 'loja 3/6 no dia' in k)
    t('o selo do cartão diz "aguardando avaliação"', 'data-espelho-aguardando' in cols.get('ENTREGUE', '')
      and 'title="Concluída na Rotina ADM"' not in k)
    t('a coluna Entregue não mostra "Vazio" por cima do cartão', 'Vazio' not in cols.get('ENTREGUE', ''))
    t('a Concluída, sem metas, mostra "Vazio"', 'Vazio' in cols.get('CONCLUIDA', ''))
    t('o espelho não entra no total das colunas', total_coluna(k, 'ENTREGUE') == 0 and total_coluna(k, 'CONCLUIDA') == 0,
      (total_coluna(k, 'ENTREGUE'), total_coluna(k, 'CONCLUIDA')))
    t('o cartão do espelho não é arrastável', 'data-meta-id' not in cols.get('ENTREGUE', ''))

st, k = kanban(cs, colaborador=henrique.id)
t('filtro do colaborador mantém o cartão em Entregue', titulo_cartao in colunas(k).get('ENTREGUE', ''))
st, k = kanban(cc)
t('colega da loja que não marcou não vê o cartão', st == 200 and titulo_cartao not in k)

print('Convive com meta entregue de verdade')
real = Meta.objects.create(gestor=chefe, colaborador=henrique, titulo='ZZCA meta entregue', descricao='x', prazo=hoje,
                           aprovacao=Meta.Aprovacao.APROVADA, status=Meta.Status.ENTREGUE, created_by=chefe)
st, k = kanban(cs)
cols = colunas(k)
t('meta entregue e cartão do check-list juntos em Entregue', 'ZZCA meta entregue' in cols.get('ENTREGUE', '')
  and titulo_cartao in cols.get('ENTREGUE', ''))
t('o total da coluna conta só a meta de verdade', total_coluna(k, 'ENTREGUE') == 1, total_coluna(k, 'ENTREGUE'))
real.delete()

print('Check-list completo continua aguardando avaliação')
for item in itens[3:]:
    marcar(ch, item)
st, k = kanban(cs)
cols = colunas(k)
t('loja 6/6: o cartão segue em Entregue', titulo_cartao in cols.get('ENTREGUE', '') and 'loja 6/6 no dia' in k
  and titulo_cartao not in cols.get('CONCLUIDA', ''))

print('Nada gravado no Impulso')
t('nenhuma Meta criada pelo espelho', Meta.objects.count() == metas_antes)
from django.core import mail  # noqa: E402
t('nenhum e-mail enviado', len(mail.outbox) == 0, len(mail.outbox))

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
