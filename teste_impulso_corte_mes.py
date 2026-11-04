"""Impulso (04/11/2026): CicloMes.corte_em. Com corte, só conta o que aconteceu antes dele.

Pedido: outubro só com as coisas de outubro. Cobre cada item (meta criada/avaliada depois, conteúdo
conferido depois, projeto aprovado depois, ideia aprovada depois, feedback e tarefa criados depois),
reabrir e fechar de novo sem os pontos voltarem, mês sem corte igual a antes, e o aviso na tela.

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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-impulso-corte-'))
(PASTA / 'zz_impulso_corte_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-icorte'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-icorte-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_impulso_corte_settings'
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
from decimal import Decimal as D

import requests
from django.utils import timezone

from impulso import ciclos
from impulso.models import (Ciclo, CicloMes, ConclusaoConteudo, ConteudoConectar, Ideia, Meta, ProjetoFoco, TarefaProjeto)
from impulso.scoring import calcular_pontuacao
from communications.models import CommunicationGroup
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

TZ = timezone.get_current_timezone()
OUT, FIM = dt.date(2026, 10, 1), dt.date(2026, 10, 31)
CORTE = dt.datetime(2026, 11, 1, tzinfo=TZ)
EM_OUT, EM_NOV = dt.datetime(2026, 10, 20, 12, tzinfo=TZ), dt.datetime(2026, 11, 3, 12, tzinfo=TZ)
setor = Sector.objects.create(name='ZZ Setor')
ana = User.objects.create_user(username='zzc.ana', email='zzc.ana@exemplo-teste.local', password='S3nha!teste', first_name='Ana', last_name='ZZ', sector=setor, hierarchy='PADRAO')
chefe = User.objects.create_user(username='zzc.chefe', email='zzc.chefe@exemplo-teste.local', password='S3nha!teste', first_name='Che', last_name='ZZ', sector=setor, hierarchy='SUPERADMIN', is_superuser=True)
adm, _ = CommunicationGroup.objects.get_or_create(name='ESCRITÓRIO (ADM)', defaults={'created_by': chefe})
ana.communication_groups.add(adm)
ciclo = Ciclo.objects.create(nome='ZZ', inicio=OUT, fim=FIM, criado_por=chefe)
mes = CicloMes.objects.create(ciclo=ciclo, referencia=OUT)


def data(modelo, pk, **campos):
    modelo.objects.filter(pk=pk).update(**campos)      # auto_now_add não aceita data no create


def meta(titulo, criada=EM_OUT, entregue=EM_OUT, avaliada=EM_OUT, status='CONCLUIDA'):
    m = Meta.objects.create(gestor=chefe, colaborador=ana, titulo=titulo, descricao='x', prazo=dt.date(2026, 10, 15), status=status,
                            aprovacao='APROVADA', created_by=chefe, nota_qualidade=5 if avaliada else None, nota_prazo=5 if avaliada else None)
    data(Meta, m.pk, created_at=criada, entregue_em=entregue, avaliado_em=avaliada)
    return m


def ideia(tipo, aprovada_em):
    campos = {f.name: 'x' for f in Ideia._meta.concrete_fields
              if f.get_internal_type() in ('CharField', 'TextField') and not f.blank and not f.choices}
    campos.update(autor=ana, setor_impacto='ZZ', tipo=tipo, status='APROVADA')
    i = Ideia.objects.create(**campos)
    data(Ideia, i.pk, criado_em=EM_OUT)
    if aprovada_em:
        g = Meta.objects.create(gestor=chefe, colaborador=chefe, titulo='da ideia', descricao='x', prazo=dt.date(2026, 11, 20), aprovacao='APROVADA', created_by=chefe)
        data(Meta, g.pk, created_at=aprovada_em)
        data(Ideia, i.pk, meta_gerada=g)
    return i


meta('feita em outubro')
meta('avaliada em novembro', avaliada=EM_NOV)
meta('criada em novembro com prazo de outubro', criada=EM_NOV, entregue=None, avaliada=None, status='A_FAZER')
ideia('PROJETO', EM_NOV); ideia('MELHORIA', EM_NOV); ideia('MELHORIA', EM_NOV)
curso = ConteudoConectar.objects.create(titulo='Curso ZZ', tipo='CURSO', obrigatorio=True, ativo=True, inicio=OUT, fim=FIM)
curso.obrigatorio_para.add(ana)
data(ConteudoConectar, curso.pk, criado_em=EM_OUT)
c = ConclusaoConteudo.objects.create(user=ana, conteudo=curso, concluido=True, concluido_em=EM_OUT, aprovacao='APROVADA', decidida_em=EM_NOV)
proj = ProjetoFoco.objects.create(nome='Projeto ZZ', concluido=True, concluido_em=EM_OUT, aprovacao='APROVADA', decidida_em=EM_NOV)
parte = TarefaProjeto.objects.create(projeto=proj, responsavel=ana, titulo='Parte da Ana', status='CONCLUIDA', concluida_em=EM_OUT, prazo=dt.date(2026, 10, 15))
data(TarefaProjeto, parte.pk, criado_em=EM_OUT, concluida_em=EM_OUT)
nova_tarefa = TarefaProjeto.objects.create(projeto=proj, responsavel=ana, titulo='Criada em novembro, sem prazo')
data(TarefaProjeto, nova_tarefa.pk, criado_em=EM_NOV)

print('Sem corte: a regra de sempre')
s = calcular_pontuacao(ana, inicio=OUT, fim=FIM)
t('avaliação, conferência e aprovações depois do mês contam', s['p_metas_conclusao'] == D('6.67') and s['p_curso'] == D('10.00') and s['p_ideia_aprovada'] == D('10.00'), (s['p_metas_conclusao'], s['p_curso'], s['p_ideia_aprovada']))
t('o projeto aprovado depois conta; a tarefa nova pesa', s['detalhes']['projeto_foco'].get('projetos_concluidos') == 1 and s['detalhes']['projeto_foco'].get('total') == 2)

print('Com corte')
CicloMes.objects.filter(pk=mes.pk).update(corte_em=CORTE)
c2 = calcular_pontuacao(ana, inicio=OUT, fim=FIM)
d = c2['detalhes']
t('meta criada depois sai da conta', d['metas']['total'] == 2, d['metas'])
t('meta avaliada depois não está concluída nem avaliada', d['metas']['concluidas'] == 1 and d['metas']['avaliadas'] == 1 and c2['p_metas_conclusao'] == D('5.00') and c2['p_metas_qualidade'] == D('10.00'), d['metas'])
t('conteúdo conferido depois volta a "aguardando"', c2['p_curso'] == 0 and d['curso']['concluidos'] == 0 and d['curso']['aguardando'] == 1 and d['curso']['itens'][0]['situacao'] == 'PENDENTE', d['curso'])
t('projeto aprovado depois perde a metade da conclusão', d['projeto_foco']['projetos_concluidos'] == 0 and c2['p_projeto_foco'] == D('10.00'), d['projeto_foco'])
t('tarefa criada depois não pesa', d['projeto_foco']['total'] == 1 and d['projeto_foco']['concluidas'] == 1)
t('ideia aprovada depois: sem pontos e fora do desempate', c2['p_ideia_aprovada'] == 0 and d['inovar']['aprovadas'] == 0 and c2['desempate']['ideias_aprovadas'] == 0, d['inovar'])
t('as 3 ideias propostas no mês continuam valendo', c2['p_ideias'] == D('10.00') and d['inovar']['propostas'] == 3)
ideia('PROJETO', EM_OUT)
t('projeto aprovado antes do corte conta', calcular_pontuacao(ana, inicio=OUT, fim=FIM)['p_ideia_aprovada'] == D('10.00'))
Ideia.objects.filter(meta_gerada__created_at=EM_OUT).delete()

print('Fechar, reabrir e fechar de novo')
mes.refresh_from_db()
ciclos.fechar_mes(mes, chefe)
p = mes.pontuacoes.get(user=ana)
t('o fechamento usa o corte', p.p_ideia_aprovada == 0 and p.p_projeto_foco == D('10.00') and p.detalhes['inovar']['aprovadas'] == 0, p.p_ideia_aprovada)
primeiro = p.total
ciclos.reabrir_mes(mes)
mes.refresh_from_db()
ciclos.fechar_mes(mes, chefe)
t('reabrir e fechar não traz os pontos de volta', mes.pontuacoes.get(user=ana).total == primeiro)
t('o corte continua no mês', CicloMes.objects.get(pk=mes.pk).corte_em == CORTE)
outubro_d = calcular_pontuacao(ana, inicio=OUT, fim=FIM)
t('tela do mês passado calcula igual ao fechamento', outubro_d['total'] == primeiro)
nov = calcular_pontuacao(ana, inicio=dt.date(2026, 11, 1), fim=dt.date(2026, 11, 30))
t('novembro não herda o corte', nov['detalhes']['projeto_foco'].get('projetos_concluidos') == 1, nov['detalhes']['projeto_foco'])

from django.test import Client
cs = Client(); cs.force_login(chefe)
h = cs.get(f'/impulso/ciclos/mes/{mes.pk}/').content.decode()
t('a tela avisa do corte', 'Conta só o que aconteceu antes de 01/11/2026' in h, h[:0])

print(f'\n{ok} OK, {fail} falha(s)')
