"""Impulso (04/11/2026): o acompanhamento mostra o mês do ciclo até ele ser finalizado.

Regra pedida para os próximos meses e ciclos: virar o calendário não muda a tela; o mês anterior
fica em conferência até ficar finalizado e ter o ranking. Sem ciclo aberto, vale o mês do calendário.

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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-impulso-acomp-'))
(PASTA / 'zz_impulso_acomp_settings.py').write_text(f"""
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
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_impulso_acomp_settings'
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

import requests
from django.utils import timezone

from communications.models import CommunicationGroup
from impulso import ciclos
from impulso.models import Ciclo, CicloMes, Meta
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

hoje = timezone.localdate()
este = hoje.replace(day=1)
passado = (este - dt.timedelta(days=1)).replace(day=1)
retrasado = (passado - dt.timedelta(days=1)).replace(day=1)
proximo = (este + dt.timedelta(days=32)).replace(day=1)
setor = Sector.objects.create(name='ZZ')
chefe = User.objects.create_user(username='zza.chefe', email='zza.chefe@exemplo-teste.local', password='S3nha!teste', first_name='Che', last_name='ZZ', sector=setor, hierarchy='SUPERADMIN', is_superuser=True)
ana = User.objects.create_user(username='zza.ana', email='zza.ana@exemplo-teste.local', password='S3nha!teste', first_name='Ana', last_name='ZZ', sector=setor, hierarchy='PADRAO')
adm, _ = CommunicationGroup.objects.get_or_create(name='ESCRITÓRIO (ADM)', defaults={'created_by': chefe})
chefe.communication_groups.add(adm); ana.communication_groups.add(adm)
cs = Client(); cs.force_login(chefe)
ca = Client(); ca.force_login(ana)


def meta(titulo, prazo):
    Meta.objects.create(gestor=chefe, colaborador=ana, titulo=titulo, descricao='x', prazo=prazo, status='CONCLUIDA', aprovacao='APROVADA', created_by=chefe, nota_qualidade=5, nota_prazo=5)


meta('do passado', passado + dt.timedelta(days=9))
meta('deste 1', este + dt.timedelta(days=1)); meta('deste 2', este + dt.timedelta(days=2))
URL = '/impulso/acompanhamento/'


def contexto(cliente=cs, url=URL):
    return cliente.get(url).context


print('Sem ciclo')
t('sem ciclo aberto: mês do calendário', ciclos.mes_em_conferencia() is None)
c = contexto()
t('tela no mês do calendário, sem aviso', c['minha']['inicio'] == este and not c['mes_atrasado'] and 'em conferência' not in cs.get(URL).content.decode())

print('Mês anterior ainda aberto')
ciclo = Ciclo.objects.create(nome='ZZ', inicio=retrasado, fim=proximo, criado_por=chefe)
m0 = CicloMes.objects.create(ciclo=ciclo, referencia=retrasado, status='FECHADO')
m1 = CicloMes.objects.create(ciclo=ciclo, referencia=passado)
m2 = CicloMes.objects.create(ciclo=ciclo, referencia=este)
m3 = CicloMes.objects.create(ciclo=ciclo, referencia=proximo)
t('fica no mais antigo não finalizado', ciclos.mes_em_conferencia() == m1)
c = contexto(); h = cs.get(URL).content.decode()
t('a tela mostra o mês do ciclo, não o do calendário', c['minha']['inicio'] == passado and c['mes_atrasado'], c['minha']['inicio'])
t('o ranking usa as metas desse mês', next(r for r in c['ranking'] if r['user'] == ana)['dados']['detalhes']['metas']['total'] == 1)
t('avisa e leva ao mês', 'em conferência' in h and f'/impulso/ciclos/mes/{m1.id}/' in h)
d = contexto(cs, f'{URL}{ana.id}/')
t('o detalhe da pessoa mostra o mesmo mês', d['atual']['inicio'] == passado and d['atual']['detalhes']['metas']['total'] == 1)
t('o colaborador vê o mesmo', contexto(ca)['minha']['inicio'] == passado)

print('Finalizar o mês')
ciclos.fechar_mes(m1, chefe)
t('passa para o próximo mês aberto', ciclos.mes_em_conferencia() == m2)
c = contexto(); h = cs.get(URL).content.decode()
t('tela no mês atual, sem aviso', c['minha']['inicio'] == este and not c['mes_atrasado'] and 'em conferência' not in h)
t('o ranking do mês finalizado está pronto', cs.get(f'/impulso/ciclos/mes/{m1.id}/ranking/').status_code == 200 and m1.pontuacoes.filter(user=ana).exists())
t('metas do mês atual na tela', next(r for r in c['ranking'] if r['user'] == ana)['dados']['detalhes']['metas']['total'] == 2)
ciclos.reabrir_mes(m1)
t('reabrir volta para a conferência dele', ciclos.mes_em_conferencia() == m1)
ciclos.fechar_mes(m1, chefe)

print('Limites')
ciclos.fechar_mes(m2, chefe)
t('mês que ainda não começou não entra', ciclos.mes_em_conferencia() is None and contexto()['minha']['inicio'] == este)
t('com a data avançada, ele entra', ciclos.mes_em_conferencia(hoje=proximo) == m3)
ciclo2 = Ciclo.objects.create(nome='ZZ novo', inicio=este, fim=proximo, criado_por=chefe)
m4 = CicloMes.objects.create(ciclo=ciclo2, referencia=este)
t('vale para o ciclo novo', ciclos.mes_em_conferencia() == m4)
Ciclo.objects.filter(pk=ciclo2.pk).update(status='ENCERRADO')
t('ciclo encerrado não prende a tela', ciclos.mes_em_conferencia() is None)

print('?mes= na tela da pessoa')
from decimal import Decimal as D
from impulso.models import PontuacaoMensal
Ciclo.objects.filter(pk=ciclo2.pk).delete()
U = f'{URL}{ana.id}/'
ctx = contexto(cs, f'{U}?mes={m1.id}')
h = cs.get(f'{U}?mes={m1.id}').content.decode()
linhas = {l['item']: l for l in ctx['linhas']}
t('o mês finalizado manda no cabeçalho e no item a item', ctx['atual']['inicio'] == passado and ctx['snapshot'].mes_id == m1.id and 'finalizado' in h, ctx['atual']['inicio'])
t('item a item é o daquele mês', linhas['Metas — conclusão']['info'] == '1 de 1 concluída(s)', linhas['Metas — conclusão'])
t('título do detalhe traz o mês', passado.strftime('%m') and 'item a item' in h)
# Depois de finalizar, mudar a meta não muda o que valeu.
Meta.objects.filter(titulo='do passado').update(status='A_FAZER')
ctx2 = contexto(cs, f'{U}?mes={m1.id}')
t('vem do que foi salvo, não da situação de hoje', {l['item']: l for l in ctx2['linhas']}['Metas — conclusão']['info'] == '1 de 1 concluída(s)')
Meta.objects.filter(titulo='do passado').update(status='CONCLUIDA')
p1 = PontuacaoMensal.objects.get(mes=m1, user=ana)
t('total e faixa são os salvos', ctx['atual']['total'] == p1.total and ctx['atual']['percentual'] == p1.percentual and ctx['atual_faixa']['label'] == ctx['snapshot_faixa']['label'])
t('os blocos somam o total', ctx['atual']['confiar'] + ctx['atual']['conectar'] + ctx['atual']['inovar'] == p1.total)
ciclos.ajustar_pontuacao(p1, {'p_feedback': '10'}, 'Teste de ajuste', chefe)
ctx3 = contexto(cs, f'{U}?mes={m1.id}')
t('ajuste manual aparece no detalhado', {l['item']: l for l in ctx3['linhas']}['Feedback do gestor']['pontos'] == D('10') and ctx3['ajuste'])
ctx4 = contexto(cs, f'{U}?mes={m2.id}')
t('outro mês finalizado mostra o dele', ctx4['atual']['inicio'] == este and {l['item']: l for l in ctx4['linhas']}['Metas — conclusão']['info'] == '2 de 2 concluída(s)')
ctx5 = contexto(cs, f'{U}?mes={m3.id}')
t('mês aberto: conta ao vivo dele, sem quadro de fechado', ctx5['atual']['inicio'] == proximo and ctx5['snapshot'] is None and 'em conferência' in cs.get(f'{U}?mes={m3.id}').content.decode())
ciclos.reabrir_mes(m1)
ctx6 = contexto(cs, f'{U}?mes={m1.id}')
t('mês reaberto volta à conta ao vivo', ctx6['snapshot'] is None and ctx6['atual']['inicio'] == passado)
ciclos.fechar_mes(m1, chefe)
t('sem parâmetro: mês em conferência', contexto(cs, U)['mes_escolhido'] is None and contexto(cs, U)['snapshot'] is None)
t('mês inexistente: ignora', cs.get(f'{U}?mes=99999').status_code == 200 and cs.get(f'{U}?mes=abc').status_code == 200)
outro = User.objects.create_user(username='zza.outro', email='zza.outro@exemplo-teste.local', password='S3nha!teste', first_name='Ou', last_name='ZZ', sector=setor, hierarchy='PADRAO')
outro.communication_groups.add(adm)
co = Client(); co.force_login(outro)
t('colega não abre o detalhe de outra pessoa pelo parâmetro', co.get(f'{U}?mes={m1.id}').status_code == 302)
t('a própria pessoa abre', ca.get(f'{U}?mes={m1.id}').status_code == 200)

print(f'\n{ok} OK, {fail} falha(s)')
