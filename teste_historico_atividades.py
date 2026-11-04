"""/activity-history/: limpeza em lotes, linha do tempo, antes/depois e ligação com o mapa. Banco descartável; nada sai para a rede."""
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-historico-'))
(PASTA / 'zz_historico_settings.py').write_text(f"""
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
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_historico_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

import csv
import datetime as dt
import io
import json
import socket

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core import historico
from core.models import LimpezaHistorico, SystemLog
from maps.models import ConfiguracaoMapa, PosicaoRegistrada
from maps.servicos import posicao_no_momento, posicoes_perto, ultima_posicao
from tangerino.models import RegistroPontoPortal
from users.models import Sector, UserChangeLog

# Bloqueio de rede: sqlite não usa socket, então qualquer conexão aqui é vazamento
# (Z-API, Resend e MinIO têm credencial de verdade neste ambiente).
def _sem_rede(*a, **k):
    raise AssertionError(f'tentou sair para a rede: {a!r}')


socket.create_connection = _sem_rede
socket.socket.connect = _sem_rede

assert connection.vendor == 'sqlite', 'este teste apaga registros: só em banco descartável'
assert 'zz-historico-' in str(settings.DATABASES['default']['NAME'])

User = get_user_model()
ok = falhas = 0


def t(nome, condicao, detalhe=''):
    global ok, falhas
    if condicao:
        ok += 1
    else:
        falhas += 1
        print(f'  FALHOU: {nome} {detalhe}')


def cliente(user=None):
    c = Client()
    if user:
        c.force_login(user)
    return c


def texto(resposta):
    return resposta.content.decode('utf-8')


def novo_log(user=None, acao='USER_LOGIN', descricao='x', quando=None, ip=None):
    log = SystemLog.objects.create(user=user, action_type=acao, description=descricao, ip_address=ip)
    if quando:
        SystemLog.objects.filter(pk=log.pk).update(created_at=quando)
        log.refresh_from_db()
    return log


def em_lote(quantos, quando, acao='USER_LOGIN'):
    SystemLog.objects.bulk_create([SystemLog(action_type=acao, description=f'antigo {i}') for i in range(quantos)])
    ids = list(SystemLog.objects.filter(description__startswith='antigo ', created_at__gt=quando)
               .values_list('id', flat=True))
    SystemLog.objects.filter(id__in=ids).update(created_at=quando)


def limpar_tudo():
    SystemLog.objects.all().delete()
    LimpezaHistorico.objects.all().delete()
    PosicaoRegistrada.objects.all().delete()
    RegistroPontoPortal.objects.all().delete()
    UserChangeLog.objects.all().delete()


def postar(c, url, corpo=None):
    return c.post(url, data=json.dumps(corpo or {}), content_type='application/json')


loja = Sector.objects.create(name='Loja Centro')
chefe = User.objects.create_user(username='chefe', email='chefe@t.local', password='x',
                                 first_name='Chefe', last_name='Hierarquia', hierarchy='SUPERADMIN', sector=loja)
raiz = User.objects.create_superuser(username='raiz', email='raiz@t.local', password='x',
                                     first_name='Raiz', last_name='Super')
outro_chefe = User.objects.create_user(username='chefe2', email='chefe2@t.local', password='x',
                                       first_name='Outra', last_name='Chefe', hierarchy='SUPERADMIN')
comum = User.objects.create_user(username='comum', email='comum@t.local', password='x',
                                 first_name='Ana', last_name='Comum', hierarchy='PADRAO', sector=loja)
ex = User.objects.create_user(username='ex', email='ex@t.local', password='x',
                              first_name='Ex', last_name='Colaborador', is_active=False)

URL = reverse('core:activity_history')
URL_CSV = reverse('core:export_activity_history')
URL_LIMPAR = reverse('core:limpar_historico')
URL_INICIAR = reverse('core:limpar_historico_iniciar')
URL_MAPA = reverse('maps:mapa')
URL_API = reverse('maps:api_posicoes')


def url_lote(i):
    return reverse('core:limpar_historico_lote', args=[i])


agora = timezone.now()
corte = historico.corte_da_limpeza()
corte_txt = timezone.localtime(corte).strftime('%Y-%m-%d')
CONFIRMA = {'confirmacao': 'LIMPAR', 'corte': corte_txt}

print('1) Quem entra')
limpar_tudo()
t('anônimo vai para o login', cliente().get(URL).status_code == 302)
r = cliente(comum).get(URL)
t('colaborador comum não entra', r.status_code == 302 and r.url == reverse('home'), r.status_code)
t('colaborador comum não exporta', cliente(comum).get(URL_CSV).status_code == 302)
t('colaborador comum não vê a prévia', cliente(comum).get(URL_LIMPAR).status_code == 302)
t('SUPERADMIN por hierarquia entra (o menu já oferecia)', cliente(chefe).get(URL).status_code == 200)
t('superuser entra', cliente(raiz).get(URL).status_code == 200)
t('o botão de limpar aparece para SUPERADMIN', 'ahBotaoLimpar' in texto(cliente(chefe).get(URL)))
with mock.patch('core.historico.pode_limpar_historico', return_value=False):
    t('o botão de limpar some sem a permissão própria', 'ahBotaoLimpar' not in texto(cliente(chefe).get(URL)))

print('2) Prévia não apaga nada e conta certo')
limpar_tudo()
velho1 = novo_log(comum, descricao='velho 1', quando=corte - dt.timedelta(days=40))
velho2 = novo_log(comum, descricao='velho 2', quando=corte - dt.timedelta(seconds=1))
borda = novo_log(comum, descricao='exatamente no corte', quando=corte)
recente = novo_log(comum, descricao='recente', quando=agora - dt.timedelta(days=10))
quase = novo_log(comum, descricao='364 dias', quando=agora - dt.timedelta(days=364))
limpeza_antiga = novo_log(chefe, acao=historico.ACAO_LIMPEZA, descricao='limpeza de antes',
                          quando=corte - dt.timedelta(days=400))
c = cliente(chefe)
antes = SystemLog.objects.count()
r = c.get(URL_LIMPAR)
h = texto(r)
t('prévia abre', r.status_code == 200)
t('prévia não apaga', SystemLog.objects.count() == antes)
t('prévia conta só o que passou de 1 ano', r.context['previa']['total'] == 2, r.context['previa']['total'])
t('prévia mostra o mais antigo', r.context['previa']['mais_antigo'] == velho1.created_at)
t('prévia mostra o corte', timezone.localtime(corte).strftime('%d/%m/%Y') in h)
t('a data do mais antigo está na tela', timezone.localtime(velho1.created_at).strftime('%d/%m/%Y') in h)
t('botão nasce desligado até digitar', 'id="ahExecutar" type="button" disabled' in h)

print('3) Sem confirmação explícita nada sai')
antes = historico.registros_a_limpar(corte).count()
t('sem palavra: 400', postar(c, URL_INICIAR, {'corte': corte_txt}).status_code == 400)
t('palavra errada: 400', postar(c, URL_INICIAR, {'confirmacao': 'limpar', 'corte': corte_txt}).status_code == 400)
t('corpo inválido: 400', c.post(URL_INICIAR, data='{{{', content_type='application/json').status_code == 400)
t('prévia de outro dia: 409', postar(c, URL_INICIAR, {'confirmacao': 'LIMPAR', 'corte': '2020-01-01'}).status_code == 409)
t('GET não inicia', c.get(URL_INICIAR).status_code == 405)
t('GET não apaga lote', c.get(url_lote(1)).status_code == 405)
t('lote de limpeza inexistente: 409', postar(c, url_lote(999)).status_code == 409)
t('nada saiu', historico.registros_a_limpar(corte).count() == antes)
t('nenhuma limpeza aberta', LimpezaHistorico.objects.count() == 0)
t('comum: iniciar 403', postar(cliente(comum), URL_INICIAR, CONFIRMA).status_code == 403)
t('comum: lote 403', postar(cliente(comum), url_lote(1)).status_code == 403)
t('nada saiu depois do comum', historico.registros_a_limpar(corte).count() == antes)
csrf = Client(enforce_csrf_checks=True)
csrf.force_login(chefe)
t('sem CSRF: 403', postar(csrf, URL_INICIAR, CONFIRMA).status_code == 403)

print('4) Limpeza em lotes')
em_lote(4500, corte - dt.timedelta(days=5))
previsto = historico.previa_da_limpeza()['total']
t('prévia com volume', previsto == 4502, previsto)
r = postar(c, URL_INICIAR, CONFIRMA)
d = r.json()
t('confirmado, inicia', r.status_code == 200 and d['previsto'] == 4502, d)
lid = d['id']
limpeza = LimpezaHistorico.objects.get(pk=lid)
t('registra quem', limpeza.executado_por_id == chefe.id and limpeza.executado_por_nome == 'Chefe Hierarquia')
t('registra quando', limpeza.iniciado_em is not None and limpeza.concluido_em is None)
t('registra o período', limpeza.corte == corte and limpeza.mais_antigo == velho1.created_at)
marca = SystemLog.objects.get(pk=limpeza.id_registro)
t('o rastro existe antes da primeira linha sair',
  marca.action_type == historico.ACAO_LIMPEZA and marca.user_id == chefe.id and 'em andamento' in marca.description,
  marca.description)
t('iniciar ainda não apagou', historico.registros_a_limpar(corte).count() == 4502)

with CaptureQueriesContext(connection, ) as ctx:
    r = postar(c, url_lote(lid))
d = r.json()
t('1º lote tira 2000', d['lote'] == 2000 and d['removidos'] == 2000 and not d['concluido'], d)
apagamentos = [q['sql'] for q in ctx.captured_queries if q['sql'].upper().startswith('DELETE') and 'core_systemlog' in q['sql']]
t('um DELETE só por requisição', len(apagamentos) == 1, len(apagamentos))
t('o DELETE vai por id, com limite', '"id" IN' in apagamentos[0] and apagamentos[0].count(',') < 2000)
t('o texto do log acompanha', '2000 de 4502' in SystemLog.objects.get(pk=limpeza.id_registro).description)
t('resto segue no banco', historico.registros_a_limpar(corte).count() == 2502)

r2 = postar(cliente(outro_chefe), URL_INICIAR, CONFIRMA)
t('outra pessoa não abre limpeza ao mesmo tempo', r2.status_code == 409 and 'Chefe Hierarquia' in r2.json()['erro'], r2.content)
t('outra pessoa não toca a limpeza alheia', postar(cliente(outro_chefe), url_lote(lid)).status_code == 409)

d = postar(c, url_lote(lid)).json()
t('2º lote', d['lote'] == 2000 and not d['concluido'], d)
d = postar(c, url_lote(lid)).json()
t('3º lote termina', d['lote'] == 502 and d['removidos'] == 4502 and d['concluido'], d)
limpeza.refresh_from_db()
t('conclui com a contagem', limpeza.situacao == 'CONCLUIDA' and limpeza.removidos == 4502 and limpeza.concluido_em)
marca.refresh_from_db()
t('o log diz quem e quantos', marca.user_id == chefe.id and '4502 de 4502' in marca.description and 'concluída' in marca.description, marca.description)
t('exatamente no corte fica', SystemLog.objects.filter(pk=borda.pk).exists())
t('recente fica', SystemLog.objects.filter(pk=recente.pk).exists())
t('364 dias fica', SystemLog.objects.filter(pk=quase.pk).exists())
t('1 segundo antes do corte sai', not SystemLog.objects.filter(pk=velho2.pk).exists())
t('limpeza antiga nunca sai', SystemLog.objects.filter(pk=limpeza_antiga.pk).exists())
t('registro desta limpeza fica', SystemLog.objects.filter(pk=marca.pk).exists())
t('lote depois de pronto: 409', postar(c, url_lote(lid)).status_code == 409)
t('sem mais o que limpar: 400', postar(c, URL_INICIAR, CONFIRMA).status_code == 400)
h = texto(cliente(chefe).get(URL_LIMPAR))
t('tela mostra a limpeza feita', 'Chefe Hierarquia' in h and '4502 de 4502' in h and 'Concluída' in h)
t('tela sem o que limpar não oferece o botão', 'ahExecutar' not in h)
t('linha do tempo mostra a limpeza', 'Limpeza do histórico' in texto(cliente(chefe).get(URL)))

print('5) Limpeza interrompida e travas')
limpar_tudo()
em_lote(2100, corte - dt.timedelta(days=5))
d = postar(c, URL_INICIAR, CONFIRMA).json()
primeira = d['id']
postar(c, url_lote(primeira))
t('recomeçar pela mesma pessoa fecha a anterior', postar(c, URL_INICIAR, CONFIRMA).status_code == 200)
anterior = LimpezaHistorico.objects.get(pk=primeira)
t('a anterior fica como interrompida, com o que tirou',
  anterior.situacao == 'INTERROMPIDA' and anterior.removidos == 2000, anterior.situacao)
t('o log da anterior também diz', 'interrompida' in SystemLog.objects.get(pk=anterior.id_registro).description)
aberta = LimpezaHistorico.objects.filter(situacao='ANDAMENTO').get()
LimpezaHistorico.objects.filter(pk=aberta.pk).update(atualizado_em=timezone.now() - dt.timedelta(minutes=10))
r = postar(cliente(outro_chefe), URL_INICIAR, CONFIRMA)
t('parada há muito não bloqueia os outros', r.status_code == 200, r.content)
adulterada = LimpezaHistorico.objects.get(pk=r.json()['id'])
LimpezaHistorico.objects.filter(pk=adulterada.pk).update(corte=timezone.now())
adulterada.refresh_from_db()
novo = novo_log(comum, descricao='de hoje')
try:
    historico.apagar_um_lote(adulterada)
    t('corte adulterado é recusado', False)
except ValueError:
    t('corte adulterado é recusado', True)
t('e nada recente sai', SystemLog.objects.filter(pk=novo.pk).exists())
t('corte em 29/02', historico.corte_da_limpeza(dt.date(2028, 2, 29)).date() == dt.date(2027, 2, 28))
t('corte comum é meia-noite de um ano atrás',
  timezone.localtime(historico.corte_da_limpeza(dt.date(2026, 11, 4))).strftime('%Y-%m-%d %H:%M') == '2025-11-04 00:00')

print('6) Antes e depois')
casos = [
    ('Status do chamado #699 alterado: ABERTO → EM_ANDAMENTO', 'Status do chamado #699', [('Status', 'Aberto', 'Em andamento')]),
    ('Status do resgate #303 alterado de APROVADO para ENTREGUE', 'Status do resgate #303', [('Status', 'Aprovado', 'Entregue')]),
    ('Prioridade do chamado #1898 alterada para Crítica', 'Prioridade do chamado #1898', [('Prioridade', None, 'Crítica')]),
    ('Setor editado: teste → Compras', 'Setor editado', [('Nome', 'teste', 'Compras')]),
    ('Aprovador atualizado: LUCAS FERRAZ - Ordem 1→3 - R$ 1000.00→100000.0', 'Aprovador atualizado: LUCAS FERRAZ',
     [('Ordem', '1', '3'), ('Valor máximo (R$)', '1000.00', '100000.0')]),
    ('Chamado #6988 editado: title: Compra - Água → Compra IGOR; status: ABERTO → FECHADO', 'Chamado #6988 editado',
     [('Título', 'Compra - Água', 'Compra IGOR'), ('Status', 'Aberto', 'Fechado')]),
]
for descricao, titulo, esperado in casos:
    tit, muds = historico.diferencas_do_texto(descricao)
    t(f'lê: {descricao[:40]}', tit == titulo and [(m['campo'], m['antes'], m['depois']) for m in muds] == esperado, (tit, muds))
for descricao in ['Chamado criado: #5484 - Pagamento de fotos', 'João realizou login no sistema',
                  'Link de recuperação enviado para a@b.c', 'Comunicado criado: de A para B',
                  'Usuário editado: X (x@y.z) — 1 campo(s) alterado(s)', '']:
    t(f'não inventa diferença: {descricao[:30]!r}', historico.diferencas_do_texto(descricao)[1] == [])

limpar_tudo()
inicio = timezone.now()
UserChangeLog.objects.create(target=comum, changed_by=chefe, field='job_title', field_label='Cargo',
                             old_value='Vendedor', new_value='Gerente')
UserChangeLog.objects.create(target=ex, changed_by=chefe, field='job_title', field_label='Cargo',
                             old_value='NAO-E-ESTA-PESSOA', new_value='NAO-E-ESTA-PESSOA-2')
UserChangeLog.objects.create(target=comum, changed_by=raiz, field='phone', field_label='Telefone',
                             old_value='OUTRO-AUTOR', new_value='OUTRO-AUTOR-2')
ed = novo_log(chefe, 'USER_UPDATE', f'Usuário editado: Ana Comum ({comum.email}) — 1 campo(s) alterado(s)')
velha = novo_log(chefe, 'USER_UPDATE', f'Usuário editado: Ana Comum ({comum.email}) — 1 campo(s) alterado(s)',
                 quando=inicio - dt.timedelta(hours=3))
novo_log(chefe, 'TICKET_UPDATE', 'Status do chamado #7 alterado: ABERTO → FECHADO')
novo_log(chefe, 'TICKET_CREATE', 'Chamado criado: <script>alert(1)</script>')
h = texto(cliente(chefe).get(URL))
t('edição de cadastro mostra campo, antes e depois',
  'Cargo' in h and '>Vendedor</del>' in h and '>Gerente</ins>' in h)
t('não mistura edição de outra pessoa', 'NAO-E-ESTA-PESSOA' not in h)
t('não mistura edição de outro autor', 'OUTRO-AUTOR' not in h)
t('edição sem linha de cadastro não ganha diferença', historico.diferencas_de_cadastro([velha]) == {})
t('status do chamado aparece legível', '>Aberto</del>' in h and '>Fechado</ins>' in h)
t('texto do log vai escapado', '<script>alert(1)</script>' not in h and '&lt;script&gt;' in h)
t('nota diz que o log não guarda antes/depois', 'sem campos de antes e depois' in h)
t('sem vazar comentário de template', '{#' not in h and '{%' not in h)
novo_log(chefe, 'TICKET_PRIORITY_CHANGE', 'Prioridade do chamado #9 alterada para Crítica')
t('sem antes gravado, diz que não registrou', 'antes não registrado' in texto(cliente(chefe).get(URL)))

print('7) Catálogo, ícones e módulo')
for codigo, _rotulo in SystemLog.ACTION_TYPES:
    t(f'rótulo para {codigo}', codigo in historico.ROTULOS)
limpar_tudo()
for codigo in historico.ROTULOS:
    novo_log(None, codigo, f'd {codigo}')
novo_log(None, 'CODIGO_NOVO_XYZ', 'desconhecido')
dados = historico.dados_da_acao('CODIGO_NOVO_XYZ')
t('código novo não quebra', dados['rotulo'] == 'Codigo novo xyz' and dados['modulo'] == 'outros')
t('login é verde', historico.dados_da_acao('USER_LOGIN')['cor'] == 'verde')
t('exclusão é vermelha', historico.dados_da_acao('TICKET_DELETE')['cor'] == 'vermelho')
t('edição é âmbar', historico.dados_da_acao('TICKET_EDIT')['cor'] == 'ambar')
t('login é de Acesso, não de Usuários', historico.modulo_de('USER_LOGIN')[0] == 'acesso')
t('senha de usuário é de Acesso', historico.modulo_de('USER_PASSWORD_CHANGE')[0] == 'acesso')
for chave, _r in [(c_, r_) for c_, r_, _p in historico.MODULOS] + [historico.OUTROS]:
    no_banco = set(SystemLog.objects.filter(historico.filtro_de_modulo(chave)).values_list('action_type', flat=True))
    na_regra = {a for a in SystemLog.objects.values_list('action_type', flat=True) if historico.modulo_de(a)[0] == chave}
    t(f'filtro e regra iguais em {chave}', no_banco == na_regra and no_banco, (no_banco ^ na_regra))

print('8) Filtros e paginação')
g = cliente(chefe).get      # entrar grava USER_LOGIN: o cliente vem antes dos registros
limpar_tudo()
ontem = agora - dt.timedelta(days=1)
a1 = novo_log(comum, 'USER_LOGIN', 'entrou', quando=ontem)
a2 = novo_log(chefe, 'TICKET_CREATE', 'Chamado criado: #1 - impressora', quando=agora - dt.timedelta(days=5))
a3 = novo_log(ex, 'SECTOR_EDIT', 'Setor editado: a → b', quando=agora - dt.timedelta(days=40))


def ids_de(resposta):
    return {log.id for d in resposta.context['dias'] for b in d['blocos'] for log in b['logs']}


t('sem filtro traz tudo', ids_de(g(URL)) == {a1.id, a2.id, a3.id})
t('filtra por pessoa', ids_de(g(URL, {'user': comum.id})) == {a1.id})
t('filtra por ação', ids_de(g(URL, {'action_type': 'TICKET_CREATE'})) == {a2.id})
t('filtra por módulo', ids_de(g(URL, {'modulo': 'setores'})) == {a3.id})
t('módulo Acesso pega login', ids_de(g(URL, {'modulo': 'acesso'})) == {a1.id})
t('módulo Usuários não pega login', ids_de(g(URL, {'modulo': 'usuarios'})) == set())
d40 = timezone.localtime(a3.created_at).date()
t('filtra por período, com o último dia inteiro',
  ids_de(g(URL, {'date_from': d40.isoformat(), 'date_to': d40.isoformat()})) == {a3.id})
t('filtra por texto', ids_de(g(URL, {'q': 'impressora'})) == {a2.id})
t('filtra por nome', ids_de(g(URL, {'q': 'Comum'})) == {a1.id})
t('pessoa inválida não derruba', g(URL, {'user': 'abc'}).status_code == 200)
t('data inválida não derruba', g(URL, {'date_from': '99/99'}).status_code == 200)
t('módulo inválido não derruba', g(URL, {'modulo': 'zzz'}).status_code == 200)
r = g(URL, {'user': ex.id})
t('ex-colaborador continua no seletor', 'Ex Colaborador (inativo)' in texto(r))
novo_csv = g(URL_CSV, {'modulo': 'chamados'})
linhas = list(csv.reader(io.StringIO(novo_csv.content.decode('utf-8-sig'))))
t('CSV usa os mesmos filtros', len(linhas) == 2 and 'impressora' in linhas[1][4], linhas)
t('CSV traz módulo e rótulo', linhas[1][2] == 'Chamados' and linhas[1][3] == 'Chamado criado')

limpar_tudo()
SystemLog.objects.bulk_create([SystemLog(user=comum if i % 2 else chefe, action_type='TICKET_COMMENT', description=f'c{i}')
                               for i in range(120)])
r = g(URL, {'action_type': 'TICKET_COMMENT', 'page': 2})
t('50 por página', len(ids_de(r)) == 50 and r.context['page_obj'].paginator.num_pages == 3)
t('paginação mantém filtro sem repetir page',
  'page=3&amp;action_type=TICKET_COMMENT' in texto(r) and 'page=2&amp;page' not in texto(r))
t('página fora da faixa não derruba', g(URL, {'page': 9999}).status_code == 200)

print('9) Linha do tempo agrupada')
limpar_tudo()
hoje_12 = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
for m in range(4):
    novo_log(comum, 'USER_LOGIN', f'login {m}', quando=hoje_12 - dt.timedelta(days=1, minutes=m))
novo_log(chefe, 'TICKET_UPDATE', 'Status do chamado #1 alterado: ABERTO → FECHADO', quando=hoje_12 - dt.timedelta(days=2))
novo_log(chefe, 'TICKET_UPDATE', 'Status do chamado #2 alterado: ABERTO → FECHADO', quando=hoje_12 - dt.timedelta(days=2, minutes=1))
novo_log(chefe, 'TICKET_UPDATE', 'Status do chamado #3 alterado: ABERTO → FECHADO', quando=hoje_12 - dt.timedelta(days=2, minutes=2))
r = g(URL)
dias = r.context['dias']
t('agrupa por dia', len(dias) == 2 and dias[0]['rotulo'] == 'Ontem', [d['rotulo'] for d in dias])
t('repetição seguida vira um bloco', dias[0]['blocos'][0]['agrupado'] and dias[0]['blocos'][0]['quantos'] == 4)
t('bloco mostra × quantidade', 'Entrada no portal × 4' in texto(r))
t('registro com mudança nunca fica recolhido', len(dias[1]['blocos']) == 3 and not any(b['agrupado'] for b in dias[1]['blocos']))

print('10) Consultas não crescem com a página')
limpar_tudo()
novo_log(comum, 'USER_LOGIN', 'a', quando=agora - dt.timedelta(minutes=1))
c2 = cliente(chefe)
c2.get(URL)
with CaptureQueriesContext(connection) as poucos:
    c2.get(URL)
SystemLog.objects.bulk_create([SystemLog(user=comum if i % 2 else chefe, action_type='TICKET_COMMENT', description=f'c{i}')
                               for i in range(60)])
with CaptureQueriesContext(connection) as muitos:
    c2.get(URL)
t('sem consulta por linha', len(muitos) <= len(poucos) + 3, (len(poucos), len(muitos)))

print('11) Posição perto do momento')
limpar_tudo()
base = timezone.now().replace(microsecond=0) - dt.timedelta(days=3)


def posicao(user, quando, lat=-20.1, lon=-40.1, origem='APP'):
    return PosicaoRegistrada.objects.create(usuario=user, latitude=lat, longitude=lon, momento=quando, origem=origem)


posicao(comum, base - dt.timedelta(minutes=20), lat=-20.20)
posicao(comum, base + dt.timedelta(minutes=5), lat=-20.25)
posicao(chefe, base, lat=-99.0)
achou = posicoes_perto([('x', comum.id, base)])['x']
t('escolhe a mais próxima', achou['latitude'] == -20.25 and achou['minutos'] == 5 and not achou['antes'], achou)
t('fora da janela não conta', posicoes_perto([('x', comum.id, base + dt.timedelta(hours=2))]) == {})
t('sem pedidos não consulta', posicoes_perto([]) == {})
RegistroPontoPortal.objects.create(usuario=ex, employee_id=1, momento=base, latitude=-21.0, longitude=-41.0)
RegistroPontoPortal.objects.create(usuario=ex, employee_id=1, momento=base + dt.timedelta(minutes=1),
                                   latitude=-22.0, longitude=-42.0, sucesso=False)
RegistroPontoPortal.objects.create(usuario=ex, employee_id=1, momento=base + dt.timedelta(minutes=1))
achou = posicoes_perto([('p', ex.id, base + dt.timedelta(minutes=1))])['p']
t('usa o ponto e ignora batida com erro ou sem local', achou['latitude'] == -21.0 and achou['origem'] == 'PONTO', achou)
t('rótulo da origem', achou['origem_rotulo'] == 'marcação de ponto')
t('última posição', ultima_posicao(comum.id)['origem'] == 'APP' and ultima_posicao(raiz.id) is None)
t('no formato do mapa', posicao_no_momento(comum, base)[0]['id'] == comum.id)

print('12) Histórico → mapa, respeitando a permissão')
log_perto = novo_log(comum, 'USER_LOGIN', 'perto', quando=base)
log_longe = novo_log(comum, 'USER_LOGIN', 'longe', quando=base + dt.timedelta(hours=5))
log_sem = novo_log(None, 'WEBHOOK_TRIGGER', 'sem pessoa', quando=base)
c3 = cliente(chefe)
r = c3.get(URL)
h = texto(r)
em = timezone.localtime(base).strftime('%Y-%m-%dT%H:%M:%S')
link = f'{URL_MAPA}?usuario={comum.id}&amp;em={em}'
t('registro com posição ganha o atalho', link in h, em)
t('a URL não leva coordenada', 'lat=' not in h and 'lon=' not in h)
t('mostra de onde veio a posição', 'localização ao vivo' in h and 'Posição 5 min depois' in h)
t('mostra a loja', 'Loja Centro' in h)
por_id = {l.id: l for d_ in r.context['dias'] for b in d_['blocos'] for l in b['logs']}
t('longe do momento não ganha posição', por_id[log_longe.id].posicao is None)
t('sem pessoa não ganha posição', por_id[log_sem.id].posicao is None)
r = c3.get(URL, {'user': comum.id})
t('filtro por pessoa mostra a última posição', 'ahUltimaPosicao' in texto(r))
t('sem posição diz isso', 'Sem posição registrada' in texto(c3.get(URL, {'user': raiz.id})))

with mock.patch('maps.permissions.pode_ver_mapa', return_value=False), \
        mock.patch('maps.servicos.posicoes_perto', side_effect=AssertionError('consultou posição')) as nao_chama, \
        mock.patch('maps.servicos.ultima_posicao', side_effect=AssertionError('consultou posição')):
    r = c3.get(URL)
    h = texto(r)
    r_p = c3.get(URL, {'user': comum.id})
    hp = texto(r_p)
t('sem mapa: abre o histórico', r.status_code == 200 and r_p.status_code == 200)
t('sem mapa: nada de atalho', URL_MAPA not in h.split('id="ahTotal"')[1] and 'data-mapa' not in h)
t('sem mapa: nada de posição', 'min depois' not in h and 'min antes' not in h and 'localização ao vivo' not in h
  and '-20,25' not in h and '-20.25' not in h)
t('sem mapa: nem a nota sobre posição', 'não de onde a ação foi feita' not in h)
t('sem mapa: filtro por pessoa não revela posição', 'ahUltimaPosicao' not in hp and 'Sem posição' not in hp)
t('sem mapa: a loja do cadastro continua', 'Loja Centro' in h)
linhas = list(csv.reader(io.StringIO(c3.get(URL_CSV).content.decode('utf-8-sig'))))
t('CSV não leva posição', all('-20.25' not in ' '.join(l) for l in linhas) and 'Latitude' not in linhas[0])

print('13) Mapa → histórico e o momento no mapa')
r = c3.get(URL_MAPA, {'usuario': comum.id, 'em': em})
h = texto(r)
t('abre o mapa nesse momento', r.status_code == 200 and r.context['pessoa_momento'] == comum)
t('só aquela pessoa, na posição mais próxima',
  len(r.context['posicoes_json']) == 1 and r.context['posicoes_json'][0]['lat'] == -20.25, r.context['posicoes_json'])
t('avisa que não é agora', 'Não é onde a pessoa está agora' in h)
t('tela não fica se atualizando', 'var MODO_MOMENTO = true' in h)
t('volta para o histórico do dia', f'user={comum.id}&amp;date_from=' in h)
hora_local = timezone.localtime(base + dt.timedelta(minutes=5)).strftime('%d/%m/%Y %H:%M')
t('o balão mostra a hora local, não UTC', r.context['posicoes_json'][0]['momento'] == hora_local,
  (r.context['posicoes_json'][0]['momento'], hora_local))
api = c3.get(URL_API, {'usuario': comum.id, 'em': em}).json()
t('API segue o mesmo recorte', api['total'] == 1 and api['posicoes'][0]['lat'] == -20.25, api)

r = c3.get(URL_MAPA, {'usuario': ex.id, 'em': timezone.localtime(base).strftime('%Y-%m-%dT%H:%M:%S')})
t('ex-colaborador aparece no momento', r.context['total'] == 1)
r = c3.get(URL_MAPA, {'usuario': comum.id, 'em': timezone.localtime(base + dt.timedelta(hours=9)).strftime('%Y-%m-%dT%H:%M:%S')})
t('sem posição perto diz isso', r.context['total'] == 0 and 'Nenhuma posição registrada até 30 min' in texto(r))
r = c3.get(URL_MAPA, {'usuario': comum.id, 'em': 'lixo'})
t('data inválida cai no mapa normal', r.status_code == 200 and r.context['pessoa_momento'] is None and 'var MODO_MOMENTO = false' in texto(r))
r = c3.get(URL_MAPA, {'usuario': 999999, 'em': em})
t('pessoa inexistente cai no mapa normal', r.status_code == 200 and r.context['pessoa_momento'] is None)

t('comum não vê o mapa de ninguém', cliente(comum).get(URL_MAPA, {'usuario': chefe.id, 'em': em}).status_code == 302)
t('comum não usa a API com momento', cliente(comum).get(URL_API, {'usuario': chefe.id, 'em': em}).status_code == 403)

posicao(comum, timezone.now() - dt.timedelta(hours=1), lat=-20.5)
r = c3.get(URL_MAPA)
h = texto(r)
t('mapa normal segue sem momento', r.context['pessoa_momento'] is None and 'var MODO_MOMENTO = false' in h)
t('atalho para o histórico da pessoa', f'{URL}?user={comum.id}' in h and f"var URL_HISTORICO = '" in h)
with mock.patch('core.historico.pode_ver_historico', return_value=False):
    h = texto(c3.get(URL_MAPA))
    h2 = texto(c3.get(URL_MAPA, {'usuario': comum.id, 'em': em}))
t('sem histórico: nada de atalho no mapa', f'{URL}?user=' not in h and "var URL_HISTORICO = ''" in h)
t('sem histórico: nem no modo momento', 'Histórico desse dia' not in h2)

print('14) Coleta de posição continua como estava')
ConfiguracaoMapa.objects.all().delete()
r = cliente(comum).post(reverse('maps:api_minha_posicao'), {'lat': '-20', 'lon': '-40'})
t('coleta desligada continua recusando', r.status_code == 409)
ConfiguracaoMapa.objects.create(coleta_ativa=True)
r = cliente(comum).post(reverse('maps:api_minha_posicao'), {'lat': '-20', 'lon': '-40'})
t('coleta ligada continua gravando', r.status_code == 200)
t('o histórico não mexeu na configuração', ConfiguracaoMapa.carregar().coleta_ativa is True)

print('15) "Nunca entrou" não depende do log que a limpeza apaga')
limpar_tudo()
User.objects.filter(pk=comum.pk).update(last_login=timezone.now() - dt.timedelta(days=500))
User.objects.filter(pk=outro_chefe.pk).update(last_login=None)
User.objects.filter(pk=chefe.pk).update(last_login=timezone.now())
User.objects.filter(pk=raiz.pk).update(last_login=timezone.now())
r = cliente(chefe).get(URL)
t('quem entrou há mais de 1 ano não vira "nunca entrou"', comum not in list(r.context['nunca_lista']))
t('quem nunca entrou aparece', outro_chefe in list(r.context['nunca_lista']))
t('quem sumiu aparece com os dias', r.context['inativos_top'][0]['pessoa'] == comum and r.context['inativos_top'][0]['dias'] >= 499)

print(f'\n{ok} OK, {falhas} falha(s)')
sys.exit(1 if falhas else 0)
