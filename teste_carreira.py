"""/progresso/ (25/11/2026): o caminho do consultor até Pleno — requisitos, pedido, decisão e certificado. Banco descartável."""
import io
import json
import os
import pathlib
import sys
import tempfile
import zipfile
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-carreira-'))
(PASTA / 'zz_carreira_settings.py').write_text(f"""
from redeconfianca.settings import *              # noqa: F401,F403

DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3', 'NAME': r'{PASTA}/banco.sqlite3'}}}}


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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-carr'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-carr-2'}},
}}
""")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_carreira_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import requests
from django.contrib.auth import get_user_model
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from carreira import regras
from carreira.models import Advertencia, ConfigCarreira, LojaApta, PedidoPromocao, PerfilCarreira
from core.models import Notification
from knowledge_trails.models import KnowledgeTrail, TrailProgress
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

# D-1 e ponto ficam de fora (MySQL e Tangerino): cada teste diz o número.
D1 = {'valor': (200, 6)}
PONTO = {'valor': {'pct': Decimal('1.20'), 'dias_sem_batida': 0, 'minutos_atraso': 25}}
mock.patch.object(regras, 'erro_d1', lambda user, hoje: D1['valor']).start()
mock.patch.object(regras, 'absenteismo', lambda user, hoje: PONTO['valor']).start()

HOJE = timezone.localdate()
loja = Sector.objects.create(name='Loja Glória')
escritorio = Sector.objects.create(name='Escritório')


def novo(u, **k):
    k.setdefault('hierarchy', 'PADRAO')
    return User.objects.create_user(username=f'zzc.{u}', email=f'zzc.{u}@exemplo-teste.local', password='S3nha!teste',
                                    first_name=u.title(), last_name='ZZ', **k)


sa = novo('chefe', hierarchy='SUPERADMIN')
ana = novo('ana', sector=loja, job_title='CONSULTOR DE VENDAS', admission_date=HOJE - timedelta(days=400))
beto = novo('beto', sector=loja, job_title='CONSULTOR DE VENDAS', admission_date=HOJE - timedelta(days=200))
adm = novo('adm', sector=escritorio)
csa, cana, cbeto, cadm = Client(), Client(), Client(), Client()
csa.force_login(sa); cana.force_login(ana); cbeto.force_login(beto); cadm.force_login(adm)

print('Quem vê')
t('SUPERADMIN vê o menu', 'Progresso' in csa.get('/').content.decode() and regras.pode_ver_modulo(sa))
r = csa.get('/progresso/')
t('SUPERADMIN abre direto no painel', r.status_code == 302 and r['Location'].endswith('/progresso/painel/'))
t('consultor não vê antes de liberar', cana.get('/progresso/').status_code == 302 and not regras.pode_ver_modulo(ana))
t('nem no menu', 'carreira:jornada' not in cana.get('/').content.decode() and '/progresso/' not in cana.get('/').content.decode())
cana.post('/progresso/painel/liberar/', {'liberado': '1'})
t('consultor não libera', not ConfigCarreira.atual().liberado)
r = csa.post('/progresso/painel/liberar/', {'liberado': '1'}, follow=True)
t('SUPERADMIN libera pelo botão', ConfigCarreira.atual().liberado and 'Liberado para o comercial' in r.content.decode())
t('consultor da loja passa a ver (menu e tela)', cana.get('/progresso/').status_code == 200 and '/progresso/' in cana.get('/').content.decode())
t('quem não é do comercial (escritório) continua sem ver', cadm.get('/progresso/').status_code == 302)

print('Requisitos')
j = regras.avaliar(ana)
req = {r['chave']: r for r in j['requisitos']}
t('8 requisitos, na ordem da tabela', [r['chave'] for r in j['requisitos']] ==
  ['tempo', 'resultado', 'satisfacao', 'qualidade', 'assiduidade', 'conduta', 'formacao', 'loja'])
t('tempo: 13 meses de casa cumpre', req['tempo']['situacao'] == 'ok' and req['tempo']['valor'] == '13 meses (12 exigidos)', req['tempo'])
jb = {r['chave']: r for r in regras.avaliar(beto)['requisitos']}
t('tempo: 6 meses ainda não, e diz quando completa', jb['tempo']['situacao'] == 'falta' and 'completa em' in jb['tempo']['dica']
  and 0.4 < jb['tempo']['progresso'] < 0.6, jb['tempo'])
p = regras.perfil_de(beto); p.ultima_promocao = HOJE - timedelta(days=30); p.save()
t('última promoção reinicia a contagem', regras.avaliar(beto)['requisitos'][0]['valor'].startswith('0 de 12')
  or regras.avaliar(beto)['requisitos'][0]['valor'].startswith('1 de 12'))
t('resultado e satisfação aguardam (comissionamento ainda não lido)', req['resultado']['situacao'] == 'aguardando' and req['satisfacao']['situacao'] == 'aguardando')
t('qualidade: 6 erros em 200 = 3% cumpre', req['qualidade']['situacao'] == 'ok' and '3,0%' in req['qualidade']['valor'], req['qualidade'])
D1['valor'] = (100, 8)
t('qualidade: 8% não cumpre', {r['chave']: r for r in regras.avaliar(ana)['requisitos']}['qualidade']['situacao'] == 'falta')
D1['valor'] = None
t('qualidade sem vendas no D-1 aguarda', {r['chave']: r for r in regras.avaliar(ana)['requisitos']}['qualidade']['situacao'] == 'aguardando')
D1['valor'] = (200, 6)
t('assiduidade: 1,2% cumpre', req['assiduidade']['situacao'] == 'ok')
PONTO['valor'] = {'pct': Decimal('3.5'), 'dias_sem_batida': 2, 'minutos_atraso': 40}
_a = {r['chave']: r for r in regras.avaliar(ana)['requisitos']}['assiduidade']
t('assiduidade: 3,5% não cumpre e mostra o detalhe do ponto', _a['situacao'] == 'falta'
  and '2 dia(s) sem batida' in _a['valor'] and '40 min' in _a['valor'], _a)
PONTO['valor'] = {'pct': Decimal('1.20'), 'dias_sem_batida': 0, 'minutos_atraso': 25}
t('conduta sem advertência cumpre', req['conduta']['situacao'] == 'ok')
adv = Advertencia.objects.create(user=ana, data=HOJE - timedelta(days=20), motivo='atraso', registrado_por=sa)
c = {r['chave']: r for r in regras.avaliar(ana)['requisitos']}['conduta']
t('advertência nos últimos 3 meses trava e diz quando libera', c['situacao'] == 'falta' and 'Libera em' in c['dica'], c)
adv.data = HOJE - timedelta(days=120); adv.save()
t('advertência antiga não conta', {r['chave']: r for r in regras.avaliar(ana)['requisitos']}['conduta']['situacao'] == 'ok')
t('formação aguarda o SUPERADMIN escolher as trilhas', req['formacao']['situacao'] == 'aguardando')
t('loja apta aguarda o comitê', req['loja']['situacao'] == 'aguardando')
# a tela do consultor
h = cana.get('/progresso/').content.decode()
t('a tela mostra os 8 com situação e barra', h.count('data-requisito=') == 8 and 'pg-barra' in h and 'data-anel' in h
  and 'Aguardando dado' in h and 'Conquistado' in h)
t('sem lançamentos de SUPERADMIN na tela do consultor', 'Lançar dados da jornada' not in h and 'data-decidir' not in h)

print('Lançamentos do SUPERADMIN')
r = cana.post(f'/progresso/pessoa/{ana.pk}/salvar/', {'resultado_media': '120'})
t('consultor não lança', regras.perfil_de(ana).resultado_media is None)
r = csa.post(f'/progresso/pessoa/{ana.pk}/salvar/', {'resultado_media': 'abc'}, follow=True)
t('número inválido não salva nada', regras.perfil_de(ana).resultado_media is None and 'Não salvei' in r.content.decode())
r = csa.post(f'/progresso/pessoa/{ana.pk}/salvar/', {'resultado_media': '104,5', 'resultado_referencia': 'jun–nov/26',
                                                     'satisfacao_nota': '4,1'})
pf = regras.perfil_de(ana)
t('SUPERADMIN lança resultado e satisfação', pf.resultado_media == Decimal('104.5') and pf.satisfacao_nota == Decimal('4.1'))
j = regras.avaliar(ana)
req = {r['chave']: r for r in j['requisitos']}
t('resultado 104,5% cumpre', req['resultado']['situacao'] == 'ok' and '104,5%' in req['resultado']['valor'])
t('satisfação 4,1 ainda não', req['satisfacao']['situacao'] == 'falta')
csa.post(f'/progresso/pessoa/{ana.pk}/salvar/', {'resultado_media': '104,5', 'satisfacao_nota': '4,6', 'qualidade_manual': '9'})
t('qualidade lançada à mão vale mais que o D-1', {r['chave']: r for r in regras.avaliar(ana)['requisitos']}['qualidade']['situacao'] == 'falta')
csa.post(f'/progresso/pessoa/{ana.pk}/salvar/', {'resultado_media': '104,5', 'satisfacao_nota': '4,6'})
trilha1 = KnowledgeTrail.objects.create(title='Trilha do consultor', description='x', sector=loja)
trilha2 = KnowledgeTrail.objects.create(title='Formação de responsável de loja', description='x', sector=loja)
csa.post('/progresso/painel/trilhas/', {'trilhas': [trilha1.pk, trilha2.pk]})
t('SUPERADMIN escolhe as trilhas', set(ConfigCarreira.atual().trilhas.values_list('pk', flat=True)) == {trilha1.pk, trilha2.pk})
TrailProgress.objects.create(trail=trilha1, user=ana, status='completed', completed_at=timezone.now())
f = {r['chave']: r for r in regras.avaliar(ana)['requisitos']}['formacao']
t('formação: 1 de 2 trilhas', f['situacao'] == 'falta' and f['valor'].startswith('1 de 2') and len(f['trilhas']) == 2)
TrailProgress.objects.create(trail=trilha2, user=ana, status='completed', completed_at=timezone.now())
ano, tri = regras.trimestre_de(HOJE)
r = csa.post('/progresso/painel/lojas/', {'ano': ano, 'tri': tri, f'apta_{loja.pk}': '1', f'rent_{loja.pk}': 'xx'}, follow=True)
t('rentabilidade inválida não salva nada', not LojaApta.objects.exists() and 'Nada salvo' in r.content.decode())
csa.post('/progresso/painel/lojas/', {'ano': ano, 'tri': tri, f'apta_{loja.pk}': '0', f'rent_{loja.pk}': '12,5'})
l = {r['chave']: r for r in regras.avaliar(ana)['requisitos']}['loja']
t('loja não apta: falta, sem mostrar o número ao consultor', l['situacao'] == 'falta' and '12,5' not in l['valor']
  and 'mudar de loja' in l['dica'], l)
l = {r['chave']: r for r in regras.avaliar(ana, para_superadmin=True)['requisitos']}['loja']
t('o SUPERADMIN vê a rentabilidade', '12,5%' in l['valor'], l)
t('nenhum pedido antes de concluir', not PedidoPromocao.objects.exists())
r = cana.get('/progresso/certificado/')
t('certificado só depois de concluir', r.status_code == 302)

print('Concluiu: pedido para o SUPERADMIN')
csa.post('/progresso/painel/lojas/', {'ano': ano, 'tri': tri, f'apta_{loja.pk}': '1', f'rent_{loja.pk}': '24'})
r = cana.get('/progresso/')
h = r.content.decode()
pedidos = PedidoPromocao.objects.filter(user=ana)
t('8 de 8: abre o pedido de promoção', r.context['concluiu'] and pedidos.count() == 1 and pedidos[0].situacao == 'ANALISE')
t('o pedido guarda os 8 requisitos', len(pedidos[0].requisitos) == 8 and all(x['situacao'] == 'ok' for x in pedidos[0].requisitos))
t('avisa o SUPERADMIN no sino', Notification.objects.filter(user=sa, title__icontains='promoção', related_url__contains=f'/progresso/pessoa/{ana.pk}/').exists())
t('a tela comemora (confete e parabéns) e diz que foi para análise', 'pgParabens' in h and 'pgConfete' in h
  and 'Enviado para análise do SUPERADMIN' in h and '100' in h)
cana.get('/progresso/')
t('abrir de novo não duplica o pedido', PedidoPromocao.objects.filter(user=ana).count() == 1
  and Notification.objects.filter(user=sa, title__icontains='promoção').count() == 1)
r = cana.get('/progresso/certificado/')
t('certificado da jornada', r.status_code == 200 and 'Certificado de Conquista' in r.content.decode()
  and 'Ana Zz' in r.content.decode() and 'window.print()' in r.content.decode())
t('outro consultor não abre o certificado dela', cbeto.get(f'/progresso/certificado/{ana.pk}/').status_code == 404)
h = csa.get('/progresso/painel/').content.decode()
t('o painel mostra o pedido com os requisitos', f'data-pedido="{pedidos[0].pk}"' in h and 'Loja apta' in h)

print('Decisão')
cana.post(f'/progresso/pedido/{pedidos[0].pk}/decidir/', {'decisao': 'PROMOVIDO'})
t('consultor não decide', PedidoPromocao.objects.get(pk=pedidos[0].pk).situacao == 'ANALISE')
csa.post(f'/progresso/pedido/{pedidos[0].pk}/decidir/', {'decisao': 'NAO_AGORA', 'parecer': ''})
t('"ainda não" exige parecer', PedidoPromocao.objects.get(pk=pedidos[0].pk).situacao == 'ANALISE')
csa.post(f'/progresso/pedido/{pedidos[0].pk}/decidir/', {'decisao': 'PROMOVIDO', 'parecer': 'Merecido'})
pf = PerfilCarreira.objects.get(user=ana)
t('promover: vira Pleno, com a data', PedidoPromocao.objects.get(pk=pedidos[0].pk).situacao == 'PROMOVIDO'
  and pf.nivel == 'PLENO' and pf.promovido_em and pf.ultima_promocao == HOJE)
t('a consultora recebe o aviso', Notification.objects.filter(user=ana, title__icontains='Pleno').exists())
h = cana.get('/progresso/').content.decode()
t('a tela vira a de Pleno', 'Promoção a Pleno aprovada' in h and 'pg-hero-pleno' in h)
t('certificado de promoção', 'Certificado de Promoção' in cana.get('/progresso/certificado/').content.decode())
t('Pleno não abre outro pedido', PedidoPromocao.objects.filter(user=ana).count() == 1)

print('"Ainda não"')
for k in ('resultado_media', 'satisfacao_nota'):
    setattr(regras.perfil_de(beto), k, None)
pb = regras.perfil_de(beto); pb.ultima_promocao = None; pb.resultado_media = Decimal('110'); pb.satisfacao_nota = Decimal('4.5'); pb.save()
beto.admission_date = HOJE - timedelta(days=500); beto.save()
TrailProgress.objects.create(trail=trilha1, user=beto, status='completed'); TrailProgress.objects.create(trail=trilha2, user=beto, status='completed')
cbeto.get('/progresso/')
pedido_b = PedidoPromocao.objects.get(user=beto)
csa.post(f'/progresso/pedido/{pedido_b.pk}/decidir/', {'decisao': 'NAO_AGORA', 'parecer': 'Vamos rever no próximo trimestre'})
h = cbeto.get('/progresso/').content.decode()
t('"ainda não" com parecer: o consultor lê', 'ainda não' in h and 'Vamos rever no próximo trimestre' in h)
t('e não abre outro pedido logo em seguida', PedidoPromocao.objects.filter(user=beto).count() == 1)

print('Painel')
h = csa.get('/progresso/painel/').content.decode()
t('o painel lista os consultores com o retrato', 'Ana ZZ' in h and 'Beto ZZ' in h and 'Adm ZZ' not in h and 'pp-pontos' in h)
t('consultor não abre o painel', cana.get('/progresso/painel/').status_code == 302)
h = csa.get(f'/progresso/pessoa/{beto.pk}/').content.decode()
t('SUPERADMIN abre a jornada da pessoa com os lançamentos', 'Lançar dados da jornada' in h and 'Advertências' in h)
csa.post(f'/progresso/pessoa/{beto.pk}/advertencia/', {'data': HOJE.isoformat(), 'motivo': 'Uniforme'})
t('registra advertência', Advertencia.objects.filter(user=beto, motivo='Uniforme').exists())
a = Advertencia.objects.get(user=beto, motivo='Uniforme')
csa.post(f'/progresso/pessoa/{beto.pk}/advertencia/', {'apagar': a.pk})
t('apaga advertência', not Advertencia.objects.filter(pk=a.pk).exists())
csa.post('/progresso/painel/liberar/', {'liberado': '0'})
t('esconder volta a tirar do comercial', cana.get('/progresso/').status_code == 302)


print('Regra do absenteísmo (ponto falso)')
mock.patch.stopall()
for nome in ('get', 'post', 'request'):
    mock.patch.object(requests, nome, sem_rede).start()
from django.core.cache import cache as _cache
H8 = 8 * 3600
LINHAS = [
    {'previsto_segundos': H8, 'trabalhado_segundos': 0, 'pendencias': ['Não houve nenhuma batida no dia']},   # falta: 8 h
    {'previsto_segundos': H8, 'trabalhado_segundos': H8 - 5 * 60, 'pendencias': []},                         # 5 min: tolerância
    {'previsto_segundos': H8, 'trabalhado_segundos': H8 - 30 * 60, 'pendencias': []},                        # atraso de 30 min
    {'previsto_segundos': H8, 'trabalhado_segundos': 3600, 'pendencias': ['Faltou a 2ª batida (saída)']},     # esquecida: fora
    {'previsto_segundos': 0, 'trabalhado_segundos': 0, 'pendencias': []},                                     # abonado/folga
] + [{'previsto_segundos': H8, 'trabalhado_segundos': H8, 'pendencias': []}] * 47
User.objects.filter(pk=ana.pk).update(tangerino_employee_id=999)
ana = User.objects.get(pk=ana.pk)
LINHAS = [{**l, 'usuario': ana} for l in LINHAS]
_cache.clear()   # LocMem deste teste
with mock.patch('tangerino.pendencias.linhas_do_periodo', return_value=LINHAS):
    a = regras.absenteismo(ana, HOJE)
# previsto: 50 dias x 8 h (a esquecida e o abonado saem); faltou: 8 h + 30 min
esperado = (Decimal((H8 + 30 * 60) * 100) / Decimal(50 * H8)).quantize(Decimal('0.01'))
t('falta conta o dia inteiro, atraso só acima de 10 min, esquecida e abono ficam fora',
  a and a['pct'] == esperado and a['dias_sem_batida'] == 1 and a['minutos_atraso'] == 30, (a, esperado))
User.objects.filter(pk=beto.pk).update(tangerino_employee_id=None)
t('sem ponto no Tangerino: aguarda', regras.absenteismo(User.objects.get(pk=beto.pk), HOJE) is None)


print('Resultado do comissionamento pós-contestação (26/11/2026)')
from types import SimpleNamespace
from carreira import resultado as res
from users import commission_views as cv


def linha(nome, pcts):
    """pcts: {pilar_chave: (pct_1, pct_2, pct_3)} em fração, como na planilha."""
    dados = {'NOME': nome}
    for chave, (p1, p2, p3) in pcts.items():
        dados[f'%ATING_{chave}_1'], dados[f'%ATING_{chave}_2'], dados[f'%ATING_{chave}_3'] = p1, p2, p3
    return {'nome': nome, 'data': dados}


TODOS = ('MOVEL', 'FIXA', 'SMART', 'ELETRO', 'ESSEN', 'SEG', 'SVA')
PLANILHAS = {
    'url-2026-09': [linha('Ana ZZ', {k: (1.10, 1.00, 0.50) for k in TODOS}),
                    linha('Fulano Sem Cadastro', {k: (2.0, 2.0, 2.0) for k in TODOS})],
    'url-2026-07': [linha('Ana ZZ', {**{k: (1.20, 0.90, 0.80) for k in TODOS}, 'SVA': (0, 0.90, 0.80)})],
}
versoes = [SimpleNamespace(pk=9, year=2026, month=9), SimpleNamespace(pk=7, year=2026, month=7)]
with mock.patch.object(cv, 'get_excel_urls', lambda y, m, p=None: {'comissao': f'url-{y}-{m:02d}'}), \
        mock.patch.object(cv, 'fetch_all_users_from_sheet',
                          lambda sheet, excel_url=None, cache_suffix=None: {'success': True, 'users': PLANILHAS[excel_url]}):
    pessoas = res.atingimentos_por_pessoa(versoes)
meses = pessoas[cv.normalize_person_name('ANA ZZ')]
t('cada mês usa a própria versão; o mês sem versão vem da seguinte', sorted(meses) == [(2026, 5), (2026, 6), (2026, 7), (2026, 8), (2026, 9)]
  and meses[(2026, 9)]['Móvel'] == 110.0 and meses[(2026, 8)]['Móvel'] == 100.0 and meses[(2026, 7)]['Móvel'] == 120.0
  and meses[(2026, 6)]['Móvel'] == 90.0, meses)
t('o 3º mês da versão de 09 não passa por cima da própria versão de 07', meses[(2026, 7)]['Fixa'] == 120.0)
t('pilar zerado no mês é "sem dado", não 0%', 'SVA' not in meses[(2026, 7)] and meses[(2026, 6)]['SVA'] == 90.0)
media, rotulo, pilares, quantos = res.media_da_pessoa(meses)
valores = [v for m in meses.values() for v in m.values()]
t('média de todos os atingimentos (pilar x mês)', media == Decimal(str(round(sum(valores) / len(valores), 2))) and quantos == 5
  and rotulo == 'mai–set/26', (media, rotulo))
t('média por pilar para mostrar na tela', [p['nome'] for p in pilares] == list(res.PILARES)
  and next(p for p in pilares if p['nome'] == 'SVA')['meses'] == 4, pilares)
t('sem meses, sem média', res.media_da_pessoa({}) is None)
jan = res.janela(versoes)
t('a janela são os 6 meses a partir da versão mais nova (abr–set/26)', jan == {(2026, m) for m in range(4, 10)}, jan)
t('mês fora da janela não entra na média', res.media_da_pessoa({(2026, 2): {'Móvel': 150.0}}, jan) is None
  and res.media_da_pessoa({(2026, 2): {'Móvel': 150.0}, (2026, 9): {'Móvel': 90.0}}, jan)[0] == Decimal('90.0'))
with mock.patch.object(res, 'atingimentos_por_pessoa', lambda versoes=None, falhas=None: pessoas), \
        mock.patch.object(res, 'janela', lambda versoes: {(2026, m) for m in range(4, 10)}):
    atualizados, sem_dado = res.atualizar_todos()
pa = PerfilCarreira.objects.get(user=ana)
t('grava no perfil de quem bate o nome (o resto fica sem dado)', atualizados == 1 and sem_dado >= 1
  and pa.resultado_auto == media and pa.resultado_auto_meses.startswith('mai–set/26') and pa.resultado_auto_em)
PerfilCarreira.objects.filter(user=ana).update(resultado_media=None)
r = {x['chave']: x for x in regras.avaliar(User.objects.get(pk=ana.pk))['requisitos']}['resultado']
t('sem lançamento manual, o Resultado vem do comissionamento', r['situacao'] == ('ok' if media >= 100 else 'falta')
  and 'mai–set/26' in r['valor'] and 'lançado' not in r['valor'] and len(r['pilares']) == 7, r)
PerfilCarreira.objects.filter(user=ana).update(resultado_media=Decimal('80'))
r = {x['chave']: x for x in regras.avaliar(User.objects.get(pk=ana.pk))['requisitos']}['resultado']
t('o lançado pelo SUPERADMIN vale mais', r['situacao'] == 'falta' and 'lançado pelo SUPERADMIN' in r['valor'])
PerfilCarreira.objects.filter(user=ana).update(resultado_media=None)
h = csa.get(f'/progresso/pessoa/{ana.pk}/').content.decode()
t('a tela mostra o atingimento de cada pilar', 'data-pilares' in h and 'Móvel <strong>' in h)
with mock.patch('carreira.resultado.atualizar_em_segundo_plano', return_value=True) as disparo:
    r = csa.post('/progresso/painel/resultado/', follow=True)
t('o painel tem o botão que relê o comissionamento', disparo.called and 'Lendo o comissionamento' in r.content.decode()
  and 'data-resultado' in r.content.decode())
with mock.patch('carreira.resultado.atualizar_em_segundo_plano') as disparo:
    cana.post('/progresso/painel/resultado/')
t('consultor não dispara a leitura', not disparo.called)
antes_auto = PerfilCarreira.objects.get(user=ana).resultado_auto
falhou = {'url-2026-09': {'error': 'o OneDrive respondeu com uma página'}}
with mock.patch.object(cv, 'get_excel_urls', lambda y, m, p=None: {'comissao': f'url-{y}-{m:02d}'}), \
        mock.patch.object(cv, 'fetch_all_users_from_sheet',
                          lambda sheet, excel_url=None, cache_suffix=None: falhou.get(excel_url) or {'success': True, 'users': PLANILHAS[excel_url]}), \
        mock.patch.object(res.time, 'sleep', lambda s: None), \
        mock.patch('users.models.CommissionSpreadsheetVersion.objects') as vobj:
    vobj.filter.return_value.exclude.return_value.order_by.return_value.__getitem__.return_value = versoes
    saida = res.atualizar_todos()
t('planilha que não baixa (mesmo tentando 3 vezes): não regrava nada', saida is None
  and PerfilCarreira.objects.get(user=ana).resultado_auto == antes_auto)
from core.agendador_diario import TAREFAS
t('roda sozinho todo dia (agendador diário)', any(c == 'carreira_resultado' and f == 'carreira.resultado.tarefa_diaria'
                                                 for c, _h, f, _d in TAREFAS))

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
