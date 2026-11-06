"""Solicitações ao DP (/ponto/solicitacoes/) e documentos do colaborador por categoria (06/11/2026).

Ajuste de ponto, atestado e declaração: envio, aviso ao DP, análise, devolução para correção
(com o que mudou no histórico), aprovação, lançamento do ajuste e o atestado aprovado indo para
os documentos do colaborador. Nos documentos: permissão por categoria, arquivo só com permissão
e a seção categorizada na edição do cadastro.

Roda num sqlite descartável (schema direto dos models); nada sai para a rede.
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-dp-'))
(PASTA / 'zz_dp_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dp'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dp-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_dp_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
os.environ.setdefault('RC_SEM_TAREFAS_DIARIAS', '1')

django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import datetime as dt

import requests
from django.contrib.auth import get_user_model
from django.utils import timezone

from notifications.models import NotificationPreference, UserNotification
from solicitacoes_dp.models import Evento, ResponsavelDP, Solicitacao
from solicitacoes_dp.permissoes import destinatarios, e_analista
from tangerino.models import MarcacaoPonto
from users.models import PermissaoCategoriaDocumento, Sector, UserDocument

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


for _n in ('get', 'post', 'request'):
    mock.patch.object(requests, _n, sem_rede).start()

loja = Sector.objects.create(name='Loja Centro')
dp_setor = Sector.objects.create(name='Departamento Pessoal ')


def usuario(nome, setor=loja, hierarquia='PADRAO'):
    u = User.objects.create_user(username=f'zzd.{nome}', email=f'zzd.{nome}@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZD', last_name=nome.capitalize(), sector=setor, hierarchy=hierarquia)
    c = Client()
    c.force_login(u)
    return u, c


ana, ca = usuario('ana')
bruno, cb = usuario('bruno')
dora, cd = usuario('dora', dp_setor, 'ADMINISTRATIVO')         # DP
gil, cg = usuario('gil', loja, 'SUPERVISOR')                    # gerencia cadastros, não é DP
chefe, cs = usuario('chefe', loja, 'SUPERADMIN')
hoje = timezone.localdate()
ontem = hoje - dt.timedelta(days=1)
PDF = b'%PDF-1.4 atestado de teste'


def avisos(u):
    return list(UserNotification.objects.filter(user=u).select_related('notification').order_by('id'))


print('Acesso e menu')
h = ca.get('/ponto/solicitacoes/').content.decode()
t('todo colaborador abre e vê no menu', 'Solicitações ao DP' in h and 'href="/ponto/solicitacoes/nova/ajuste/"' in h)
t('colaborador não vê a caixa do DP', '/ponto/dp/' not in h and ca.get('/ponto/dp/').status_code == 302)
t('o DP: RH/DP (setor) e SUPERADMIN analisam; gerente comum não', e_analista(dora) and e_analista(chefe) and not e_analista(gil) and not e_analista(ana))
t('o aviso vai para o DP (não para o SUPERADMIN, havendo DP)', destinatarios() == [dora])
t('DP vê a aba da caixa', 'Caixa do DP' in cd.get('/ponto/solicitacoes/').content.decode())

print('Ajuste de ponto')
URL_AJ = '/ponto/solicitacoes/nova/ajuste/'
base_aj = {'data': ontem.isoformat(), 'motivo_ajuste': 'ESQUECEU', 'hora_0': '08:00', 'hora_1': '12:00', 'hora_2': '13:00',
           'hora_3': '17:30', 'justificativa': 'Esqueci de bater a volta do almoço porque fui atender um cliente.'}
for nome_caso, mudanca, msg in (
        ('sem horários', {'hora_0': '', 'hora_1': '', 'hora_2': '', 'hora_3': ''}, 'horários certos'),
        ('dia no futuro', {'data': (hoje + dt.timedelta(days=2)).isoformat()}, 'já passou'),
        ('fora de ordem', {'hora_1': '07:00'}, 'em ordem'),
        ('horário repetido', {'hora_2': '12:00'}, 'repetidos'),
        ('justificativa curta', {'justificativa': 'esqueci'}, 'justificativa'),
        ('sem motivo', {'motivo_ajuste': ''}, 'motivo do ajuste'),
        ('muito antigo', {'data': (hoje - dt.timedelta(days=200)).isoformat()}, 'últimos 90 dias')):
    r = ca.post(URL_AJ, {**base_aj, **mudanca})
    t(f'{nome_caso}: recusa', r.status_code == 200 and msg in r.content.decode() and not Solicitacao.objects.exists(), nome_caso)
r = ca.post(URL_AJ, base_aj)
aj = Solicitacao.objects.get()
t('envia e abre a solicitação', r.status_code == 302 and r['Location'] == f'/ponto/solicitacoes/{aj.pk}/' and aj.status == 'ENVIADA'
  and aj.marcacoes == [{'rotulo': 'Entrada', 'hora': '08:00'}, {'rotulo': 'Saída para o intervalo', 'hora': '12:00'},
                       {'rotulo': 'Volta do intervalo', 'hora': '13:00'}, {'rotulo': 'Saída', 'hora': '17:30'}])
t('histórico: enviou', list(aj.eventos.values_list('acao', flat=True)) == ['CRIADA'])
av = avisos(dora)
t('o DP é avisado no sino, com quem, o que e o link', len(av) == 1 and 'Nova solicitação' in av[0].notification.title
  and 'Zzd Ana' in av[0].notification.message.title() and av[0].notification.action_url == f'/ponto/solicitacoes/{aj.pk}/')
t('quem pediu e o SUPERADMIN não recebem esse aviso', not avisos(ana) and not avisos(chefe))
t('mesmo dia em aberto não duplica', 'já tem uma solicitação de ajuste' in ca.post(URL_AJ, base_aj).content.decode())
MarcacaoPonto.objects.create(employee_id=1, usuario=ana, data=ontem, entrada1=timezone.make_aware(dt.datetime.combine(ontem, dt.time(8, 1))),
                             saida1=timezone.make_aware(dt.datetime.combine(ontem, dt.time(12, 0))),
                             entrada2=timezone.make_aware(dt.datetime.combine(ontem, dt.time(17, 31))), sincronizado_em=timezone.now())
j = ca.get('/ponto/solicitacoes/marcacoes-do-dia/', {'data': ontem.isoformat()}).json()
t('o colaborador vê o que o relógio registrou no dia', j['horas'] == ['08:01', '12:00', '17:31'], j)
t('dia sem espelho: avisa que não tem', ca.get('/ponto/solicitacoes/marcacoes-do-dia/', {'data': hoje.isoformat()}).json()['horas'] is None)
t('só o próprio ponto', cb.get('/ponto/solicitacoes/marcacoes-do-dia/', {'data': ontem.isoformat()}).json()['horas'] is None)
h = cd.get(f'/ponto/solicitacoes/{aj.pk}/').content.decode()
t('o DP compara relógio x pedido', 'O relógio registrou' in h and '08:01 · 12:00 · 17:31' in h and 'Horários pedidos' in h and '17:30' in h)
t('outro colaborador não vê', cb.get(f'/ponto/solicitacoes/{aj.pk}/').status_code == 404 and cg.get(f'/ponto/solicitacoes/{aj.pk}/').status_code == 404)

print('Análise do DP')
r = cb.post(f'/ponto/solicitacoes/{aj.pk}/acao/', {'acao': 'aprovar'})
t('quem não é DP não aprova', cb.post(f'/ponto/solicitacoes/{aj.pk}/acao/', {'acao': 'aprovar'}).status_code == 404)
ca.post(f'/ponto/solicitacoes/{aj.pk}/acao/', {'acao': 'aprovar'})
aj.refresh_from_db()
t('nem o próprio colaborador', aj.status == 'ENVIADA')
cd.post(f'/ponto/solicitacoes/{aj.pk}/acao/', {'acao': 'assumir'})
aj.refresh_from_db()
t('o DP assume: em análise, com o responsável, e o colaborador é avisado', aj.status == 'EM_ANALISE' and aj.responsavel == dora
  and 'em análise' in avisos(ana)[-1].notification.title)
r = cd.post(f'/ponto/solicitacoes/{aj.pk}/acao/', {'acao': 'devolver', 'texto': 'oi'}, follow=True)
aj.refresh_from_db()
t('devolver exige dizer o que corrigir', aj.status == 'EM_ANALISE' and 'o que corrigir' in r.content.decode())
cd.post(f'/ponto/solicitacoes/{aj.pk}/acao/', {'acao': 'devolver', 'texto': 'A saída foi às 18:00, confira o horário.'})
aj.refresh_from_db()
t('devolvida para correção, com o recado', aj.status == 'DEVOLVIDA' and aj.resposta.startswith('A saída')
  and 'devolveu' in avisos(ana)[-1].notification.title and 'A saída foi às 18:00' in avisos(ana)[-1].notification.message)
h = ca.get('/ponto/solicitacoes/').content.decode()
t('a lista do colaborador destaca a devolvida', 'devolvida' in h and 'DP: A saída foi às 18:00' in h)
t('DP não aprova enquanto está com o colaborador', 'não está aguardando' in cd.post(f'/ponto/solicitacoes/{aj.pk}/acao/', {'acao': 'aprovar'}, follow=True).content.decode())
antes = len(avisos(dora))
r = ca.post(f'/ponto/solicitacoes/{aj.pk}/corrigir/', {**base_aj, 'hora_3': '18:00', 'comentario': 'Corrigi a saída',
                                                        'anexos': SimpleUploadedFile('print.png', b'\x89PNG fake', 'image/png')})
aj.refresh_from_db()
ev = aj.eventos.last()
t('corrige e reenvia: volta para o DP', r.status_code == 302 and aj.status == 'ENVIADA' and aj.marcacoes[-1]['hora'] == '18:00')
t('o histórico guarda o que mudou', ev.acao == 'REENVIADA' and ev.texto == 'Corrigi a saída'
  and any(a['campo'] == 'Horários' and '17:30' in a['antes'] and '18:00' in a['depois'] for a in ev.alteracoes)
  and any(a['campo'] == 'Arquivo novo' and a['depois'] == 'print.png' for a in ev.alteracoes), ev.alteracoes)
t('quem está com a análise é avisado da correção', len(avisos(dora)) == antes + 1 and 'reenviada' in avisos(dora)[-1].notification.title)
h = cd.get(f'/ponto/solicitacoes/{aj.pk}/').content.decode()
t('a tela mostra o antes e o depois', 'line-through' in h and 'Corrigiu e reenviou' in h.replace('corrigiu e reenviou', 'Corrigiu e reenviou'))
cd.post(f'/ponto/solicitacoes/{aj.pk}/acao/', {'acao': 'aprovar', 'texto': 'Ok, vou lançar.'})
aj.refresh_from_db()
t('aprovado; o ajuste ainda falta lançar', aj.status == 'APROVADA' and not aj.ajustado_no_ponto and aj.aprovada_em
  and 'aprovada' in avisos(ana)[-1].notification.title.lower())
h = cd.get('/ponto/dp/', {'filtro': 'lancar'}).content.decode()
t('a caixa mostra os ajustes a lançar', f'#{aj.pk}' in h and 'falta lançar' in h)
cd.post(f'/ponto/solicitacoes/{aj.pk}/acao/', {'acao': 'lancar'})
aj.refresh_from_db()
t('marcar como lançado no ponto', aj.ajustado_no_ponto and aj.ajustado_em and list(aj.eventos.values_list('acao', flat=True))[-1] == 'AJUSTADA')
t('não dá para cancelar o que já foi aprovado', 'não foi analisada' in ca.post(f'/ponto/solicitacoes/{aj.pk}/acao/', {'acao': 'cancelar'}, follow=True).content.decode())

print('Atestado')
URL_AT = '/ponto/solicitacoes/nova/atestado/'
base_at = {'especie': 'MEDICO', 'data': hoje.isoformat(), 'data_fim': (hoje + dt.timedelta(days=2)).isoformat(),
           'justificativa': 'Consulta e repouso por três dias.'}
t('sem o documento: recusa', 'Anexe o documento' in ca.post(URL_AT, base_at).content.decode())
t('formato estranho: recusa', 'envie PDF ou foto' in ca.post(URL_AT, {**base_at, 'anexos': SimpleUploadedFile('virus.exe', b'MZ', 'application/octet-stream')}).content.decode())
t('data final antes da inicial: recusa', 'antes da inicial' in ca.post(URL_AT, {**base_at, 'data_fim': ontem.isoformat(),
                                                                                'anexos': SimpleUploadedFile('a.pdf', PDF, 'application/pdf')}).content.decode())
t('só uma das horas: recusa', 'duas horas' in ca.post(URL_AT, {**base_at, 'hora_inicio': '08:00', 'anexos': SimpleUploadedFile('a.pdf', PDF, 'application/pdf')}).content.decode())
r = ca.post(URL_AT, {**base_at, 'anexos': SimpleUploadedFile('atestado ana.pdf', PDF, 'application/pdf')})
at = Solicitacao.objects.get(tipo='ATESTADO')
t('atestado enviado', at.status == 'ENVIADA' and at.dias == 3 and at.anexos.count() == 1 and 'Atestado médico' in at.titulo)
anexo = at.anexos.get()
r = ca.get(f'/ponto/solicitacoes/{at.pk}/anexo/{anexo.pk}/')
t('o arquivo abre para quem pediu', r.status_code == 200 and b''.join(r.streaming_content) == PDF)
t('e para o DP; outros não', cd.get(f'/ponto/solicitacoes/{at.pk}/anexo/{anexo.pk}/').status_code == 200
  and cb.get(f'/ponto/solicitacoes/{at.pk}/anexo/{anexo.pk}/').status_code == 404 and cg.get(f'/ponto/solicitacoes/{at.pk}/anexo/{anexo.pk}/').status_code == 404)
t('nome do arquivo impossível de adivinhar', anexo.arquivo.name.startswith('dp/solicitacoes/') and 'atestado' not in anexo.arquivo.name)
cd.post(f'/ponto/solicitacoes/{at.pk}/acao/', {'acao': 'aprovar', 'arquivar': '1'})
at.refresh_from_db()
doc = at.documentos.first()
t('aprovado: vai para os documentos do colaborador, em Atestados', at.status == 'APROVADA' and doc and doc.user == ana
  and doc.categoria == 'ATESTADO' and doc.file.read() == PDF and doc.original_filename == 'atestado ana.pdf' and doc.uploaded_by == dora)
t('histórico conta que guardou', '1 arquivo(s) guardado(s)' in at.eventos.get(acao='APROVADA').texto)

print('Declaração, cancelar e mensagens')
r = ca.post('/ponto/solicitacoes/nova/declaracao/', {'especie': 'COMPARECIMENTO', 'data': hoje.isoformat(), 'hora_inicio': '08:00', 'hora_fim': '10:30',
                                                     'justificativa': 'Exame de sangue no laboratório.', 'anexos': SimpleUploadedFile('d.jpg', b'\xff\xd8\xff', 'image/jpeg')})
de = Solicitacao.objects.get(tipo='DECLARACAO')
t('declaração por horas', de.periodo.endswith('das 08:00 às 10:30'))
ca.post(f'/ponto/solicitacoes/{de.pk}/acao/', {'acao': 'comentar', 'texto': 'Esqueci de mandar o verso, mando amanhã.'})
t('mensagem do colaborador avisa o DP', 'Comentário do colaborador' in avisos(dora)[-1].notification.title)
cd.post(f'/ponto/solicitacoes/{de.pk}/acao/', {'acao': 'comentar', 'texto': 'Sem problema.'})
t('mensagem do DP avisa o colaborador', 'comentou' in avisos(ana)[-1].notification.title)
ca.post(f'/ponto/solicitacoes/{de.pk}/acao/', {'acao': 'cancelar'})
de.refresh_from_db()
t('o colaborador cancela antes da análise', de.status == 'CANCELADA' and de.eventos.last().acao == 'CANCELADA')
t('outro colaborador não comenta', cb.post(f'/ponto/solicitacoes/{at.pk}/acao/', {'acao': 'comentar', 'texto': 'oi oi'}).status_code == 404)

print('Caixa do DP')
ca.post(URL_AJ, {**base_aj, 'data': (hoje - dt.timedelta(days=3)).isoformat()})
velha = Solicitacao.objects.filter(tipo='AJUSTE', status='ENVIADA').get()
Solicitacao.objects.filter(pk=velha.pk).update(enviada_em=timezone.now() - dt.timedelta(days=4))
r = cd.get('/ponto/dp/')
h = r.content.decode()
k = r.context['kpis']
t('KPIs da caixa', k['aguardando'] == 1 and k['lancar'] == 0 and k['aprovadas_mes'] == 2 and k['atrasadas'] == 1, k)
t('pendente há dias aparece em vermelho', 'há mais de 2 dias' in h and 'text-red-600 font-semibold' in h)
link = lambda pk: f'href="/ponto/solicitacoes/{pk}/"'
t('busca por nome e por número', link(velha.pk) in cd.get('/ponto/dp/', {'filtro': 'todas', 'q': 'ana'}).content.decode()
  and link(velha.pk) not in cd.get('/ponto/dp/', {'filtro': 'todas', 'q': 'bruno'}).content.decode()
  and link(at.pk) in (hq := cd.get('/ponto/dp/', {'filtro': 'todas', 'q': f'#{at.pk}'}).content.decode()) and link(aj.pk) not in hq)
t('filtro por tipo', link(at.pk) in cd.get('/ponto/dp/', {'filtro': 'todas', 'tipo': 'ATESTADO'}).content.decode()
  and link(aj.pk) not in cd.get('/ponto/dp/', {'filtro': 'todas', 'tipo': 'ATESTADO'}).content.decode())
t('contador no menu do DP', 'title="Esperando o DP">1<' in h)

print('Responsáveis do DP')
t('só o SUPERADMIN escolhe', cd.get('/ponto/dp/responsaveis/').status_code == 302)
cs.post('/ponto/dp/responsaveis/', {'acao': 'adicionar', 'user': bruno.pk})
bruno = User.objects.get(pk=bruno.pk)
t('responsável escolhido analisa e passa a receber os avisos', e_analista(bruno) and destinatarios() == [bruno])
NotificationPreference.objects.create(user=bruno, in_app_enabled=False)
antes_b, antes_d = len(avisos(bruno)), len(avisos(dora))
ca.post('/ponto/solicitacoes/nova/declaracao/', {'especie': 'ESCOLAR', 'data': hoje.isoformat(), 'justificativa': 'Prova na faculdade à noite.',
                                                 'anexos': SimpleUploadedFile('p.pdf', PDF, 'application/pdf')})
t('quem desligou os avisos do portal não recebe; o DP fora da lista também não', len(avisos(bruno)) == antes_b and len(avisos(dora)) == antes_d)
r = ResponsavelDP.objects.get(user=bruno)
cs.post('/ponto/dp/responsaveis/', {'acao': 'remover', 'id': r.pk})
t('tirando todos, volta a valer o RH/DP', destinatarios() == [dora])

print('Documentos do colaborador por categoria')
pessoal = UserDocument.objects.create(user=ana, document_name='RG', file=SimpleUploadedFile('rg.pdf', b'%PDF rg'), original_filename='rg.pdf', categoria='PESSOAL')
h = cg.get(f'/users/manage/users/{ana.pk}/profile/').content.decode()
t('gerente vê as categorias, menos Atestados', 'Documentos Pessoais' in h and 'Declarações' in h and 'id="doc-atestado"' not in h
  and '1 documento(s) em categorias com acesso restrito' in h)
h = cd.get(f'/users/manage/users/{ana.pk}/profile/').content.decode()
t('o DP vê os atestados (o aprovado entrou sozinho)', 'id="doc-atestado"' in h and 'Atestado médico' in h)
t('o arquivo abre pelo portal, só com permissão', cg.get(f'/users/manage/user-documents/{pessoal.pk}/arquivo/').status_code == 200
  and cg.get(f'/users/manage/user-documents/{doc.pk}/arquivo/').status_code == 404
  and cd.get(f'/users/manage/user-documents/{doc.pk}/arquivo/').status_code == 200
  and cb.get(f'/users/manage/user-documents/{pessoal.pk}/arquivo/').status_code == 404
  and ca.get(f'/users/manage/user-documents/{doc.pk}/arquivo/').status_code == 200)
t('o link do arquivo não é mais o do bucket', f'/users/manage/user-documents/{pessoal.pk}/arquivo/' in cg.get(f'/users/manage/users/{ana.pk}/profile/').content.decode())
r = cg.post(f'/users/manage/user-documents/{pessoal.pk}/categoria/', {'categoria': 'ATESTADO'})
t('gerente não joga documento em categoria que não vê', r.status_code == 403)
r = cg.post(f'/users/manage/users/{ana.pk}/documents/upload/', {'categoria': 'ATESTADO', 'file': SimpleUploadedFile('x.pdf', b'%PDF')})
t('nem envia para ela', UserDocument.objects.filter(user=ana, original_filename='x.pdf').count() == 0)
t('excluir documento de categoria que não vê: não', cg.post(f'/users/manage/user-documents/{doc.pk}/delete/').status_code == 302
  and UserDocument.objects.filter(pk=doc.pk).exists())
he = cs.get(f'/users/manage/users/{ana.pk}/edit/').content.decode()
t('a edição do cadastro tem a seção por categoria', 'id="documentos"' in he and 'id="doc-pessoal"' in he and 'id="doc-admissional"' in he
  and f'name="voltar" value="/users/manage/users/{ana.pk}/edit/"' in he)
r = cs.post(f'/users/manage/users/{ana.pk}/documents/upload/', {'categoria': 'ADMISSIONAL', 'voltar': f'/users/manage/users/{ana.pk}/edit/',
                                                                 'file': SimpleUploadedFile('contrato.pdf', b'%PDF c')})
t('enviar pela edição volta para a edição', r['Location'] == f'/users/manage/users/{ana.pk}/edit/#documentos'
  and UserDocument.objects.get(original_filename='contrato.pdf').categoria == 'ADMISSIONAL')
r = cs.post(f'/users/manage/users/{ana.pk}/documents/upload/', {'voltar': 'https://site-de-fora.exemplo/', 'file': SimpleUploadedFile('c2.pdf', b'%PDF')})
t('e não redireciona para fora', r['Location'] == f'/users/manage/users/{ana.pk}/profile/')

print('Acesso por categoria (SUPERADMIN)')
t('só o SUPERADMIN configura', cg.get('/users/manage/documentos/permissoes/').status_code == 302 and cs.get('/users/manage/documentos/permissoes/').status_code == 200)
form = {f'acesso_{c}': a for c, a in (('PESSOAL', 'SUPERADMIN'), ('ADMISSIONAL', 'GESTORES'), ('ATESTADO', 'RH_DP'),
                                       ('DECLARACAO', 'GESTORES'), ('OUTROS', 'GESTORES'))}
cs.post('/users/manage/documentos/permissoes/', form)
cg = Client(); cg.force_login(gil)
h = cg.get(f'/users/manage/users/{ana.pk}/profile/').content.decode()
t('Pessoais só para o SUPERADMIN: o gerente deixa de ver', 'id="doc-pessoal"' not in h and cg.get(f'/users/manage/user-documents/{pessoal.pk}/arquivo/').status_code == 404)
cs.post('/users/manage/documentos/permissoes/', {**form, 'usuarios_PESSOAL': [gil.pk]})
cg = Client(); cg.force_login(gil)
t('liberando a pessoa a mais, ela volta a ver', 'id="doc-pessoal"' in cg.get(f'/users/manage/users/{ana.pk}/profile/').content.decode())
regra = PermissaoCategoriaDocumento.objects.get(categoria='PESSOAL')
t('a regra guarda quem mudou', regra.acesso == 'SUPERADMIN' and list(regra.usuarios.all()) == [gil] and regra.atualizado_por == chefe)
import zipfile, io as _io
r = cd.get(f'/users/manage/users/{ana.pk}/documents/download-all/')
nomes = zipfile.ZipFile(_io.BytesIO(b''.join(r.streaming_content) if r.streaming else r.content)).namelist()
t('baixar todos respeita a categoria (DP não vê Pessoais agora)', not any('RG' in n for n in nomes) and any('Atestado' in n for n in nomes), nomes)

print(f'\n{ok} OK, {fail} falha(s)')
