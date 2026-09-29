"""CONECTAR: entrega com vários itens, atribuição simples e painel de conferência.

Pedido (28/09/2026), em três frentes:

- quem avalia (gestores e SUPERADMINs) vê, de um jeito simples e dinâmico,
  quem fez e como fez;
- quem atribui só seleciona as pessoas — e elas devem entregar;
- quem faz sobe o que for preciso: link, documento, vídeo, vários e de
  formatos diferentes.

O que este teste cobre:

- a entrega aceita vários arquivos de formatos diferentes e link, num envio só;
- o nome que aparece é o do arquivo, não o uuid com que ele é guardado;
- arquivo grande demais é recusado com o tamanho na mensagem;
- item só sai pela mão de quem entregou, e nunca depois de aprovado;
- o certificado do campo antigo continua aparecendo junto das entregas novas;
- "exige entrega" não deixa concluir sem nada anexado;
- anexar depois de uma recusa devolve a entrega para a fila;
- o formulário de atribuir virou uma lista de pessoas por loja, com busca;
- o painel de conferência é dos gestores, mostra o que cada um anexou, decide
  sem recarregar a página, cobra motivo na recusa e mostra quem está devendo;
- ninguém confere a própria entrega.

Roda num sqlite descartável (a tabela das entregas ainda não existe no
Postgres compartilhado). Nada sai para o MinIO nem para o banco de verdade.
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-conectar-'))
(PASTA / 'zz_conectar_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-con'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-con-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_conectar_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

from communications.models import CommunicationGroup
from core.models import Notification
from impulso import views
from impulso.models import ConclusaoConteudo, ConteudoConectar, EntregaConteudo
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


def arquivo(nome, tamanho=64, tipo='application/pdf'):
    return SimpleUploadedFile(nome, b'z' * tamanho, content_type=tipo)


def fetch(cliente, url, dados=None, arquivos=None):
    """POST como a tela faz: por fetch, esperando JSON."""
    carga = dict(dados or {})
    carga.update(arquivos or {})
    r = cliente.post(url, carga, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {}


# ── cenário ────────────────────────────────────────────────────────────────
loja = Sector.objects.create(name='ZZ Loja do Conectar')
outra = Sector.objects.create(name='ZZ Outra Loja')

ana = User.objects.create_user(username='zzc.ana', email='zzc.ana@exemplo-teste.local',
                               password='S3nha!teste', first_name='ZZC', last_name='Ana',
                               sector=loja)
bruno = User.objects.create_user(username='zzc.bruno', email='zzc.bruno@exemplo-teste.local',
                                 password='S3nha!teste', first_name='ZZC', last_name='Bruno',
                                 sector=outra)
gestor = User.objects.create_user(username='zzc.gestor', email='zzc.gestor@exemplo-teste.local',
                                  password='S3nha!teste', first_name='ZZC', last_name='Gestor',
                                  sector=loja, is_superuser=True, hierarchy='SUPERADMIN')
# Os grupos precisam de quem os criou (o gestor entra primeiro).
adm = CommunicationGroup.objects.create(name="ADM's LOJAS", created_by=gestor)
gestores = CommunicationGroup.objects.create(name='GESTORES (IMPULSO)', created_by=gestor)
for pessoa in (ana, bruno, gestor):
    pessoa.communication_groups.add(adm)
gestor.communication_groups.add(gestores)

# Gestor do Impulso sem ser superusuário: é quem confere a loja dele.
chefia = User.objects.create_user(username='zzc.chefia', email='zzc.chefia@exemplo-teste.local',
                                  password='S3nha!teste', first_name='ZZC', last_name='Chefia',
                                  sector=loja)
chefia.communication_groups.add(adm, gestores)

curso = ConteudoConectar.objects.create(
    tipo=ConteudoConectar.Tipo.CURSO, titulo='ZZ Curso de Atendimento',
    criado_por=gestor, exige_entrega=True)
curso.obrigatorio_para.set([ana, bruno])
pop = ConteudoConectar.objects.create(
    tipo=ConteudoConectar.Tipo.POP, titulo='ZZ POP da Vitrine', criado_por=gestor)

c_ana = Client(); c_ana.force_login(ana)
c_bruno = Client(); c_bruno.force_login(bruno)
c_gestor = Client(); c_gestor.force_login(gestor)
c_chefia = Client(); c_chefia.force_login(chefia)

print('== QUEM FAZ: SUBIR O QUE FOR PRECISO ==')
status, dado = fetch(c_ana, f'/impulso/conectar/{curso.id}/entrega/', {
    'arquivos': [arquivo('certificado.pdf'), arquivo('foto-do-quadro.jpg', tipo='image/jpeg'),
                 arquivo('atendimento.mp4', tipo='video/mp4')],
})
t('vários arquivos num envio só', status == 200 and dado.get('ok') and len(dado['itens']) == 3,
  (status, dado))
t('de formatos diferentes, cada um com sua cara',
  [i['especie'] for i in dado.get('itens', [])] == ['documento', 'imagem', 'video'],
  [i.get('especie') for i in dado.get('itens', [])])
t('e o nome que aparece é o do arquivo, não o uuid',
  [i['nome'] for i in dado.get('itens', [])][0] == 'certificado.pdf',
  [i.get('nome') for i in dado.get('itens', [])])

status, dado = fetch(c_ana, f'/impulso/conectar/{curso.id}/entrega/',
                     {'url': 'https://exemplo.local/planilha', 'titulo': 'Planilha do mês'})
t('link também é entrega', status == 200 and dado['itens'][0]['especie'] == 'link', dado)
t('com a descrição que a pessoa deu', dado['itens'][0]['nome'] == 'Planilha do mês', dado)

conclusao = ConclusaoConteudo.objects.get(conteudo=curso, user=ana)
t('os quatro itens ficam na mesma entrega', conclusao.entregas.count() == 4,
  conclusao.entregas.count())

status, dado = fetch(c_ana, f'/impulso/conectar/{curso.id}/entrega/', {})
t('envio sem nada avisa', status == 400 and 'arquivo ou informe um link' in dado.get('error', ''),
  dado)

gigante = SimpleUploadedFile('video-gigante.mp4', b'z' * 10, content_type='video/mp4')
gigante.size = 300 * 1024 ** 2
recado = views._erro_da_entrega(gigante)
t('arquivo grande demais é recusado', recado and '300 MB' in recado, recado)
t('e a mensagem diz qual é o limite', '100 MB' in (recado or ''), recado)
t('arquivo vazio também não passa',
  'vazio' in (views._erro_da_entrega(SimpleUploadedFile('nada.pdf', b'')) or ''))
with mock.patch.object(views, 'ENTREGA_MAX_BYTES', 32):
    status, dado = fetch(c_ana, f'/impulso/conectar/{curso.id}/entrega/',
                         {'arquivos': [arquivo('pesado.pdf', 64)]})
t('e a tela recusa antes de guardar', status == 400 and 'limite' in dado.get('error', ''), dado)
t('nada foi guardado do que não passou',
  not EntregaConteudo.objects.filter(titulo='pesado.pdf').exists())

primeiro = conclusao.entregas.first()
status, dado = fetch(c_bruno, f'/impulso/conectar/entrega/{primeiro.id}/remover/')
t('ninguém mexe na entrega do outro', status == 403 and not dado.get('ok'), (status, dado))
status, dado = fetch(c_ana, f'/impulso/conectar/entrega/{primeiro.id}/remover/')
t('quem entregou remove o próprio item', status == 200 and dado.get('ok'), (status, dado))
t('e o item some de verdade', conclusao.entregas.count() == 3, conclusao.entregas.count())

print('\n== CONCLUIR: ENTREGA COBRADA NO SERVIDOR ==')
c_bruno.post(f'/impulso/conectar/{curso.id}/concluir/', follow=True)
conclusao_bruno = ConclusaoConteudo.objects.filter(conteudo=curso, user=bruno).first()
t('conteúdo que exige entrega não fecha vazio',
  conclusao_bruno is not None and not conclusao_bruno.concluido)

r = c_bruno.post(f'/impulso/conectar/{curso.id}/concluir/',
                 {'arquivos': [arquivo('relatorio.pdf')]}, follow=True)
conclusao_bruno.refresh_from_db()
t('com anexo no mesmo envio, fecha', conclusao_bruno.concluido)
t('e fica aguardando conferência',
  conclusao_bruno.aprovacao == ConclusaoConteudo.Aprovacao.PENDENTE)
t('o anexo virou entrega', conclusao_bruno.entregas.count() == 1)

c_ana.post(f'/impulso/conectar/{curso.id}/concluir/', follow=True)
conclusao.refresh_from_db()
t('quem já tinha anexado fecha sem mandar nada de novo', conclusao.concluido)

print('\n== O CERTIFICADO ANTIGO NÃO SOME ==')
antiga = ConclusaoConteudo.objects.create(
    conteudo=pop, user=bruno, concluido=True,
    certificado=SimpleUploadedFile('antigo.pdf', b'z' * 16, content_type='application/pdf'))
itens = antiga.itens_entregues()
t('o certificado do campo antigo vira item da entrega',
  len(itens) == 1 and itens[0].especie == 'documento', [(i.nome, i.especie) for i in itens])
t('com um nome que se entende', itens[0].nome == 'Certificado', itens[0].nome)
t('e ninguém apaga o legado pela tela', itens[0].pode_remover(bruno) is False)
EntregaConteudo.objects.create(conclusao=antiga, url='https://exemplo.local/extra')
t('convive com as entregas novas', len(antiga.itens_entregues()) == 2)

print('\n== QUEM ATRIBUI ==')
html = c_gestor.get('/impulso/conectar/novo/').content.decode()
t('o formulário lista as pessoas para marcar', 'name="obrigatorio_para"' in html
  and 'type="checkbox"' in html)
t('sem o select de segurar Ctrl', 'Segure Ctrl' not in html)
t('com busca', 'data-atr-busca' in html)
t('e agrupadas por loja', 'ZZ Loja do Conectar' in html and 'marcar a loja' in html)
t('dá para cobrar entrega', 'name="exige_entrega"' in html)

r = c_gestor.post('/impulso/conectar/novo/', {
    'grupo': ConteudoConectar.GRUPO_POP_VIDEO, 'titulo': 'ZZ POP Novo',
    'obrigatorio': 'on', 'exige_entrega': 'on',
    'obrigatorio_para': [str(ana.id), str(bruno.id)],
}, follow=True)
novo = ConteudoConectar.objects.filter(titulo='ZZ POP Novo').first()
t('o conteúdo nasce com quem deve entregar',
  novo is not None and set(novo.obrigatorio_para.values_list('id', flat=True)) == {ana.id, bruno.id})
t('e com a entrega cobrada', novo.exige_entrega)

r = c_gestor.post(f'/impulso/conectar/{novo.id}/editar/', {
    'grupo': ConteudoConectar.GRUPO_POP_VIDEO, 'titulo': 'ZZ POP Novo',
    'obrigatorio': 'on', 'obrigatorio_para': [str(bruno.id)],
}, follow=True)
novo.refresh_from_db()
t('editar troca quem deve entregar',
  list(novo.obrigatorio_para.values_list('id', flat=True)) == [bruno.id],
  list(novo.obrigatorio_para.values_list('id', flat=True)))
t('e desmarcar a cobrança vale', novo.exige_entrega is False)

t('o aviso de exclusão conta o que foi entregue',
  curso.impacto_da_exclusao['certificados'] >= 3, curso.impacto_da_exclusao)

print('\n== O COLABORADOR SOBE PARA ELE MESMO ==')
Notification.objects.all().delete()
r = c_ana.post('/impulso/conectar/novo/', {
    'titulo': 'ZZ POP que a Ana fez',
    'descricao': 'ZZ passo a passo da vitrine',
    'url': 'https://exemplo.local/pasta-da-ana',
    'arquivos': [arquivo('pop-da-ana.pdf'), arquivo('vitrine.mp4', tipo='video/mp4'),
                 arquivo('foto-antes.jpg', tipo='image/jpeg')],
}, follow=True)
subido = ConteudoConectar.objects.filter(titulo='ZZ POP que a Ana fez').first()
t('o envio do colaborador cria o conteúdo', subido is not None, r.status_code)
t('marcado como enviado pela equipe', subido.criado_por_equipe and subido.criado_por_id == ana.id)
t('já atrelado a ela mesma',
  list(subido.obrigatorio_para.values_list('id', flat=True)) == [ana.id],
  list(subido.obrigatorio_para.values_list('id', flat=True)))
t('o documento e o vídeo viram o material do conteúdo',
  subido.documento is not None and subido.arquivo_de_video is not None,
  (bool(subido.documento), bool(subido.arquivo_de_video)))

entrega_ana = ConclusaoConteudo.objects.filter(conteudo=subido, user=ana).first()
t('e a entrega já nasce feita', entrega_ana is not None and entrega_ana.concluido)
t('esperando o gestor conferir',
  entrega_ana.aprovacao == ConclusaoConteudo.Aprovacao.PENDENTE)
t('o arquivo que sobrou dos dois campos vira item da entrega',
  entrega_ana.entregas.count() == 1
  and entrega_ana.entregas.first().titulo == 'foto-antes.jpg',
  [e.titulo for e in entrega_ana.entregas.all()])

itens = entrega_ana.itens_entregues()
nomes = [i.nome for i in itens]
t('o que ela subiu aparece como a entrega dela',
  'Documento enviado' in nomes and 'Vídeo enviado' in nomes
  and 'Link informado' in nomes, nomes)
t('junto do item extra', 'foto-antes.jpg' in nomes, nomes)
t('e conta como entrega para o "exige entrega"', entrega_ana.tem_entrega)

avisos = list(Notification.objects.values_list('user_id', 'title'))
t('o gestor do setor é avisado',
  any(u == chefia.id and 'conferência' in (titulo or '').lower() for u, titulo in avisos),
  avisos)

painel = c_gestor.get('/impulso/conectar/conferir/').content.decode()
t('a entrega aparece no painel de conferência', 'ZZ POP que a Ana fez' in painel)
t('com o que ela subiu à mão', 'Documento enviado' in painel)
status, dado = fetch(c_gestor, f'/impulso/conectar/conclusao/{entrega_ana.id}/decidir/',
                     {'decisao': 'aprovar'})
entrega_ana.refresh_from_db()
t('e o gestor só precisa aprovar', status == 200 and entrega_ana.aprovada)

# O gestor publicando material da rede continua sem virar entrega dele.
c_gestor.post('/impulso/conectar/novo/', {
    'grupo': ConteudoConectar.GRUPO_CURSO, 'titulo': 'ZZ Curso publicado pelo gestor',
    'obrigatorio': 'on',
}, follow=True)
publicado = ConteudoConectar.objects.filter(titulo='ZZ Curso publicado pelo gestor').first()
t('o que o gestor publica não vira entrega dele',
  publicado is not None
  and not ConclusaoConteudo.objects.filter(conteudo=publicado, user=gestor).exists())

print('\n== QUEM AVALIA ==')
r = c_ana.get('/impulso/conectar/conferir/')
t('quem não confere não entra', r.status_code == 302, r.status_code)

r = c_gestor.get('/impulso/conectar/conferir/')
t('o gestor abre o painel', r.status_code == 200, r.status_code)
painel = r.content.decode()
t('vê quem fez', 'ZZC Ana' in painel and 'ZZC Bruno' in painel)
t('e como fez, item por item', 'foto-do-quadro.jpg' in painel and 'atendimento.mp4' in painel
  and 'Planilha do mês' in painel)
t('com a loja de cada um', 'ZZ Loja do Conectar' in painel)
t('a fila separa o que falta conferir', 'Aguardando você' in painel and 'data-aba="fila"' in painel)
t('dá para filtrar por conteúdo e loja', 'cfr-conteudo' in painel and 'cfr-loja' in painel)
t('e a prévia abre na própria tela', 'cfr-modal' in painel)

status, dado = fetch(c_gestor, f'/impulso/conectar/conclusao/{conclusao.id}/decidir/',
                     {'decisao': 'recusar'})
t('recusa sem motivo não passa', status == 400 and 'motivo' in dado.get('error', ''), dado)
conclusao.refresh_from_db()
t('e nada foi gravado', conclusao.aprovacao == ConclusaoConteudo.Aprovacao.PENDENTE)

status, dado = fetch(c_gestor, f'/impulso/conectar/conclusao/{conclusao.id}/decidir/',
                     {'decisao': 'recusar', 'observacao': 'ZZ o certificado está ilegível'})
conclusao.refresh_from_db()
t('recusa com motivo vale', status == 200 and dado.get('ok')
  and conclusao.aprovacao == ConclusaoConteudo.Aprovacao.RECUSADA, (status, dado))
t('a tela recebe o novo estado sem recarregar',
  dado.get('aprovacao') == 'RECUSADA' and dado.get('decidida_por') and dado.get('decidida_em'),
  dado)

status, dado = fetch(c_ana, f'/impulso/conectar/{curso.id}/entrega/',
                     {'arquivos': [arquivo('certificado-legivel.pdf')]})
conclusao.refresh_from_db()
t('corrigir devolve a entrega para a fila',
  status == 200 and conclusao.aprovacao == ConclusaoConteudo.Aprovacao.PENDENTE, dado)

status, dado = fetch(c_gestor, f'/impulso/conectar/conclusao/{conclusao.id}/decidir/',
                     {'decisao': 'aprovar'})
conclusao.refresh_from_db()
t('aprovar fecha a conta', status == 200 and conclusao.aprovada and conclusao.vale_ponto)

status, dado = fetch(c_ana, f'/impulso/conectar/entrega/{conclusao.entregas.first().id}/remover/')
t('depois de aprovada, a entrega não muda mais', status == 403, (status, dado))
status, dado = fetch(c_ana, f'/impulso/conectar/{curso.id}/entrega/',
                     {'arquivos': [arquivo('outro.pdf')]})
t('nem recebe item novo', status == 400 and 'já foi aprovada' in dado.get('error', ''), dado)

propria = ConclusaoConteudo.objects.create(conteudo=pop, user=chefia, concluido=True)
status, dado = fetch(c_chefia, f'/impulso/conectar/conclusao/{propria.id}/decidir/',
                     {'decisao': 'aprovar'})
t('gestor não confere a própria entrega', status == 403, (status, dado))
propria.refresh_from_db()
t('e ela continua na fila', propria.aprovacao == ConclusaoConteudo.Aprovacao.PENDENTE)

painel = c_gestor.get('/impulso/conectar/conferir/').content.decode()
t('o painel mostra quem ainda não entregou', 'Ainda não entregaram' in painel)
t('e a cobertura de cada conteúdo', 'ZZ Curso de Atendimento' in painel
  and 'data-aba="cobertura"' in painel)

print('\n== A TELA DE QUEM FAZ ==')
detalhe = c_ana.get(f'/impulso/conectar/{curso.id}/').content.decode()
t('a entrega aparece inteira', 'Minha entrega' in detalhe
  and 'certificado-legivel.pdf' in detalhe and 'Planilha do mês' in detalhe)
t('com o estado da conferência', 'Aprovada' in detalhe)
t('e sem oferecer anexo novo depois de aprovada', 'id="entEnviar"' not in detalhe)

detalhe = c_bruno.get(f'/impulso/conectar/{curso.id}/').content.decode()
t('quem ainda espera continua podendo anexar', 'id="entEnviar"' in detalhe
  and 'Aguardando conferência' in detalhe)
t('aceita vários arquivos de uma vez', 'id="entArquivos" multiple' in detalhe)
t('e link', 'id="entUrl"' in detalhe)

print(f'\n{ok} OK / {fail} falhas')
print(f'(banco e arquivos descartáveis em {PASTA} — pode apagar)')
sys.exit(1 if fail else 0)
