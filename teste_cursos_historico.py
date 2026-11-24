"""Cursos: histórico dos cursos encerrados e o analítico de cada um (23/11/2026).

Pedido: "em /cursos/ deve ter o histórico de todos os cursos que já foram
finalizados e o analítico de cada um".

"Finalizado" aqui é o curso publicado com o prazo vencido; concluir, para a
pessoa, é ter o comprovante aprovado.

Cobre:

- a lista do gestor mostra só os cursos encerrados (nem o aberto, nem o rascunho),
  com busca, tipo e período, e pagina;
- o colaborador vê o próprio histórico, com o próprio resultado, e não vê o
  curso de quem não é dele;
- os números do analítico num cenário pequeno: cobrados, aprovados, em
  conferência, não fez, no prazo, reenvio, tempo de conferência, loja a loja;
- quem entrou depois do prazo não conta; quem saiu depois de mandar conta;
- colaborador não abre o analítico; o CSV sai com uma linha por pessoa;
- as telas abrem (200) e os links aparecem em /cursos/ e no quadro.

Banco descartável (sqlite, schema direto dos models): nada toca o Postgres
compartilhado nem o S3.
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-crs-hist-'))
(PASTA / 'zz_crs_hist_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-crs-hist'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-crs-hist-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_crs_hist_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
os.environ.setdefault('RC_SEM_TAREFAS_DIARIAS', '1')

django.setup()

from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from datetime import datetime, time, timedelta

import requests
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from communications.models import CommunicationGroup
from cursos import views as cursos_views
from cursos.models import AtribuicaoCurso, Comprovante, ConfiguracaoCursos, Curso

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
antigo = hoje - timedelta(days=400)


def novo(u, primeiro, ultimo, **k):
    k.setdefault('hierarchy', 'PADRAO')
    return User.objects.create_user(username=f'zzch.{u}', email=f'zzch.{u}@exemplo-teste.local',
                                    password='S3nha!teste', first_name=primeiro, last_name=ultimo, **k)


def momento(dia, hora=10):
    return timezone.make_aware(datetime.combine(dia, time(hora, 0)))


def envio(curso, pessoa, quando, status=Comprovante.PENDENTE, revisor=None, horas_conferencia=None,
          observacao=''):
    c = Comprovante.objects.create(
        curso=curso, colaborador=pessoa, nome_original='comprovante.pdf', tamanho=10,
        arquivo=SimpleUploadedFile('comprovante.pdf', b'%PDF-1.4 teste', content_type='application/pdf'))
    campos = {'enviado_em': quando, 'status': status, 'observacao': observacao}
    if revisor:
        campos.update(revisado_por=revisor, revisado_em=quando + timedelta(hours=horas_conferencia))
    Comprovante.objects.filter(id=c.id).update(**campos)
    return c


# --- Cenário ---------------------------------------------------------------
gestor = novo('gestor', 'Gestora', 'Cursos')
grupo = CommunicationGroup.objects.create(name='ZZ Grupo Hist Cursos', created_by=gestor)
vend1 = novo('vend1', 'Ana', 'Lima', pdv='LOJA A', admission_date=antigo)
vend2 = novo('vend2', 'Bruno', 'Reis', pdv='LOJA A', admission_date=antigo)
vend3 = novo('vend3', 'Carla', 'Souza', pdv='LOJA B')               # sem admissão: vale o cadastro
User.objects.filter(id=vend3.id).update(date_joined=momento(antigo))
saiu = novo('saiu', 'Davi', 'Desligado', pdv='LOJA B', admission_date=antigo)
novato = novo('novato', 'Elias', 'Novato', pdv='LOJA A', admission_date=hoje - timedelta(days=1))
novato2 = novo('novato2', 'Fabi', 'Recente', pdv='LOJA B')          # cadastro de hoje, sem admissão
fora = novo('fora', 'Gil', 'Fora', pdv='ESCRITÓRIO', admission_date=antigo)
for u in (vend1, vend2, vend3, saiu, novato, novato2):
    u.communication_groups.add(grupo)

cfg = ConfiguracaoCursos.get()
cfg.grupos.set([grupo])
cfg.gestores.set([gestor])

prazo_foco = hoje - timedelta(days=10)
foco = Curso.objects.create(tipo=Curso.FOCO, titulo='ZZ Foco Encerrado', publicado=True,
                            competencia=prazo_foco.replace(day=1), prazo=prazo_foco, criado_por=gestor)
capac = Curso.objects.create(tipo=Curso.CAPACITACAO, titulo='ZZ Capacitação Encerrada', publicado=True,
                             prazo=hoje - timedelta(days=5), criado_por=gestor)
aberto = Curso.objects.create(tipo=Curso.FOCO, titulo='ZZ Foco Aberto', publicado=True,
                              prazo=hoje + timedelta(days=5), criado_por=gestor)
rascunho = Curso.objects.create(tipo=Curso.FOCO, titulo='ZZ Rascunho Vencido', publicado=False,
                                prazo=hoje - timedelta(days=3), criado_por=gestor)
AtribuicaoCurso.objects.create(curso=capac, colaborador=vend1, atribuido_por=gestor)
AtribuicaoCurso.objects.create(curso=capac, colaborador=vend2, atribuido_por=gestor)

# Foco encerrado:
#   Ana   — aprovada, mandou 2 dias antes do prazo, conferida em 2 h;
#   Bruno — recusado, reenviou 1 dia depois do prazo e espera conferência;
#   Carla — não mandou;
#   Davi  — mandou no prazo, aprovado em 4 h, e depois saiu da empresa;
#   Elias e Fabi entraram depois do prazo: não contam.
envio(foco, vend1, momento(prazo_foco - timedelta(days=2)), Comprovante.APROVADO, gestor, 2)
envio(foco, vend2, momento(prazo_foco - timedelta(days=3)), Comprovante.RECUSADO, gestor, 1,
      observacao='Arquivo ilegível')
envio(foco, vend2, momento(prazo_foco + timedelta(days=1)))
envio(foco, saiu, momento(prazo_foco), Comprovante.APROVADO, gestor, 4)
User.objects.filter(id=saiu.id).update(is_active=False)
# Capacitação encerrada: Ana aprovada, Bruno não mandou.
envio(capac, vend1, momento(capac.prazo - timedelta(days=1)), Comprovante.APROVADO, gestor, 30)
# O aberto tem envio também: não pode entrar no histórico por isso.
envio(aberto, vend1, momento(hoje))


def cliente(u):
    c = Client()
    c.force_login(u)
    return c


cg, c1, c2, c3, cn, cf = (cliente(u) for u in (gestor, vend1, vend2, vend3, novato, fora))

# --- Lista do gestor ---------------------------------------------------------
print('\n== HISTÓRICO DO GESTOR ==')
r = cg.get('/cursos/historico/')
html = r.content.decode()
t('gestor abre o histórico', r.status_code == 200, r.status_code)
t('modo "todos" para o gestor', r.context['modo'] == 'todos')
titulos = [i['curso'].titulo for i in r.context['itens']]
t('lista os dois cursos encerrados', set(titulos) == {'ZZ Foco Encerrado', 'ZZ Capacitação Encerrada'}, titulos)
t('não lista o curso em aberto', 'ZZ Foco Aberto' not in html)
t('não lista o rascunho vencido', 'ZZ Rascunho Vencido' not in html)
t('mais recente primeiro', titulos[:1] == ['ZZ Capacitação Encerrada'], titulos)
t('cada curso leva ao analítico', f'/cursos/historico/{foco.id}/' in html)
t('gestor tem a aba do próprio histórico', 'Meu histórico' in html)
resumo_foco = next(i['resumo'] for i in r.context['itens'] if i['curso'].id == foco.id)
t('o cartão do curso já traz a conclusão', resumo_foco['conclusao'] == 50, resumo_foco)

r = cg.get('/cursos/historico/', {'tipo': Curso.CAPACITACAO})
t('filtro por tipo', [i['curso'].id for i in r.context['itens']] == [capac.id])
r = cg.get('/cursos/historico/', {'q': 'foco enc'})
t('busca pelo título', [i['curso'].id for i in r.context['itens']] == [foco.id])
r = cg.get('/cursos/historico/', {'de': (hoje - timedelta(days=7)).isoformat()})
t('período: prazo a partir de', [i['curso'].id for i in r.context['itens']] == [capac.id])
r = cg.get('/cursos/historico/', {'ate': (hoje - timedelta(days=7)).isoformat()})
t('período: prazo até', [i['curso'].id for i in r.context['itens']] == [foco.id])
r = cg.get('/cursos/historico/', {'de': '31/99/2026', 'tipo': 'XYZ'})
t('filtro inválido é ignorado, não derruba', r.status_code == 200 and len(r.context['itens']) == 2)

r = cg.get('/cursos/historico/', {'ver': 'meu'})
t('gestor vê o próprio histórico (vazio)', r.status_code == 200 and r.context['modo'] == 'meu'
  and r.context['linhas'] == [])

# --- Histórico do colaborador -------------------------------------------------
print('\n== HISTÓRICO DO COLABORADOR ==')
r = c1.get('/cursos/historico/')
html = r.content.decode()
t('colaborador abre o histórico', r.status_code == 200, r.status_code)
t('colaborador fica sempre no modo "meu"', r.context['modo'] == 'meu')
t('não vê as abas do gestor', 'Todos os cursos' not in html)
meus = {l['curso'].id: l for l in r.context['linhas']}
t('Ana vê os dois cursos encerrados dela', set(meus) == {foco.id, capac.id}, list(meus))
t('não vê o curso em aberto', aberto.id not in meus)
t('resultado dela: concluído e no prazo', meus[foco.id]['situacao'] == 'APROVADO'
  and meus[foco.id]['no_prazo'] is True)
t('resumo: 2 de 2, 100%', r.context['meu']['aprovados'] == 2 and r.context['meu']['conclusao'] == 100,
  r.context['meu'])
t('a tela mostra "Concluído"', 'Concluído' in html)
t('?ver=todos não abre a visão do gestor', c1.get('/cursos/historico/', {'ver': 'todos'}).context['modo'] == 'meu')

r = c2.get('/cursos/historico/')
meus = {l['curso'].id: l for l in r.context['linhas']}
t('Bruno: em conferência, depois do prazo, 2 envios',
  meus[foco.id]['situacao'] == 'CONFERIR' and meus[foco.id]['no_prazo'] is False
  and meus[foco.id]['tentativas'] == 2, meus.get(foco.id))
t('Bruno: capacitação sem envio', meus[capac.id]['situacao'] == 'NAO_FEZ')
t('"depois do prazo" aparece para ele', 'depois do prazo' in r.content.decode())

r = c3.get('/cursos/historico/')
meus = {l['curso'].id: l for l in r.context['linhas']}
t('Carla vê o foco como não enviado', meus.get(foco.id, {}).get('situacao') == 'NAO_FEZ')
t('Carla não vê a capacitação que não é dela', capac.id not in meus)
t('a tela dela diz "Não enviado"', 'Não enviado' in r.content.decode())

r = cn.get('/cursos/historico/')
t('quem entrou depois do prazo não carrega o curso antigo',
  r.status_code == 200 and foco.id not in {l['curso'].id for l in r.context['linhas']})

r = cf.get('/cursos/historico/')
t('quem não enxerga o módulo é mandado para a home', r.status_code == 302 and r.url == '/', r.get('Location'))

# --- Analítico ---------------------------------------------------------------
print('\n== ANALÍTICO ==')
r = c1.get(f'/cursos/historico/{foco.id}/')
t('colaborador não abre o analítico (volta ao histórico)',
  r.status_code == 302 and r.url.endswith('/cursos/historico/'), r.get('Location'))
r = c1.get(f'/cursos/historico/{foco.id}/', {'formato': 'csv'})
t('nem o CSV do analítico', r.status_code == 302)

r = cg.get(f'/cursos/historico/{foco.id}/')
html = r.content.decode()
t('gestor abre o analítico', r.status_code == 200, r.status_code)
res = r.context['resumo']
nomes = {l['pessoa'].first_name: l for l in r.context['linhas']}
t('cobrados: Ana, Bruno, Carla e Davi (quem saiu depois de mandar conta)',
  set(nomes) == {'Ana', 'Bruno', 'Carla', 'Davi'}, sorted(nomes))
t('quem entrou depois do prazo não conta (admissão e cadastro)', 'Elias' not in nomes and 'Fabi' not in nomes)
t('total 4', res['total'] == 4, res)
t('2 aprovados, 1 em conferência, 0 recusado, 1 não fez',
  (res['aprovados'], res['conferir'], res['recusados'], res['nao_fez']) == (2, 1, 0, 1), res)
t('taxa de conclusão 50%', res['conclusao'] == 50, res['conclusao'])
t('entregaram 75%', res['adesao'] == 75, res['adesao'])
t('no prazo 2, depois 1, pontualidade 67%',
  (res['no_prazo'], res['depois_do_prazo'], res['pontualidade']) == (2, 1, 67), res)
t('1 pessoa precisou reenviar', res['reenvios'] == 1, res['reenvios'])
t('tempo médio de conferência 3 h (2 h e 4 h)', res['espera_media'] == '3 h' and res['conferidos'] == 2, res)
t('Bruno vale o último envio (o recusado ficou para trás)', nomes['Bruno']['situacao'] == 'CONFERIR')
lojas = {b['nome']: b for b in r.context['lojas']}
t('loja a loja: LOJA A 1/2, LOJA B 1/2',
  (lojas['LOJA A']['aprovados'], lojas['LOJA A']['total'], lojas['LOJA B']['aprovados'],
   lojas['LOJA B']['total']) == (1, 2, 1, 2), lojas)
t('quem não concluiu vem primeiro na lista', r.context['linhas'][0]['situacao'] == 'NAO_FEZ')
t('a tela marca o desligado', '(desligado)' in html)
t('a tela traz o filtro por pessoa', 'anBusca' in html and 'data-an-filtro="FALTOU"' in html)
t('curso encerrado não mostra aviso de parcial', 'números são parciais' not in html)

r = cg.get(f'/cursos/historico/{capac.id}/')
res = r.context['resumo']
t('capacitação: só os atribuídos (2), 50%', res['total'] == 2 and res['conclusao'] == 50, res)
t('capacitação: conferência média de 30 h', res['espera_media'] == '30 h', res['espera_media'])

r = cg.get(f'/cursos/historico/{aberto.id}/')
t('curso em aberto abre com o aviso de números parciais',
  r.status_code == 200 and 'números são parciais' in r.content.decode())
t('rascunho não tem analítico (404)', cg.get(f'/cursos/historico/{rascunho.id}/').status_code == 404)
t('curso inexistente dá 404', cg.get('/cursos/historico/999999/').status_code == 404)

r = cg.get(f'/cursos/historico/{foco.id}/', {'formato': 'csv'})
corpo = r.content.decode('utf-8-sig')
linhas_csv = [l for l in corpo.splitlines() if l.strip()]
t('CSV do analítico sai', r.status_code == 200 and r['Content-Type'].startswith('text/csv'), r.status_code)
t('CSV: cabeçalho + 4 pessoas', len(linhas_csv) == 5, linhas_csv)
t('CSV: Bruno com 2 envios, depois do prazo',
  any('Bruno' in l and 'Em conferência' in l and ';Não;' in l and l.endswith(';2') for l in linhas_csv),
  linhas_csv)

# --- Links e paginação ---------------------------------------------------------
print('\n== LINKS E PAGINAÇÃO ==')
t('/cursos/ tem o botão do histórico', '/cursos/historico/' in c1.get('/cursos/').content.decode())
html = cg.get('/cursos/gestao/', {'curso': foco.id}).content.decode()
t('o quadro tem o histórico e o analítico do curso',
  '/cursos/historico/' in html and f'/cursos/historico/{foco.id}/' in html)

for i in range(25):
    Curso.objects.create(tipo=Curso.FOCO, titulo=f'ZZ Antigo {i:02d}', publicado=True,
                         prazo=hoje - timedelta(days=60 + i), criado_por=gestor)
r = cg.get('/cursos/historico/')
t('pagina de 20 em 20', len(r.context['itens']) == 20 and r.context['total_cursos'] == 27,
  (len(r.context['itens']), r.context['total_cursos']))
r = cg.get('/cursos/historico/', {'pagina': 2, 'tipo': Curso.FOCO})
t('a página 2 mantém o filtro', r.status_code == 200 and len(r.context['itens']) == 6
  and 'tipo=FOCO' in r.context['parametros'], len(r.context['itens']))

# --- Peças soltas ------------------------------------------------------------
print('\n== PEÇAS ==')
t('duração: minutos', cursos_views._duracao_legivel(600) == '10 min')
t('duração: horas', cursos_views._duracao_legivel(5 * 3600) == '5 h')
t('duração: dias', cursos_views._duracao_legivel(3 * 86400) == '3,0 dias')
t('duração: sem conferência', cursos_views._duracao_legivel(None) == '')

print(f'\n{ok} ok, {fail} falha(s)')
sys.exit(1 if fail else 0)
