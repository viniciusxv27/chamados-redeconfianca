"""/impulso/ciclos/mes/<id>/ e /ranking (05/11/2026): a mesma ordem nas duas telas e o
botão do SUPERADMIN "Recalcular pontos" do mês fechado. Banco descartável; a conta
(calcular_pontuacao) é dublada para dar números conhecidos.
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-recalc-'))
(PASTA / 'zz_recalc_settings.py').write_text(f"""
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
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_recalc_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

import re
from datetime import date
from decimal import Decimal as D

import requests

from impulso import ciclos
from impulso.models import Ciclo, CicloMes, PontuacaoMensal
from impulso.utils import faixa_por_score
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
        print(f'  FALHA {nome} {str(extra)[:500]}')


for nome_m in ('get', 'post', 'request'):
    mock.patch.object(requests, nome_m, lambda *a, **k: (_ for _ in ()).throw(AssertionError('rede'))).start()

loja_a = Sector.objects.create(name='Loja A')
loja_b = Sector.objects.create(name='Loja B')


def novo(nome, **kw):
    return User.objects.create_user(username=f'zzr.{nome}', email=f'zzr.{nome}@exemplo-teste.local', password='S3nha!teste',
                                    first_name=nome.title(), last_name='ZZ', sector=loja_a, **kw)


boss = novo('boss', hierarchy='SUPERADMIN')
gestor = novo('gestor', hierarchy='ADMIN')
ana, bia, caio, davi = (novo(n) for n in ('ana', 'bia', 'caio', 'davi'))
pessoas = [ana, bia, caio, davi]

# Notas por pessoa: (pontos de metas_qualidade, ajustes de ponto). Ana e Bia empatam em 80%;
# Bia tem menos ajustes, então vem antes. Caio 80% também, com mais ajustes. Davi 50%.
NOTAS = {ana.pk: (D('8'), 3), bia.pk: (D('8'), 1), caio.pk: (D('8'), 5), davi.pk: (D('5'), 0)}


def conta(user, inicio=None, fim=None, referencia=None):
    pontos, ajustes = NOTAS.get(user.pk, (D('0'), 0))
    campos = {c: D('0') for c, _, _ in ciclos.CAMPOS_PONTOS}
    campos['p_metas_qualidade'] = pontos
    pct = (pontos / D('10') * 100).quantize(D('0.01'))
    return {**campos, 'total': pontos, 'aplicavel': D('10'), 'percentual': pct, 'faixa': faixa_por_score(pct),
            'detalhes': {'assiduidade': {'fonte': 'ponto', 'total_ajustes': ajustes},
                         'metas': {'concluidas': 2}, 'inovar': {'aprovadas': 0}}}


mock.patch('impulso.ciclos.calcular_pontuacao', side_effect=conta).start()
colaboradores = mock.patch('impulso.ciclos.get_colaboradores', return_value=pessoas).start()

ciclo = Ciclo.objects.create(nome='ZZ Ciclo', inicio=date(2026, 10, 1), fim=date(2026, 12, 31), criado_por=gestor)
mes = CicloMes.objects.create(ciclo=ciclo, referencia=date(2026, 10, 1))
ciclos.fechar_mes(mes, gestor)
mes.refresh_from_db()
t('o fechamento (refatorado) grava todo mundo', PontuacaoMensal.objects.filter(mes=mes).count() == 4 and mes.is_fechado)

cb = Client(); cb.force_login(boss)
cg = Client(); cg.force_login(gestor)


def nomes(html, marca):
    return re.findall(marca, html)


print('\n== MESMA ORDEM NAS DUAS TELAS ==')
det = cb.get(f'/impulso/ciclos/mes/{mes.id}/').content.decode()
tabela = re.findall(r'detalhe_colaborador[^"]*|/impulso/[^"]*colaborador/(\d+)/', det)
ids_tabela = [int(x) for x in re.findall(r'/impulso/acompanhamento/(\d+)/\?mes=', det)] or \
    [int(x) for x in re.findall(r'/(\d+)/\?mes=' + str(mes.id), det)]
t('a tabela segue a pontuação e o desempate', ids_tabela == [bia.pk, ana.pk, caio.pk, davi.pk], (ids_tabela, [bia.pk, ana.pk, caio.pk, davi.pk]))
rk = cb.get(f'/impulso/ciclos/mes/{mes.id}/ranking/')
ordem_rk = [l['user'].pk for l in rk.context['linhas']]
t('o ranking tem a mesma ordem', ordem_rk == ids_tabela, ordem_rk)
t('e a tabela do mês também (contexto)', [i['user'].pk for i in cb.get(f'/impulso/ciclos/mes/{mes.id}/').context['pontuacoes']] == ordem_rk)

print('\n== BOTÃO ==')
h = rk.content.decode()
t('o SUPERADMIN vê "Recalcular pontos"', 'Recalcular pontos' in h and f'/impulso/ciclos/mes/{mes.id}/recalcular/' in h)
t('o gestor não vê', 'Recalcular pontos' not in cg.get(f'/impulso/ciclos/mes/{mes.id}/ranking/').content.decode())
r = cg.post(f'/impulso/ciclos/mes/{mes.id}/recalcular/', follow=True)
t('e não consegue pelo POST', PontuacaoMensal.objects.get(mes=mes, user=davi).percentual == D('50.00'))
t('GET não recalcula', cb.get(f'/impulso/ciclos/mes/{mes.id}/recalcular/').status_code == 405)

print('\n== RECALCULAR ==')
p_davi = PontuacaoMensal.objects.get(mes=mes, user=davi)
ciclos.ajustar_pontuacao(PontuacaoMensal.objects.get(mes=mes, user=caio), {'p_feedback': '2'}, 'Feedback esquecido', boss)
User.objects.filter(pk=ana.pk).update(sector=loja_b)          # trocou de loja depois do fechamento
eva = novo('eva')                                              # entrou no grupo depois
colaboradores.return_value = pessoas + [eva]
NOTAS[eva.pk] = (D('10'), 0)
NOTAS[davi.pk] = (D('9'), 0)                                   # aprovação que entrou depois do fechamento
fechado_em = mes.fechado_em
r = cb.post(f'/impulso/ciclos/mes/{mes.id}/recalcular/', follow=True)
h = r.content.decode()
mes.refresh_from_db()
p_davi.refresh_from_db()
t('Davi foi recalculado: 50% → 90%, faixa e C$ junto', p_davi.percentual == D('90.00') and p_davi.faixa == faixa_por_score(D('90')), p_davi.percentual)
t('a mensagem diz quem mudou', 'recalculados para 4 colaborador(es)' in h and 'Davi ZZ: 50% → 90%' in h, re.findall(r'Pontos de[^<]*', h))
t('ajuste manual continua valendo e é dito', PontuacaoMensal.objects.get(mes=mes, user=caio).p_feedback == D('2.00')
  and '1 ajuste(s) manual(is) continuam valendo' in h)
t('quem entrou depois não cai no mês fechado', not PontuacaoMensal.objects.filter(mes=mes, user=eva).exists())
t('o setor do fechamento é mantido', PontuacaoMensal.objects.get(mes=mes, user=ana).setor_id == loja_a.pk)
t('o mês continua fechado, com a data do fechamento', mes.is_fechado and mes.fechado_em == fechado_em)
t('fica registrado quem e quando recalculou', mes.recalculado_por_id == boss.pk and mes.recalculado_em)
t('o ranking diz que foi recalculado', 'recalculado em' in h)
t('a tela do mês também', 'Pontos recalculados em' in cb.get(f'/impulso/ciclos/mes/{mes.id}/').content.decode())
t('a nova ordem já vale (Caio 100% pelo ajuste, Davi 90%)', [l['user'].pk for l in r.context['linhas']][:2] == [caio.pk, davi.pk],
  [(l['user'].first_name, l['p'].percentual) for l in r.context['linhas']])

r = cb.post(f'/impulso/ciclos/mes/{mes.id}/recalcular/', follow=True)
t('recalcular de novo sem mudança diz isso', 'Ninguém mudou de pontuação' in r.content.decode())

print('\n== QUANDO NÃO PODE ==')
ciclos.reabrir_mes(mes)
t('mês aberto: sem botão', 'Recalcular pontos' not in cb.get(f'/impulso/ciclos/mes/{mes.id}/ranking/').content.decode())
r = cb.post(f'/impulso/ciclos/mes/{mes.id}/recalcular/', follow=True)
t('e o POST recusa', 'O mês está aberto' in r.content.decode())
ciclos.fechar_mes(mes, gestor)
Ciclo.objects.filter(pk=ciclo.pk).update(confiancas_creditadas=True)
t('ciclo pago: sem botão', 'Recalcular pontos' not in cb.get(f'/impulso/ciclos/mes/{mes.id}/ranking/').content.decode())
NOTAS[davi.pk] = (D('1'), 0)
r = cb.post(f'/impulso/ciclos/mes/{mes.id}/recalcular/', follow=True)
t('e o POST recusa sem mexer', 'C$ já foram creditadas' in r.content.decode()
  and PontuacaoMensal.objects.get(mes=mes, user=davi).percentual == D('90.00'))
Ciclo.objects.filter(pk=ciclo.pk).update(confiancas_creditadas=False)

print('\n== REABERTO NO MEIO DA CONTA ==')
def conta_e_reabre(user, **kw):
    CicloMes.objects.filter(pk=mes.pk).update(status=CicloMes.Status.ABERTO)
    return conta(user, **kw)
with mock.patch('impulso.ciclos.calcular_pontuacao', side_effect=conta_e_reabre):
    try:
        ciclos.recalcular_mes_fechado(mes, boss)
        t('reaberto durante a conta: não grava', False)
    except ciclos.AjusteRecusado:
        t('reaberto durante a conta: não grava', PontuacaoMensal.objects.get(mes=mes, user=davi).percentual == D('90.00'))

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
