"""/impulso/metas/ (05/11/2026): caixa "A fazer Vinicius" do SUPERADMIN abre uma coluna com o A Fazer criado pelo Vinicius (User 41).

17/11/2026: o resto do que ele criou fica na coluna do seu status, com card vermelho; o User 47 abre com a caixa marcada. Banco descartável."""
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-kb-vinicius-'))
(PASTA / 'zz_kb_vinicius_settings.py').write_text(f"""
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
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_kb_vinicius_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

import requests
from django.utils import timezone

from communications.models import CommunicationGroup
from impulso.models import Meta

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


def novo(u, **k):
    k.setdefault('hierarchy', 'PADRAO')
    return User.objects.create_user(username=f'zzk.{u}', email=f'zzk.{u}@exemplo-teste.local', password='S3nha!teste', first_name=u.title(), last_name='ZZ', **k)


vinicius = novo('vinicius', id=41, hierarchy='SUPERADMIN')
chefe = novo('chefe', hierarchy='SUPERADMIN', is_superuser=True)
# 17/11/2026: o User 47 (SUPERADMIN no banco real) abre com a caixa marcada.
alcides = novo('alcides', id=47, hierarchy='SUPERADMIN')
gestor = novo('gestor')
ana = novo('ana')
for nome_grupo, membros in (('ESCRITÓRIO (ADM)', [ana, gestor]), ('GESTORES (IMPULSO)', [gestor])):
    g, _ = CommunicationGroup.objects.get_or_create(name=nome_grupo, defaults={'created_by': chefe})
    g.members.add(*membros)
hoje = timezone.localdate()
base = dict(gestor=gestor, colaborador=ana, prazo=hoje, aprovacao=Meta.Aprovacao.APROVADA)
dele1 = Meta.objects.create(titulo='Do Vinicius 1', created_by=vinicius, **base)
dele2 = Meta.objects.create(titulo='Do Vinicius 2', created_by=vinicius, **base)
andando = Meta.objects.create(titulo='Dele em andamento', created_by=vinicius, status='EM_ANDAMENTO', **base)
outra = Meta.objects.create(titulo='De outra pessoa', created_by=gestor, **base)
# 05/11/2026: a coluna passou a trazer tudo o que ele criou e não foi concluído, de qualquer mês.
longe = Meta.objects.create(titulo='Dele mês que vem', created_by=vinicius,
                            **{**base, 'prazo': hoje + timezone.timedelta(days=70)})
feita = Meta.objects.create(titulo='Dele concluída', created_by=vinicius, status='CONCLUIDA', **base)
# Em avaliação e fora do mês da tela: entra mesmo assim, na coluna Entregue.
avaliando = Meta.objects.create(titulo='Dele em avaliação', created_by=vinicius, status='ENTREGUE',
                                **{**base, 'prazo': hoje + timezone.timedelta(days=70)})
outra_andando = Meta.objects.create(titulo='Outra em andamento', created_by=gestor, status='EM_ANDAMENTO', **base)
# O 47 não é is_superuser: enxerga pelo _metas_do_usuario (aqui, como participante de todas).
for m in Meta.objects.all():
    m.participantes.add(alcides)
cs = Client(); cs.force_login(chefe)
ca = Client(); ca.force_login(alcides)
VERMELHO = 'kanban-card imp-card p-3.5 imp-card-vinicius'
E, ENT = 'Em Andamento', 'Entregue (aguardando avaliação)'
cg = Client(); cg.force_login(gestor)
U = '/impulso/metas/'


def colunas(cliente, q=''):
    r = cliente.get(U + q)
    return r, {c['label']: [m.titulo for m in c['metas']] for c in r.context['colunas']}


r, c = colunas(cs)
h = r.content.decode()
t('caixa aparece para o SUPERADMIN, desmarcada', 'data-filtro-a-fazer-vinicius' in h and 'A fazer Vinicius' in h and not r.context['a_fazer_vinicius'])
t('desmarcada: quatro colunas, como sempre', 'A Fazer Vinicius' not in c and len(c) == 4 and 'xl:grid-cols-4' in h, list(c))
t('desmarcada: A Fazer traz tudo', set(c['A Fazer']) == {'Do Vinicius 1', 'Do Vinicius 2', 'De outra pessoa', 'Dele mês que vem'}, c['A Fazer'])
t('desmarcada: nenhum card vermelho e Em Andamento igual', VERMELHO not in h and set(c[E]) == {'Dele em andamento', 'Outra em andamento'}, c[E])
r, c = colunas(cs, '?a_fazer_vinicius=1')
h = r.content.decode()
t('marcada: abre a coluna antes do A Fazer', list(c)[:2] == ['A Fazer Vinicius', 'A Fazer'] and len(c) == 5 and 'xl:grid-cols-5' in h, list(c))
t('a coluna traz o A Fazer dele, de qualquer mês', set(c['A Fazer Vinicius']) == {'Do Vinicius 1', 'Do Vinicius 2', 'Dele mês que vem'}, c['A Fazer Vinicius'])
t('sem aparecer duas vezes', c['A Fazer'] == ['De outra pessoa'], c['A Fazer'])
t('o que está em andamento fica na Em Andamento', set(c[E]) == {'Dele em andamento', 'Outra em andamento'}, c[E])
t('o que está em avaliação vai para a Entregue, mesmo fora do mês', c[ENT] == ['Dele em avaliação'], c[ENT])
todos = [x for v in c.values() for x in v]
t('nenhum card repetido na tela', len(todos) == len(set(todos)), todos)
t('concluída continua na Concluída', 'Dele concluída' in c['Concluída'] and 'Dele concluída' not in c['A Fazer Vinicius'], c['Concluída'])
t('total certo e agrupado por prazo', r.context['colunas'][0]['total'] == 3 and r.context['colunas'][0]['grupos'])
t('totais das colunas de status contam os dele', [k['total'] for k in r.context['colunas'][2:4]] == [2, 1], [k['total'] for k in r.context['colunas']])
t('os cards dele são vermelhos (3 na coluna + andamento + avaliação + concluída)', h.count(VERMELHO) == 6 and '.imp-card.imp-card-vinicius' in h,
  h.count(VERMELHO))


def card(html, titulo):
    fim = html.index('>' + titulo + '</a>')
    return html[html.rindex('kanban-card imp-card', 0, fim):fim]


t('o card dele em andamento é vermelho e o da outra pessoa não', VERMELHO.split('kanban-card ')[1] in card(h, 'Dele em andamento')
  and 'imp-card-vinicius' not in card(h, 'Outra em andamento'))
t('o card dele concluído é vermelho', 'imp-card-vinicius' in card(h, 'Dele concluída'))
t('o card de outra pessoa no A Fazer não é vermelho', 'imp-card-vinicius' not in card(h, 'De outra pessoa'))
t('caixa volta marcada', 'name="a_fazer_vinicius" value="1"' in h and 'checked' in h[h.index('data-filtro-a-fazer-vinicius'):h.index('A fazer Vinicius', h.index('data-filtro-a-fazer-vinicius'))])
t('dá para arrastar para as outras colunas', h.count('data-status="A_FAZER"') == 2 and 'data-droppable="true"' in h)
r, c = colunas(cs, '?a_fazer_vinicius=1&colaborador=999999')
t('mostra tudo dele mesmo com outros filtros', len(c['A Fazer Vinicius']) == 3 and c['A Fazer'] == []
  and c[E] == ['Dele em andamento'] and c[ENT] == ['Dele em avaliação'], c)
r = cs.get(U + '?a_fazer_vinicius=1&colaborador=%d' % ana.id)
h = r.content.decode()
forma = h[h.index('data-filtro-a-fazer-vinicius'):h.index('A fazer Vinicius', h.index('data-filtro-a-fazer-vinicius'))]
t('desmarcar mantém os outros filtros e manda =0', f'name="colaborador" value="{ana.id}"' in forma
  and forma.count('name="a_fazer_vinicius"') == 2 and 'type="hidden" name="a_fazer_vinicius" value="0"' in forma)
r, c = colunas(cs, '?a_fazer_vinicius=0&a_fazer_vinicius=1')
t('marcar pelo formulário (=0 e =1 juntos) marca', r.context['a_fazer_vinicius'] and 'A Fazer Vinicius' in c)
r, c = colunas(cs, '?a_fazer_vinicius=0')
t('SUPERADMIN desmarcando: tudo como antes', not r.context['a_fazer_vinicius'] and len(c) == 4 and VERMELHO not in r.content.decode())
from django.urls import reverse
UC = reverse('impulso:metas_kanban_concluidas')
j = cs.get(UC + '?a_fazer_vinicius=1&mes=&inicio=0').json()
t('"Mostrar mais" com a caixa marcada: concluída dele vermelha', 'imp-card-vinicius' in j['html'] and 'Dele concluída' in j['html'], j)
j = cs.get(UC + '?mes=&inicio=0').json()
t('"Mostrar mais" sem a caixa: nada vermelho', 'imp-card-vinicius' not in j['html'] and 'Dele concluída' in j['html'])
cs.get(U + '?a_fazer_vinicius=1')
d = cs.get(f'/impulso/metas/{dele1.id}/')
t('o Voltar da meta devolve a coluna aberta', 'a_fazer_vinicius=1' in d.content.decode())
r, c = colunas(cg, '?a_fazer_vinicius=1')
h = r.content.decode()
t('gestor não vê a caixa nem a coluna', 'data-filtro-a-fazer-vinicius' not in h and 'A Fazer Vinicius' not in c and not r.context['a_fazer_vinicius'], list(c))
t('e o A Fazer dele segue igual', {'Do Vinicius 1', 'De outra pessoa'} <= set(c['A Fazer']), c['A Fazer'])


print('User 47')
r, c = colunas(ca)
h = r.content.decode()
forma = h[h.index('data-filtro-a-fazer-vinicius'):h.index('A fazer Vinicius', h.index('data-filtro-a-fazer-vinicius'))]
t('47 abre com a caixa marcada', r.context['a_fazer_vinicius'] and 'checked' in forma and list(c)[:2] == ['A Fazer Vinicius', 'A Fazer'], list(c))
t('47: A Fazer Vinicius com o A Fazer dele', set(c['A Fazer Vinicius']) == {'Do Vinicius 1', 'Do Vinicius 2', 'Dele mês que vem'}, c['A Fazer Vinicius'])
t('47: os outros dele nas colunas de status, vermelhos', 'Dele em andamento' in c[E] and c[ENT] == ['Dele em avaliação']
  and 'imp-card-vinicius' in card(h, 'Dele em andamento') and 'imp-card-vinicius' in card(h, 'Dele em avaliação')
  and 'imp-card-vinicius' in card(h, 'Dele concluída') and h.count(VERMELHO) == 6, h.count(VERMELHO))
t('47: A Fazer comum sem os dele', c['A Fazer'] == ['De outra pessoa'], c['A Fazer'])
r, c = colunas(ca, '?a_fazer_vinicius=0')
h = r.content.decode()
forma = h[h.index('data-filtro-a-fazer-vinicius'):h.index('A fazer Vinicius', h.index('data-filtro-a-fazer-vinicius'))]
t('47 desmarcou: fica desmarcado', not r.context['a_fazer_vinicius'] and 'checked' not in forma and len(c) == 4 and VERMELHO not in h, list(c))
t('47 desmarcado: A Fazer traz tudo', set(c['A Fazer']) == {'Do Vinicius 1', 'Do Vinicius 2', 'De outra pessoa', 'Dele mês que vem'}, c['A Fazer'])
t('47 desmarcado: trocar colaborador mantém o =0', 'name="a_fazer_vinicius" value="0"' in h[:h.index('data-filtro-a-fazer-vinicius')])
r, c = colunas(ca, '?a_fazer_vinicius=0&colaborador=%d' % ana.id)
t('47 desmarcado com outro filtro: segue desmarcado', not r.context['a_fazer_vinicius'] and 'A Fazer Vinicius' not in c)
d = ca.get(f'/impulso/metas/{dele1.id}/')
t('47: o Voltar da meta mantém desmarcado', 'a_fazer_vinicius=0' in d.content.decode())
r, c = colunas(ca, '?a_fazer_vinicius=0&a_fazer_vinicius=1')
t('47 marca de novo', r.context['a_fazer_vinicius'] and 'A Fazer Vinicius' in c)
j = ca.get(UC + '?mes=&inicio=0').json()
t('47: "Mostrar mais" sem parâmetro também pinta a concluída dele', 'imp-card-vinicius' in j['html'])
r, c = colunas(cs)
t('outro SUPERADMIN segue abrindo desmarcado', not r.context['a_fazer_vinicius'] and len(c) == 4)

# 17/11/2026 ("liberar todas"): meta do Vinicius de alguém fora do alcance do 47 (era a 4442 no banco real).
fora = Meta.objects.create(titulo='Dele fora da equipe', created_by=vinicius, gestor=gestor, colaborador=gestor,
                           prazo=hoje, aprovacao=Meta.Aprovacao.APROVADA)
fora_andando = Meta.objects.create(titulo='Dele fora em andamento', created_by=vinicius, gestor=gestor, colaborador=gestor,
                                   prazo=hoje, status='EM_ANDAMENTO', aprovacao=Meta.Aprovacao.APROVADA)
pendente = Meta.objects.create(titulo='Dele ainda pendente', created_by=vinicius, gestor=gestor, colaborador=gestor,
                               prazo=hoje, aprovacao=Meta.Aprovacao.PENDENTE)
r, c = colunas(ca)
h = r.content.decode()
t('47 marcado: vê a do Vinicius fora da equipe dele', 'Dele fora da equipe' in c['A Fazer Vinicius']
  and 'Dele fora em andamento' in c[E] and 'imp-card-vinicius' in card(h, 'Dele fora em andamento'), c)
t('47: solicitação pendente continua fora do Kanban', 'Dele ainda pendente' not in sum(c.values(), []))
t('47 abre a meta fora da equipe', ca.get(f'/impulso/metas/{fora.id}/').status_code == 200)
r = ca.post(f'/impulso/metas/{fora.id}/status/', {'status': 'EM_ANDAMENTO'})
t('47 move o card fora da equipe', r.status_code == 200 and Meta.objects.get(pk=fora.pk).status == 'EM_ANDAMENTO', r.content[:200])
r, c = colunas(ca, '?a_fazer_vinicius=0')
t('47 desmarcado: as de fora da equipe somem', 'Dele fora da equipe' not in sum(c.values(), [])
  and 'Dele fora em andamento' not in sum(c.values(), []))
d = cg.get(f'/impulso/metas/{dele1.id}/')
r, c = colunas(cg, '?a_fazer_vinicius=1')
t('gestor segue sem a coluna', 'A Fazer Vinicius' not in c)

print(f'\n{ok} OK, {fail} falha(s)')
