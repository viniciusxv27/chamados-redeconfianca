"""/impulso/conectar/projetos/ (05/11/2026): aprovar parcialmente a conclusão de um projeto foco.

Pedido: "Adicione a opção de Aprovar parcialmente, e que só conte metade dos pontos da
conclusão do projeto". Máximo de 20: 10 pelas tarefas, 10 pela conclusão; parcial paga 5.
Banco descartável.
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-parcial-'))
(PASTA / 'zz_parcial_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-iacomp'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-iacomp-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_parcial_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

from datetime import timedelta
from decimal import Decimal as D

import requests
from django.utils import timezone

from core.models import Notification
from communications.models import CommunicationGroup
from impulso.models import GRUPO_ADM, ProjetoFoco, TarefaProjeto
from impulso.scoring import _info_projeto_foco, _nota_projeto_foco, periodo_do_mes

User = get_user_model()
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {str(extra)[:500]}')


def sem_rede(*a, **k):
    raise AssertionError('teste tentou sair para a rede')


for nome_m in ('get', 'post', 'request'):
    mock.patch.object(requests, nome_m, sem_rede).start()


def novo(nome, **kw):
    return User.objects.create_user(username=f'zzp.{nome}', email=f'zzp.{nome}@exemplo-teste.local', password='S3nha!teste',
                                    first_name=nome.title(), last_name='ZZ', **kw)


decisor = novo('decisor', hierarchy='SUPERADMIN')
gestor = novo('gestor', hierarchy='ADMIN', is_superuser=True, is_staff=True)
dev = novo('dev', hierarchy='PADRAO')
dev.communication_groups.add(CommunicationGroup.objects.create(name=GRUPO_ADM, created_by=gestor))   # entra no módulo
ca, cg, cd = Client(), Client(), Client()
ca.force_login(decisor); cg.force_login(gestor); cd.force_login(dev)

hoje = timezone.localdate()
inicio, fim = periodo_do_mes(hoje)


def projeto(nome):
    p = ProjetoFoco.objects.create(nome=nome, criado_por=gestor)
    p.membros.add(dev)
    TarefaProjeto.objects.create(projeto=p, titulo=f'{nome} T', responsavel=dev, prazo=hoje, status=TarefaProjeto.Status.CONCLUIDA)
    return p


def decidir(p, decisao, obs='', cliente=None):
    return (cliente or ca).post(f'/impulso/conectar/projetos/{p.id}/decidir/', {'decisao': decisao, 'observacao': obs}, follow=True)


def nota():
    return _nota_projeto_foco(dev, inicio, fim)


p = projeto('ZZ Vitrine')
print('== TELA DE DECISÃO ==')
cg.post(f'/impulso/conectar/projetos/{p.id}/concluir/')
p.refresh_from_db()
t('a conclusão espera o SUPERADMIN', p.aguardando_aprovacao)
h = ca.get(f'/impulso/conectar/projetos/{p.id}/').content.decode()
t('o SUPERADMIN vê os três botões', all(f'value="{x}"' in h for x in ('aprovar', 'parcial', 'reprovar')) and 'Aprovar parcialmente' in h)
t('e o que a parcial significa', 'conta só metade dos pontos da conclusão' in h)
h = cd.get(f'/impulso/conectar/projetos/{p.id}/').content.decode()
t('a equipe não vê', 'value="parcial"' not in h)

print('\n== QUEM E COMO ==')
decidir(p, 'parcial', 'faltou x', cliente=cd)
p.refresh_from_db()
t('quem não é SUPERADMIN não decide', p.aguardando_aprovacao)
r = decidir(p, 'parcial', '   ')
p.refresh_from_db()
t('sem motivo não passa', p.aguardando_aprovacao and 'motivo da aprovação parcial' in r.content.decode())
t('e não paga nada ainda', nota()[0] == D('10.00'), nota())

print('\n== A DECISÃO ==')
Notification.objects.all().delete()
r = decidir(p, 'parcial', 'Faltou a <vitrine> da loja B')
p.refresh_from_db()
t('fica encerrado e parcial', p.concluido and p.aprovacao == 'PARCIAL' and p.aprovada_parcialmente and p.pagou_conclusao
  and not p.entregue and not p.aguardando_aprovacao and not p.conclusao_reprovada)
t('guarda quem e quando', p.decidida_por == decisor and p.decidida_em and p.observacao.startswith('Faltou'))
n, _m, det = nota()
t('conclusão paga metade: 5 de 10', det['pontos_conclusao'] == D('5.00') and det['pontos_entrega'] == D('10.00'), det)
t('nota total 15 de 20', n == D('15.00'), n)
t('conta à parte do aprovado inteiro', det['projetos_concluidos'] == 0 and det['projetos_parciais'] == 1, det)
info = _info_projeto_foco(det)
t('a explicação da nota diz', '1 aprovado(s) parcialmente, valendo metade' in info and '(5.00 pt)' in info, info)
t('quem tem tarefa é avisado', Notification.objects.filter(user=dev, title='Projeto foco aprovado parcialmente').exists())
t('quem criou também', Notification.objects.filter(user=gestor, title='Projeto foco aprovado parcialmente').exists())
t('quem decidiu não recebe aviso de si mesmo', not Notification.objects.filter(user=decisor, title='Projeto foco aprovado parcialmente').exists())

print('\n== TELAS ==')
h = cd.get(f'/impulso/conectar/projetos/{p.id}/').content.decode()
t('o detalhe mostra o selo e o motivo, escapado', 'aprovado parcialmente' in h and 'Faltou a &lt;vitrine&gt; da loja B' in h and 'Faltou a <vitrine>' not in h)
t('e não diz "concluído" inteiro', 'fa-flag-checkered mr-1"></i>concluído' not in h)
h = cd.get('/impulso/conectar/projetos/').content.decode()
t('a lista mostra "Parcial"', 'Parcial</span>' in h)
h = ca.get('/impulso/conectar/projetos/').content.decode()
t('saiu da fila de aprovação', 'Conclusões esperando sua aprovação' not in h)
r = decidir(p, 'aprovar')
p.refresh_from_db()
t('já decidido não aceita outra decisão', p.aprovacao == 'PARCIAL')
h = cg.get(f'/impulso/conectar/projetos/{p.id}/').content.decode()
t('o aviso de reabrir fala dos pontos', 'Os pontos pela conclusão saem' in h)

print('\n== VÁRIOS PROJETOS ==')
p2 = projeto('ZZ Outro')
ProjetoFoco.objects.filter(pk=p2.pk).update(concluido=True, concluido_em=timezone.now(), aprovacao='APROVADA', decidida_em=timezone.now())
n, _m, det = nota()
t('1 parcial + 1 inteiro: (0,5 + 1) de 2 = 7,50', det['pontos_conclusao'] == D('7.50'), det)
t('total 17,50', n == D('17.50'), n)
ProjetoFoco.objects.filter(pk=p2.pk).update(aprovacao='PARCIAL')
t('2 parciais: 5,00', nota()[2]['pontos_conclusao'] == D('5.00'), nota())
ProjetoFoco.objects.filter(pk=p2.pk).delete()

print('\n== CORTE DO MÊS ==')
TarefaProjeto.objects.update(criado_em=timezone.now() - timedelta(days=1))   # o corte tira tarefa criada depois dele
_n, _m, det = _nota_projeto_foco(dev, inicio, fim, corte=p.decidida_em - timedelta(minutes=1))
t('parcial depois do corte não paga naquele retrato', det['pontos_conclusao'] == D('0.00'), det)
_n, _m, det = _nota_projeto_foco(dev, inicio, fim, corte=p.decidida_em + timedelta(minutes=1))
t('parcial antes do corte paga', det['pontos_conclusao'] == D('5.00'), det)

print('\n== REABRIR ==')
r = cg.post(f'/impulso/conectar/projetos/{p.id}/concluir/', {'reabrir': '1'}, follow=True)
p.refresh_from_db()
t('reabrir tira os pontos e limpa a decisão', not p.concluido and p.aprovacao == 'PENDENTE' and not p.observacao
  and nota()[2]['pontos_conclusao'] == D('0.00'), p.aprovacao)
t('e a mensagem diz', 'os pontos pela conclusão saem' in r.content.decode())
cg.post(f'/impulso/conectar/projetos/{p.id}/concluir/')
decidir(p, 'aprovar')
p.refresh_from_db()
t('concluir de novo pode ganhar a aprovação inteira', p.entregue and nota()[0] == D('20.00'), nota())

print('\n== MÊS FECHADO ANTES DESTA MUDANÇA ==')
t('detalhe sem a chave nova continua explicando', 'parcialmente' not in _info_projeto_foco({'total': 1, 'concluidas': 1, 'pontos_entrega': 10, 'projetos_concluidos': 1, 'projetos': 1, 'pontos_conclusao': 10}))

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
